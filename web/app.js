import { ApiClient } from "./api.js";
import { BUILTIN_TEMPLATES, getBuiltinTemplate } from "./coach/builtin-templates.js";
import { LocalCoachController, TUTOR_STATES } from "./coach/controller.js";
import { toLegacyStrokes } from "./coach/local-matcher.js";
import { clamp, distance, toXY } from "./coach/metrics.js";
import { CoachOverlay } from "./coach/overlay.js";
import {
  buildCoachPayload,
  chooseHigherConfidence,
  isMatchingCoachResponse,
  normalizeCoachResponse,
} from "./coach/server-refinement.js";

const api = new ApiClient();
const elements = {
  canvas: document.getElementById("cv"),
  coachCanvas: document.getElementById("coach-cv"),
  busyOverlay: document.getElementById("busy-overlay"),
  character: document.getElementById("chr"),
  result: document.getElementById("result"),
  coachFeedback: document.getElementById("coach-feedback"),
  coachIcon: document.getElementById("coach-icon"),
  coachText: document.getElementById("coach-text"),
  statusDot: document.getElementById("dot"),
  statusText: document.getElementById("stxt"),
  stageBadge: document.getElementById("stage-badge"),
  hint: document.getElementById("hint"),
  picker: document.getElementById("picker"),
  pickerGrid: document.getElementById("picker-grid"),
  pickerInfo: document.getElementById("picker-info"),
  pickerSearch: document.getElementById("picker-search"),
};

const inkContext = elements.canvas.getContext("2d");
const coachOverlay = new CoachOverlay(elements.coachCanvas);
const coach = new LocalCoachController({
  onStateChange(nextState) {
    document.body.dataset.tutorState = nextState;
  },
});

let canvasSize = 0;
let strokes = [];
let currentStroke = null;
let currentPointerId = null;
let strokeStartTimestamp = 0;
let template = null;
let templateCharacter = "";
let lastReport = null;
let penSeen = false;
let stage = 1;
let revealCount = 0;
let framePending = false;
let lastPartialAnalysisAt = Number.NEGATIVE_INFINITY;
let allCharacters = "";
let shownCharacters = 0;
const characterChunkSize = 400;

function identity(prefix) {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const sessionId = identity("session");
let attemptId = identity("attempt");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(tone, text) {
  elements.statusDot.className = `dot ${tone}`;
  elements.statusText.textContent = text;
}

function setCoachMessage(text, tone = "success", icon = "○") {
  elements.coachFeedback.dataset.tone = tone;
  elements.coachIcon.textContent = icon;
  elements.coachText.textContent = text;
}

function setStage(nextStage) {
  stage = nextStage;
  revealCount = 0;
  elements.stageBadge.textContent = (
    stage === 1 ? "1단계: 궤적 따라쓰기" : "2단계: 안 보고 쓰기"
  );
  elements.hint.hidden = stage !== 2;
}

function configureCanvases() {
  canvasSize = Math.min(window.innerWidth - 32, 520);
  const devicePixelRatio = window.devicePixelRatio || 1;
  elements.canvas.width = Math.round(canvasSize * devicePixelRatio);
  elements.canvas.height = Math.round(canvasSize * devicePixelRatio);
  elements.canvas.style.width = `${canvasSize}px`;
  elements.canvas.style.height = `${canvasSize}px`;
  inkContext.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  inkContext.lineCap = "round";
  inkContext.lineJoin = "round";
  coachOverlay.resize(canvasSize, devicePixelRatio);
  redrawInk();
}

function drawStroke(points, color, width, dash = []) {
  if (!points || points.length < 2) return;
  const [startX, startY] = toXY(points[0]);
  inkContext.strokeStyle = color;
  inkContext.lineWidth = width;
  inkContext.setLineDash(dash);
  inkContext.beginPath();
  inkContext.moveTo(startX * canvasSize, startY * canvasSize);
  for (const point of points.slice(1)) {
    const [x, y] = toXY(point);
    inkContext.lineTo(x * canvasSize, y * canvasSize);
  }
  inkContext.stroke();
  inkContext.setLineDash([]);
}

function qualityColor(quality) {
  if (quality > 0.7) return "#128a44";
  if (quality > 0.4) return "#9a6700";
  return "#b42318";
}

function redrawInk() {
  if (!canvasSize) return;
  inkContext.clearRect(0, 0, canvasSize, canvasSize);
  inkContext.strokeStyle = "#dedbd4";
  inkContext.lineWidth = 1;
  inkContext.setLineDash([6, 6]);
  inkContext.beginPath();
  inkContext.moveTo(canvasSize / 2, 0);
  inkContext.lineTo(canvasSize / 2, canvasSize);
  inkContext.moveTo(0, canvasSize / 2);
  inkContext.lineTo(canvasSize, canvasSize / 2);
  inkContext.stroke();
  inkContext.setLineDash([]);

  if (template) {
    const visibleCount = stage === 1 ? template.length : Math.min(revealCount, template.length);
    for (let index = 0; index < visibleCount; index += 1) {
      drawStroke(template[index], "#d8d3c8", 7);
    }
    for (let index = 0; index < visibleCount; index += 1) {
      const [x, y] = toXY(template[index][0]);
      inkContext.fillStyle = "#766f63";
      inkContext.font = "12px sans-serif";
      inkContext.fillText(String(index + 1), x * canvasSize + 4, y * canvasSize - 4);
    }
  }

  if (lastReport?.user) {
    for (let index = 0; index < lastReport.user.length; index += 1) {
      const entry = lastReport.strokes?.[index];
      drawStroke(lastReport.user[index], qualityColor(entry?.q ?? 0.5), 5);
    }
    if (lastReport.missing && template) {
      for (const missingIndex of lastReport.missing) {
        if (template[missingIndex]) {
          drawStroke(template[missingIndex], "#175cd3", 4, [8, 6]);
        }
      }
    }
    return;
  }

  for (const stroke of strokes) drawStroke(stroke, "#2b2925", 5);
  if (currentStroke) drawStroke(currentStroke, "#2b2925", 5);
}

function richPoint(event) {
  const rectangle = elements.canvas.getBoundingClientRect();
  return {
    x: clamp((event.clientX - rectangle.left) / rectangle.width),
    y: clamp((event.clientY - rectangle.top) / rectangle.height),
    t: Math.max(0, Number(event.timeStamp) - strokeStartTimestamp),
    pressure: Number.isFinite(event.pressure) ? event.pressure : undefined,
    tiltX: Number.isFinite(event.tiltX) ? event.tiltX : undefined,
    tiltY: Number.isFinite(event.tiltY) ? event.tiltY : undefined,
    pointerType: ["pen", "touch", "mouse"].includes(event.pointerType)
      ? event.pointerType
      : "mouse",
  };
}

function pushPoint(event) {
  const point = richPoint(event);
  const previous = currentStroke?.at(-1);
  if (!previous || distance([previous.x, previous.y], [point.x, point.y]) >= 0.0001) {
    currentStroke.push(point);
  }
}

function scheduleDrawingFrame() {
  if (framePending) return;
  framePending = true;
  requestAnimationFrame((now) => {
    framePending = false;
    redrawInk();
    if (!currentStroke || now - lastPartialAnalysisAt < 1000 / 30) return;
    lastPartialAnalysisAt = now;
    const warning = coach.analyzePartial(currentStroke, now);
    if (warning) coachOverlay.renderPartial(warning);
    else coachOverlay.clear();
  });
}

function renderDiagnosis(diagnosis) {
  coachOverlay.renderResult(diagnosis);
  if (!diagnosis) return;
  if (!diagnosis.primaryCue) {
    if (diagnosis.nextAction.type === "complete") {
      setCoachMessage("글자를 완성했어요. 원하면 최종 채점을 확인해 보세요.", "success", "✓");
    } else {
      setCoachMessage(
        `좋아요. ${diagnosis.nextAction.templateIndex + 1}번 획을 이어서 써 보세요.`,
        "success",
        "✓",
      );
    }
    return;
  }
  setCoachMessage(
    diagnosis.primaryCue.text,
    diagnosis.accepted ? "nudge" : "retry",
    diagnosis.accepted ? "→" : "!",
  );
}

function resetVisibleAttempt(message = "첫 획부터 천천히 써 보세요.") {
  coach.reset(template);
  attemptId = identity("attempt");
  strokes = [];
  currentStroke = null;
  currentPointerId = null;
  lastReport = null;
  elements.result.replaceChildren();
  elements.busyOverlay.style.display = "none";
  coachOverlay.clear();
  setCoachMessage(message, "success", "○");
  redrawInk();
}

function isTemplate(value) {
  return Array.isArray(value) && value.length > 0 && value.every(
    (stroke) => Array.isArray(stroke) && stroke.length > 0,
  );
}

function cachedTemplate(character) {
  try {
    const parsed = JSON.parse(localStorage.getItem(`lingo:template:v1:${character}`));
    return isTemplate(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function cacheTemplate(character, value) {
  try {
    localStorage.setItem(`lingo:template:v1:${character}`, JSON.stringify(value));
  } catch (error) {
    console.warn("template cache unavailable", error);
  }
}

async function loadTemplate({ forceNetwork = true } = {}) {
  const character = elements.character.value.trim();
  if (!character) return;
  const characterChanged = character !== templateCharacter;
  if (characterChanged) {
    templateCharacter = character;
    template = getBuiltinTemplate(character) || cachedTemplate(character);
    setStage(1);
    resetVisibleAttempt(
      template
        ? "로컬 가이드가 준비됐어요. 서버가 꺼져 있어도 획별 교정을 받을 수 있습니다."
        : "가이드를 불러오는 중입니다.",
    );
    coach.replaceTemplate(template);
  } else if (!template) {
    template = getBuiltinTemplate(character) || cachedTemplate(character);
    coach.replaceTemplate(template);
    redrawInk();
  }

  if (!forceNetwork) return;
  const token = coach.lifecycle.createRequest("template");
  try {
    const { status, body } = await api.request(
      "template",
      { char: character },
      { signal: token.controller.signal },
    );
    if (!coach.lifecycle.isCurrent(token)) return;
    if (status === 200 && isTemplate(body.strokes)) {
      template = body.strokes;
      cacheTemplate(character, template);
      coach.replaceTemplate(template);
      redrawInk();
      if (strokes.length === 0) {
        setCoachMessage("가이드가 준비됐어요. 첫 획부터 써 보세요.", "success", "○");
      }
    } else if (!template) {
      setCoachMessage(body.message || body.detail || "이 문자의 가이드를 불러오지 못했습니다.", "retry", "!");
    }
  } catch (error) {
    if (error.name !== "AbortError" && coach.lifecycle.isCurrent(token)) {
      if (template) {
        setCoachMessage("서버 연결 없이 저장된 가이드로 연습할 수 있습니다.", "nudge", "↯");
      } else {
        setCoachMessage("서버가 꺼져 있고 저장된 가이드가 없습니다.", "retry", "!");
      }
    }
  } finally {
    coach.lifecycle.finish(token);
  }
}

async function checkHealth() {
  try {
    const { body } = await api.request("health");
    if (body.deep_score_ready || (body.ok && body.model_loaded)) {
      setStatus("on", `최종 채점 서버 온라인 (${body.device || "ready"})`);
    } else if (body.coach_ready || body.ok) {
      setStatus("warm", "서버 준비 중 · 로컬 코치는 사용 가능");
    } else {
      setStatus("off", "최종 채점 서버 오프라인 · 로컬 코치는 사용 가능");
    }
  } catch {
    setStatus("off", "최종 채점 서버 오프라인 · 로컬 코치는 사용 가능");
  }
}

async function requestServerRefinement({
  localDiagnosis,
  acceptedStrokes,
  completedStroke,
  expectedTemplateIndex,
}) {
  const character = elements.character.value.trim();
  if (!character) return;
  coach.lifecycle.abortKind("coach");
  const token = coach.lifecycle.createRequest("coach");
  const timeout = setTimeout(() => token.controller.abort(), 2500);
  const payload = buildCoachPayload({
    token,
    sessionId,
    attemptId,
    char: character,
    mode: stage === 1 ? "trace" : "recall",
    acceptedStrokes,
    currentStroke: completedStroke,
    expectedTemplateIndex,
    localDiagnosis,
  });
  try {
    const { status, body } = await api.request(
      "coach",
      payload,
      { signal: token.controller.signal },
    );
    if (
      status !== 200
      || !coach.lifecycle.isCurrent(token)
      || !isMatchingCoachResponse(body, token)
    ) {
      return;
    }
    const serverDiagnosis = normalizeCoachResponse(body);
    const selected = chooseHigherConfidence(localDiagnosis, serverDiagnosis);
    if (
      selected === serverDiagnosis
      && coach.applyServerRefinement(token, localDiagnosis, serverDiagnosis)
    ) {
      renderDiagnosis(serverDiagnosis);
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      console.warn("server refinement unavailable", error);
    }
  } finally {
    clearTimeout(timeout);
    coach.lifecycle.finish(token);
  }
}

elements.canvas.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "pen") penSeen = true;
  if (penSeen && event.pointerType === "touch") return;
  if (!coach.beginStroke()) return;
  coach.lifecycle.abortKind("coach");
  if (coach.lifecycle.abortKind("score")) {
    elements.busyOverlay.style.display = "none";
  }
  event.preventDefault();
  lastReport = null;
  elements.result.replaceChildren();
  coachOverlay.clear();
  currentPointerId = event.pointerId;
  strokeStartTimestamp = Number(event.timeStamp);
  currentStroke = [];
  pushPoint(event);
  elements.canvas.setPointerCapture(event.pointerId);
  redrawInk();
});

elements.canvas.addEventListener("pointermove", (event) => {
  if (!currentStroke || event.pointerId !== currentPointerId) return;
  if (penSeen && event.pointerType === "touch") return;
  event.preventDefault();
  const events = event.getCoalescedEvents ? event.getCoalescedEvents() : [event];
  for (const coalescedEvent of events) pushPoint(coalescedEvent);
  scheduleDrawingFrame();
});

function endStroke(event) {
  if (!currentStroke || event.pointerId !== currentPointerId) return;
  if (penSeen && event.pointerType === "touch") return;
  event.preventDefault();
  if (event.type === "pointerup") pushPoint(event);
  const completed = currentStroke;
  const expectedTemplateIndex = coach.expectedTemplateIndex;
  const acceptedStrokes = strokes.filter(
    (_stroke, index) => coach.results[index]?.advancesPrefix,
  );
  currentStroke = null;
  currentPointerId = null;
  strokes.push(completed);
  const diagnosis = coach.finishStroke(completed);
  redrawInk();
  renderDiagnosis(diagnosis);
  if (diagnosis) {
    void requestServerRefinement({
      localDiagnosis: diagnosis,
      acceptedStrokes,
      completedStroke: completed,
      expectedTemplateIndex,
    });
  }
}

elements.canvas.addEventListener("pointerup", endStroke);
elements.canvas.addEventListener("pointercancel", endStroke);

document.getElementById("undo").addEventListener("click", () => {
  if (!strokes.length) return;
  strokes.pop();
  coach.undoLast();
  lastReport = null;
  elements.result.replaceChildren();
  elements.busyOverlay.style.display = "none";
  coachOverlay.clear();
  setCoachMessage("마지막 획을 지웠어요. 같은 획부터 다시 써 보세요.", "nudge", "↶");
  redrawInk();
});

document.getElementById("clear").addEventListener("click", () => resetVisibleAttempt());

for (const quick of document.getElementsByClassName("quick")) {
  quick.addEventListener("click", (event) => {
    elements.character.value = event.currentTarget.textContent;
    loadTemplate();
  });
}

document.getElementById("load").addEventListener("click", () => loadTemplate());
elements.character.addEventListener("change", () => loadTemplate());

elements.hint.addEventListener("click", () => {
  if (!template) return;
  if (revealCount < template.length) revealCount += 1;
  lastReport = null;
  elements.result.replaceChildren();
  redrawInk();
});

function renderCharacterChunk() {
  const end = Math.min(shownCharacters + characterChunkSize, allCharacters.length);
  const fragment = document.createDocumentFragment();
  for (let index = shownCharacters; index < end; index += 1) {
    const item = document.createElement("div");
    item.className = "pchar";
    item.textContent = allCharacters[index];
    fragment.appendChild(item);
  }
  elements.pickerGrid.appendChild(fragment);
  shownCharacters = end;
  elements.pickerInfo.textContent = `${shownCharacters} / ${allCharacters.length}자 (스크롤하면 더 표시)`;
}

function resetCharacterGrid() {
  elements.pickerGrid.replaceChildren();
  shownCharacters = 0;
  renderCharacterChunk();
}

document.getElementById("pick").addEventListener("click", async () => {
  elements.picker.style.display = "flex";
  elements.pickerSearch.focus();
  if (allCharacters) return;
  try {
    const { status, body } = await api.request("chars");
    allCharacters = status === 200 && body.chars
      ? body.chars
      : Object.keys(BUILTIN_TEMPLATES).join("");
  } catch {
    allCharacters = Object.keys(BUILTIN_TEMPLATES).join("");
  }
  resetCharacterGrid();
});

document.getElementById("picker-close").addEventListener("click", () => {
  elements.picker.style.display = "none";
});

elements.picker.addEventListener("click", (event) => {
  if (event.target === elements.picker) elements.picker.style.display = "none";
});

elements.pickerGrid.addEventListener("click", (event) => {
  if (!event.target.classList.contains("pchar")) return;
  elements.character.value = event.target.textContent;
  elements.picker.style.display = "none";
  loadTemplate();
});

elements.pickerGrid.addEventListener("scroll", () => {
  if (
    shownCharacters < allCharacters.length
    && elements.pickerGrid.scrollTop + elements.pickerGrid.clientHeight
      > elements.pickerGrid.scrollHeight - 200
  ) {
    renderCharacterChunk();
  }
});

elements.pickerSearch.addEventListener("input", () => {
  const value = elements.pickerSearch.value;
  for (const character of value) {
    if (allCharacters.includes(character)) {
      elements.pickerSearch.value = "";
      elements.character.value = character;
      elements.picker.style.display = "none";
      loadTemplate();
      break;
    }
  }
});

function renderScoreReport(body) {
  let html = `<div id="score-big">${escapeHtml(body.score)}점</div>`
    + `<div id="score-sub">모델 점수 ${escapeHtml(body.base_model_score)}`
    + ` · ${escapeHtml(body.elapsed)}초</div>`;
  const corrections = body.corrections || [];
  for (const correction of corrections) {
    const label = correction.index >= 0 ? `${correction.index + 1}번 획` : "누락";
    const gain = correction.gain != null && correction.gain > 0
      ? ` (+${Number(correction.gain).toFixed(1)}점 기대)`
      : "";
    const messages = (correction.messages || []).map(escapeHtml).join(" · ");
    html += `<div class="msg"><b>${escapeHtml(label + gain)}</b> — ${messages}</div>`;
  }
  if (corrections.length === 0) {
    html += '<div class="msg">교정할 획이 없습니다 — 잘 썼습니다!</div>';
  }
  if (stage === 1) {
    setStage(2);
    html += '<div class="msg"><b>2단계</b> — 이번엔 궤적 없이 기억해서 써 보세요. '
      + '막히면 <b>한 획 보기</b> 버튼으로 하나씩 확인할 수 있습니다.</div>';
  }
  elements.result.innerHTML = html;
}

document.getElementById("go").addEventListener("click", async () => {
  const character = elements.character.value.trim();
  if (!character) {
    setCoachMessage("연습할 문자를 먼저 입력하세요.", "retry", "!");
    return;
  }
  if (strokes.length === 0) {
    setCoachMessage("먼저 글자를 써 보세요.", "retry", "!");
    return;
  }

  coach.lifecycle.abortKind("coach");
  coach.lifecycle.abortKind("score");
  const token = coach.beginFinalScore();
  elements.busyOverlay.style.display = "flex";
  try {
    const { status, body } = await api.request(
      "score",
      { char: character, strokes: toLegacyStrokes(strokes) },
      { signal: token.controller.signal },
    );
    if (!coach.lifecycle.isCurrent(token)) return;
    elements.busyOverlay.style.display = "none";
    if (status !== 200) {
      coach.finishFinalScore(token, false);
      elements.result.innerHTML = `<div class="msg">${escapeHtml(
        body.message || body.detail || `오류: ${status}`,
      )}</div>`;
      setCoachMessage("최종 채점은 연결되지 않았지만 로컬 코치는 계속 동작합니다.", "nudge", "↯");
      return;
    }
    coach.finishFinalScore(token, true);
    lastReport = body;
    if (!template && isTemplate(body.template)) {
      template = body.template;
      coach.replaceTemplate(template);
    }
    coachOverlay.clear();
    redrawInk();
    renderScoreReport(body);
    setCoachMessage("최종 채점이 도착했습니다. 아래에서 교정 내용을 확인하세요.", "success", "✓");
  } catch (error) {
    if (error.name === "AbortError" || !coach.lifecycle.isCurrent(token)) return;
    elements.busyOverlay.style.display = "none";
    coach.finishFinalScore(token, false);
    elements.result.innerHTML = '<div class="msg">네트워크 오류 — 로컬 코치는 계속 사용할 수 있습니다.</div>';
    setCoachMessage("서버 없이도 획별 로컬 교정은 계속 받을 수 있습니다.", "nudge", "↯");
  }
});

window.addEventListener("resize", configureCanvases);

setStage(1);
configureCanvases();
loadTemplate();
checkHealth();
setInterval(checkHealth, 20_000);

// Exported only for browser smoke tests and diagnostics; it is not part of the score contract.
globalThis.__LINGO_TUTOR__ = {
  get state() { return coach.machine.state; },
  get attemptRevision() { return coach.lifecycle.revision; },
  get expectedTemplateIndex() { return coach.expectedTemplateIndex; },
  states: TUTOR_STATES,
};
