import { ApiClient } from "./api.js";
import {
  applyServerStrokeResult,
  buildAttemptPayload,
  createStrokeResult,
  markLastStrokeUndone,
  shouldAutoRetryStroke,
} from "./attempt-log.js";
import { CharacterPickerModel, splitCharacters } from "./character-picker.js";
import { BUILTIN_TEMPLATES, getBuiltinTemplate } from "./coach/builtin-templates.js";
import { LocalCoachController, TUTOR_STATES } from "./coach/controller.js";
import { toLegacyStrokes } from "./coach/local-matcher.js";
import { clamp, distance, toXY } from "./coach/metrics.js";
import { CoachOverlay } from "./coach/overlay.js";
import { bindStrokeEndEvents } from "./coach/pointer-boundary.js";
import { fitTemplateToWriting } from "./coach/template-fit.js";
import {
  buildCoachPayload,
  chooseHigherConfidence,
  isMatchingCoachResponse,
  normalizeCoachResponse,
} from "./coach/server-refinement.js";
import {
  isCurrentTeacherContext,
  localTeacherFallback,
  normalizeTeacherEnvelope,
  tryBuildTeacherFeedbackRequest,
} from "./coach/teacher-feedback.js";
import {
  buildLocalAttemptRecord,
  LocalAttemptStore,
} from "./local-attempt-store.js";

const TEACHER_REQUEST_TIMEOUT_MS = 28_000;

const api = new ApiClient();
const localAttemptStore = new LocalAttemptStore();
const elements = {
  canvas: document.getElementById("cv"),
  coachCanvas: document.getElementById("coach-cv"),
  busyOverlay: document.getElementById("busy-overlay"),
  character: document.getElementById("chr"),
  result: document.getElementById("result"),
  coachFeedback: document.getElementById("coach-feedback"),
  coachIcon: document.getElementById("coach-icon"),
  coachText: document.getElementById("coach-text"),
  teacherActions: document.getElementById("teacher-actions"),
  teacherExplain: document.getElementById("teacher-explain"),
  teacherRequestStatus: document.getElementById("teacher-request-status"),
  teacherFeedback: document.getElementById("teacher-feedback"),
  teacherSource: document.getElementById("teacher-source"),
  teacherPrimary: document.getElementById("teacher-primary"),
  teacherSecondary: document.getElementById("teacher-secondary"),
  statusDot: document.getElementById("dot"),
  statusText: document.getElementById("stxt"),
  stageBadge: document.getElementById("stage-badge"),
  hint: document.getElementById("hint"),
  picker: document.getElementById("picker"),
  pickerGrid: document.getElementById("picker-grid"),
  pickerInfo: document.getElementById("picker-info"),
  pickerSearch: document.getElementById("picker-search"),
  localHistoryCount: document.getElementById("local-history-count"),
  localHistoryList: document.getElementById("local-history-list"),
  localHistoryStatus: document.getElementById("local-history-status"),
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
let allCharacters = splitCharacters(Object.keys(BUILTIN_TEMPLATES));
let visibleCharacters = [...allCharacters];
let shownCharacters = 0;
let characterCatalogPromise = null;
let pickerComposing = false;
let teacherNetworkAvailable = false;
let currentTeacherContext = null;
let repeatedErrorCode = null;
let repeatedErrorCount = 0;
const characterChunkSize = 400;
const pickerModel = new CharacterPickerModel(allCharacters);
const readingIndexUrl = new URL("./data/kanji-readings.json", import.meta.url);

function identity(prefix) {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function currentCharacter() {
  const characters = splitCharacters(elements.character.value.trim());
  return characters.length === 1 ? characters[0] : null;
}

const sessionId = identity("session");
let attemptId = identity("attempt");
let attemptStartedAt = new Date().toISOString();
let attemptStrokeResults = [];
let attemptRecordedReason = null;
let localHistoryRefreshVersion = 0;
let localStoreWarningShown = false;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function localAttemptRecord({
  endedReason = null,
  endedAt = null,
  finalScore = null,
} = {}) {
  const character = templateCharacter || currentCharacter();
  if (
    !character
    || (strokes.length === 0 && attemptStrokeResults.length === 0)
  ) {
    return null;
  }
  const savedAt = endedAt || new Date().toISOString();
  return buildLocalAttemptRecord({
    sessionId,
    attemptId,
    attemptRevision: coach.lifecycle.revision,
    character,
    mode: stage === 1 ? "trace" : "recall",
    startedAt: attemptStartedAt,
    savedAt,
    strokes,
    strokeResults: attemptStrokeResults,
    endedReason,
    endedAt,
    finalScore,
  });
}

function formatLocalAttemptTime(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "저장 시간 확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function renderLocalHistory(count, records) {
  elements.localHistoryCount.textContent = String(count);
  elements.localHistoryList.replaceChildren();
  if (records.length === 0) {
    const empty = document.createElement("div");
    empty.className = "local-history-empty";
    empty.textContent = "아직 이 기기에 저장된 필기 시도가 없습니다.";
    elements.localHistoryList.appendChild(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const record of records) {
    const item = document.createElement("div");
    item.className = "local-history-item";
    const character = document.createElement("span");
    character.className = "local-history-char";
    character.textContent = record.char || "?";
    const details = document.createElement("span");
    details.className = "local-history-meta";
    const attempts = Array.isArray(record.stroke_results)
      ? record.stroke_results.length
      : record.strokes?.length || 0;
    const removed = Array.isArray(record.stroke_results)
      ? record.stroke_results.filter((result) => result.undone).length
      : 0;
    const score = Number.isFinite(record.final_score)
      ? ` · ${record.final_score}점`
      : "";
    const removedLabel = removed > 0 ? ` · 다시 쓴 획 ${removed}` : "";
    const state = record.status === "finished" ? "완료" : "작성 중";
    details.textContent = `${state} · 입력 ${attempts}획${removedLabel}${score}`
      + ` · ${formatLocalAttemptTime(record.updated_at)}`;
    item.append(character, details);
    fragment.appendChild(item);
  }
  elements.localHistoryList.appendChild(fragment);
}

async function refreshLocalHistory() {
  const refreshVersion = ++localHistoryRefreshVersion;
  if (!localAttemptStore.available) {
    elements.localHistoryCount.textContent = "-";
    elements.localHistoryStatus.textContent = "이 브라우저에서는 로컬 저장을 사용할 수 없습니다.";
    return;
  }
  try {
    const [count, records] = await Promise.all([
      localAttemptStore.count(),
      localAttemptStore.listRecent(8),
    ]);
    if (refreshVersion !== localHistoryRefreshVersion) return;
    renderLocalHistory(count, records);
    elements.localHistoryStatus.textContent = "획 좌표와 순서를 이 브라우저에 자동 저장합니다.";
  } catch (error) {
    if (refreshVersion !== localHistoryRefreshVersion) return;
    elements.localHistoryCount.textContent = "-";
    elements.localHistoryStatus.textContent = "로컬 필기 기록을 불러오지 못했습니다.";
    if (!localStoreWarningShown) {
      localStoreWarningShown = true;
      console.warn("local attempt history unavailable", error);
    }
  }
}

function saveLocalAttempt(record) {
  if (!record) return Promise.resolve(false);
  return localAttemptStore.save(record).then(() => {
    void refreshLocalHistory();
    return true;
  }).catch((error) => {
    elements.localHistoryStatus.textContent = "로컬 저장에 실패했습니다. 브라우저 저장 권한을 확인해 주세요.";
    if (!localStoreWarningShown) {
      localStoreWarningShown = true;
      console.warn("local attempt save unavailable", error);
    }
    return false;
  });
}

function saveCurrentAttemptLocally(options = {}) {
  return saveLocalAttempt(localAttemptRecord(options));
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

function hideTeacherAnswer() {
  elements.teacherFeedback.hidden = true;
  elements.teacherSource.textContent = "선생님 설명";
  elements.teacherPrimary.textContent = "";
  elements.teacherSecondary.textContent = "";
  elements.teacherSecondary.hidden = true;
}

function clearTeacherState({ clearContext = true, resetHistory = false } = {}) {
  coach.lifecycle.abortKind("verbalize");
  if (clearContext) currentTeacherContext = null;
  if (resetHistory) {
    repeatedErrorCode = null;
    repeatedErrorCount = 0;
  }
  elements.teacherActions.hidden = !currentTeacherContext;
  elements.teacherExplain.disabled = false;
  elements.teacherRequestStatus.textContent = "";
  hideTeacherAnswer();
}

function teacherSourceLabel(source) {
  if (source === "luna") return "GPT-5.6 Luna 선생님";
  if (source === "cache") return "검증된 AI 선생님 설명";
  return "기본 선생님 설명";
}

function renderTeacherAnswer(envelope) {
  const secondary = envelope.feedback.secondary_text || "";
  elements.teacherSource.textContent = teacherSourceLabel(envelope.source);
  elements.teacherPrimary.textContent = envelope.feedback.primary_text;
  elements.teacherSecondary.textContent = secondary;
  elements.teacherSecondary.hidden = !secondary;
  elements.teacherFeedback.hidden = false;
}

function diagnosisWithServerEvidence(body, diagnosis) {
  const structured = body?.teacher_evidence ?? body?.evidence ?? {};
  const confusion = body?.confusion ?? {};
  return {
    ...diagnosis,
    nearestCompetitor: body?.nearest_competitor ?? confusion.nearest_competitor ?? null,
    criticalStroke: body?.critical_stroke ?? confusion.critical_stroke ?? null,
    targetMargin: body?.target_margin ?? confusion.margin ?? null,
    evidenceCodes: body?.evidence_codes ?? confusion.evidence_codes ?? null,
    targetFeatureProfile: structured.target_feature_profile ?? null,
    observedFeatureProfile: structured.observed_feature_profile ?? null,
  };
}

function prepareTeacherExplanation(diagnosis, { countAttempt = true } = {}) {
  clearTeacherState({ clearContext: true });
  const errorCode = diagnosis?.primaryCue?.code;
  if (!errorCode) return;

  if (countAttempt) {
    repeatedErrorCount = errorCode === repeatedErrorCode
      ? repeatedErrorCount + 1
      : 1;
    repeatedErrorCode = errorCode;
  } else if (errorCode !== repeatedErrorCode) {
    repeatedErrorCode = errorCode;
    repeatedErrorCount = 1;
  }

  const request = tryBuildTeacherFeedbackRequest({
    decisionId: identity("decision"),
    diagnosis,
    targetChar: currentCharacter(),
    nearestCompetitor: diagnosis.nearestCompetitor,
    mode: stage === 1 ? "trace" : "recall",
    totalStrokes: template?.length ?? 1,
    attemptNumber: Math.max(1, strokes.length),
    sameErrorCount: repeatedErrorCount,
  });
  if (!request) return;
  currentTeacherContext = {
    request,
    fallbackText: diagnosis.primaryCue.text,
    allowNetwork: errorCode !== "UNCERTAIN_MATCH",
  };
  elements.teacherActions.hidden = false;
  elements.teacherExplain.disabled = false;
  elements.teacherRequestStatus.textContent = "";
}

function setStage(nextStage) {
  stage = nextStage;
  revealCount = 0;
  const mode = stage === 1 ? "trace" : "recall";
  coach.setMode(mode);
  document.body.dataset.practiceMode = mode;
  elements.stageBadge.textContent = (
    stage === 1 ? "1단계: 궤적 따라쓰기" : "2단계: 위치 자유 · 모양 쓰기"
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

function completedRecallWriting() {
  return Boolean(
    stage === 2
    && template?.length
    && strokes.length > 0
    && coach.expectedTemplateIndex >= template.length
    && !currentStroke
  );
}

function drawCompletedRecallReference(referenceTemplate) {
  for (const referenceStroke of referenceTemplate) {
    drawStroke(referenceStroke, "rgba(23, 92, 211, 0.72)", 3, [8, 6]);
  }
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

  const referenceTemplate = stage === 2 && template
    ? fitTemplateToWriting(template, strokes)
    : template;
  if (referenceTemplate) {
    const visibleCount = stage === 1 ? template.length : Math.min(revealCount, template.length);
    for (let index = 0; index < visibleCount; index += 1) {
      drawStroke(referenceTemplate[index], "#d8d3c8", 7);
    }
    for (let index = 0; index < visibleCount; index += 1) {
      const [x, y] = toXY(referenceTemplate[index][0]);
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
    if (lastReport.missing && referenceTemplate) {
      for (const missingIndex of lastReport.missing) {
        if (referenceTemplate[missingIndex]) {
          drawStroke(referenceTemplate[missingIndex], "#175cd3", 4, [8, 6]);
        }
      }
    }
    if (completedRecallWriting()) drawCompletedRecallReference(referenceTemplate);
    return;
  }

  for (const stroke of strokes) drawStroke(stroke, "#2b2925", 5);
  if (currentStroke) drawStroke(currentStroke, "#2b2925", 5);
  if (completedRecallWriting()) drawCompletedRecallReference(referenceTemplate);
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

function renderDiagnosis(diagnosis, { autoRetry = false } = {}) {
  coachOverlay.renderResult(diagnosis);
  if (!diagnosis) return;
  if (autoRetry) {
    const guidance = diagnosis.primaryCue?.text
      ? `${diagnosis.primaryCue.text} `
      : "";
    setCoachMessage(
      `${guidance}방금 획은 지웠어요. 같은 획을 다시 써 보세요.`,
      "retry",
      "↶",
    );
    return;
  }
  if (!diagnosis.primaryCue) {
    if (diagnosis.nextAction.type === "complete") {
      setCoachMessage(
        stage === 2
          ? "글자를 완성했어요. 파란 점선은 쓴 위치와 크기에 맞춘 예시입니다."
          : "글자를 완성했어요. 원하면 최종 채점을 확인해 보세요.",
        "success",
        "✓",
      );
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

async function recordCurrentAttempt(
  endedReason,
  { finalScore = null, keepalive = false } = {},
) {
  const hasWriting = strokes.length > 0 || attemptStrokeResults.length > 0;
  const character = templateCharacter || currentCharacter();
  const upgradesFailedScore = (
    attemptRecordedReason === "score_failed" && endedReason === "scored"
  );
  if (
    !hasWriting
    || !character
    || (attemptRecordedReason && !upgradesFailedScore)
  ) {
    return false;
  }
  attemptRecordedReason = endedReason;
  const endedAt = new Date().toISOString();
  void saveCurrentAttemptLocally({ endedReason, endedAt, finalScore });
  const payload = buildAttemptPayload({
    sessionId,
    attemptId,
    attemptRevision: coach.lifecycle.revision,
    character,
    mode: stage === 1 ? "trace" : "recall",
    endedReason,
    startedAt: attemptStartedAt,
    endedAt,
    strokes,
    strokeResults: attemptStrokeResults,
    finalScore,
  });
  try {
    const { status, body } = await api.request(
      "attempt",
      payload,
      { keepalive },
    );
    if (status !== 202 || body.stored !== true) {
      console.warn("attempt record unavailable", body.code || status);
      return false;
    }
    return true;
  } catch (error) {
    if (!keepalive) console.warn("attempt record unavailable", error);
    return false;
  }
}

function resetVisibleAttempt(message = "첫 획부터 천천히 써 보세요.") {
  coach.reset(template);
  clearTeacherState({ clearContext: true, resetHistory: true });
  attemptId = identity("attempt");
  attemptStartedAt = new Date().toISOString();
  attemptStrokeResults = [];
  attemptRecordedReason = null;
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
  const character = currentCharacter();
  if (!character) {
    setCoachMessage("연습할 한 글자만 선택하고 Enter를 눌러 주세요.", "retry", "!");
    return;
  }
  elements.character.value = character;
  const characterChanged = character !== templateCharacter;
  if (characterChanged) {
    void recordCurrentAttempt("character_changed");
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
    teacherNetworkAvailable = Boolean(body.ok || body.coach_ready || body.deep_score_ready);
    if (body.deep_score_ready || (body.ok && body.model_loaded)) {
      setStatus("on", `최종 채점 서버 온라인 (${body.device || "ready"})`);
    } else if (body.coach_ready || body.ok) {
      setStatus("warm", "서버 준비 중 · 로컬 코치는 사용 가능");
    } else {
      setStatus("off", "최종 채점 서버 오프라인 · 로컬 코치는 사용 가능");
    }
  } catch {
    teacherNetworkAvailable = false;
    setStatus("off", "최종 채점 서버 오프라인 · 로컬 코치는 사용 가능");
  }
}

async function requestServerRefinement({
  localDiagnosis,
  acceptedStrokes,
  completedStroke,
  expectedTemplateIndex,
  historySequence,
}) {
  const character = currentCharacter();
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
    const serverDiagnosis = diagnosisWithServerEvidence(body, normalizeCoachResponse(body));
    const selected = chooseHigherConfidence(localDiagnosis, serverDiagnosis);
    if (
      selected === serverDiagnosis
      && coach.applyServerRefinement(token, localDiagnosis, serverDiagnosis)
    ) {
      applyServerStrokeResult(attemptStrokeResults, historySequence, serverDiagnosis);
      const history = attemptStrokeResults.find(
        (entry) => entry.sequence === historySequence,
      );
      const autoRetry = Boolean(
        shouldAutoRetryStroke(serverDiagnosis)
        && history
        && !history.undone
        && history.stroke_index === strokes.length - 1
      );
      if (autoRetry) {
        markLastStrokeUndone(attemptStrokeResults, history.stroke_index);
        strokes.pop();
        coach.undoLast();
      }
      redrawInk();
      renderDiagnosis(serverDiagnosis, { autoRetry });
      prepareTeacherExplanation(serverDiagnosis, { countAttempt: false });
      void saveCurrentAttemptLocally();
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
  if (attemptRecordedReason) {
    resetVisibleAttempt("새 시도를 시작합니다. 첫 획부터 써 보세요.");
  }
  if (!coach.beginStroke()) return;
  coach.lifecycle.abortKind("coach");
  clearTeacherState({ clearContext: true });
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
  const history = createStrokeResult({
    sequence: attemptStrokeResults.length,
    strokeIndex: strokes.length - 1,
    stroke: completed,
    diagnosis,
  });
  attemptStrokeResults.push(history);
  const autoRetry = shouldAutoRetryStroke(diagnosis);
  if (autoRetry) {
    markLastStrokeUndone(attemptStrokeResults, history.stroke_index);
    strokes.pop();
    coach.undoLast();
  }
  redrawInk();
  renderDiagnosis(diagnosis, { autoRetry });
  prepareTeacherExplanation(diagnosis);
  void saveCurrentAttemptLocally();
  if (diagnosis && !autoRetry) {
    void requestServerRefinement({
      localDiagnosis: diagnosis,
      acceptedStrokes,
      completedStroke: completed,
      expectedTemplateIndex,
      historySequence: history.sequence,
    });
  }
}

bindStrokeEndEvents(elements.canvas, endStroke);

document.getElementById("undo").addEventListener("click", () => {
  if (!strokes.length) return;
  const removedStrokeIndex = strokes.length - 1;
  markLastStrokeUndone(attemptStrokeResults, removedStrokeIndex);
  strokes.pop();
  coach.undoLast();
  clearTeacherState({ clearContext: true, resetHistory: true });
  lastReport = null;
  elements.result.replaceChildren();
  elements.busyOverlay.style.display = "none";
  coachOverlay.clear();
  setCoachMessage("마지막 획을 지웠어요. 같은 획부터 다시 써 보세요.", "nudge", "↶");
  redrawInk();
  void saveCurrentAttemptLocally();
});

document.getElementById("clear").addEventListener("click", () => {
  void recordCurrentAttempt("cleared");
  resetVisibleAttempt();
});

for (const quick of document.getElementsByClassName("quick")) {
  quick.addEventListener("click", (event) => {
    elements.character.value = event.currentTarget.textContent;
    loadTemplate();
  });
}

document.getElementById("load").addEventListener("click", () => loadTemplate());
elements.character.addEventListener("change", () => loadTemplate());
elements.character.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    loadTemplate();
  }
});

elements.hint.addEventListener("click", () => {
  if (!template) return;
  if (revealCount < template.length) revealCount += 1;
  lastReport = null;
  elements.result.replaceChildren();
  redrawInk();
});

function teacherDecisionStillSelected(token, decisionId) {
  return Boolean(
    token?.attemptRevision === coach.lifecycle.revision
    && currentTeacherContext?.request?.locked_decision?.decision_id === decisionId
  );
}

elements.teacherExplain.addEventListener("click", async () => {
  const context = currentTeacherContext;
  if (!context) return;
  const { request } = context;
  const decisionId = request.locked_decision.decision_id;
  const fallback = () => localTeacherFallback(request, context.fallbackText);

  if (!teacherNetworkAvailable || !context.allowNetwork) {
    renderTeacherAnswer(fallback());
    elements.teacherRequestStatus.textContent = context.allowNetwork
      ? "오프라인 · 기본 설명"
      : "낮은 확신 · 기본 설명";
    return;
  }

  coach.lifecycle.abortKind("verbalize");
  const token = coach.lifecycle.createRequest("verbalize");
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    token.controller.abort();
  }, TEACHER_REQUEST_TIMEOUT_MS);
  elements.teacherExplain.disabled = true;
  renderTeacherAnswer(fallback());
  elements.teacherRequestStatus.textContent = "기본 설명 표시 중 · AI 설명 준비 중…";

  try {
    const { status, body } = await api.request(
      "verbalize",
      request,
      { signal: token.controller.signal },
    );
    if (!isCurrentTeacherContext(coach.lifecycle, token, decisionId, currentTeacherContext)) {
      return;
    }
    const envelope = status === 200
      ? normalizeTeacherEnvelope(body, request)
      : fallback();
    renderTeacherAnswer(envelope);
    elements.teacherRequestStatus.textContent = envelope.source === "fallback"
      ? "기본 설명"
      : "검증 완료";
  } catch (error) {
    const ownsContext = timedOut
      ? teacherDecisionStillSelected(token, decisionId)
      : isCurrentTeacherContext(coach.lifecycle, token, decisionId, currentTeacherContext);
    if (ownsContext) {
      renderTeacherAnswer(fallback());
      elements.teacherRequestStatus.textContent = timedOut
        ? "시간 초과 · 기본 설명"
        : error instanceof TypeError
          ? "검증 실패 · 기본 설명"
          : "연결 실패 · 기본 설명";
    }
    if (error.name !== "AbortError") {
      console.warn("teacher feedback unavailable", error);
    }
  } finally {
    clearTimeout(timeout);
    const stillSelected = timedOut
      ? teacherDecisionStillSelected(token, decisionId)
      : isCurrentTeacherContext(coach.lifecycle, token, decisionId, currentTeacherContext);
    coach.lifecycle.finish(token);
    if (stillSelected) elements.teacherExplain.disabled = false;
  }
});

function renderCharacterChunk() {
  const end = Math.min(shownCharacters + characterChunkSize, visibleCharacters.length);
  const fragment = document.createDocumentFragment();
  for (let index = shownCharacters; index < end; index += 1) {
    const item = document.createElement("div");
    item.className = "pchar";
    item.id = `picker-option-${index}`;
    item.setAttribute("role", "option");
    item.dataset.index = String(index);
    item.dataset.character = visibleCharacters[index];
    item.textContent = visibleCharacters[index];
    item.setAttribute("aria-selected", String(index === pickerModel.activeIndex));
    if (index === pickerModel.activeIndex) item.classList.add("active");
    fragment.appendChild(item);
  }
  elements.pickerGrid.appendChild(fragment);
  shownCharacters = end;
  const query = elements.pickerSearch.value.trim();
  elements.pickerInfo.textContent = query
    ? `${visibleCharacters.length}개 검색 결과 · Enter로 확정`
    : `${shownCharacters} / ${visibleCharacters.length}자 (스크롤하면 더 표시)`;
}

function resetCharacterGrid(characters = pickerModel.matches) {
  visibleCharacters = [...characters];
  elements.pickerGrid.replaceChildren();
  shownCharacters = 0;
  renderCharacterChunk();
}

function renderActivePickerResult({ scroll = false } = {}) {
  while (
    pickerModel.activeIndex >= shownCharacters
    && shownCharacters < visibleCharacters.length
  ) {
    renderCharacterChunk();
  }
  for (const item of elements.pickerGrid.querySelectorAll(".pchar.active")) {
    item.classList.remove("active");
    item.setAttribute("aria-selected", "false");
  }
  const active = document.getElementById(`picker-option-${pickerModel.activeIndex}`);
  if (!active) {
    elements.pickerSearch.removeAttribute("aria-activedescendant");
    return;
  }
  active.classList.add("active");
  active.setAttribute("aria-selected", "true");
  elements.pickerSearch.setAttribute("aria-activedescendant", active.id);
  if (scroll) active.scrollIntoView({ block: "nearest" });
}

function refreshPickerSearch() {
  resetCharacterGrid(pickerModel.setQuery(elements.pickerSearch.value));
  renderActivePickerResult();
}

function commitPickerCharacter(character) {
  if (!character) return;
  elements.character.value = character;
  elements.picker.style.display = "none";
  elements.pickerSearch.value = "";
  pickerModel.setQuery("");
  void loadTemplate();
}

async function loadCharacterCatalog() {
  if (characterCatalogPromise) return characterCatalogPromise;
  characterCatalogPromise = Promise.allSettled([
    api.request("chars"),
    fetch(readingIndexUrl).then(async (response) => {
      if (!response.ok) throw new Error("reading index unavailable");
      return response.json();
    }),
  ]).then(([catalog, readings]) => {
    const serverCharacters = catalog.status === "fulfilled"
      && catalog.value.status === 200
      && typeof catalog.value.body.chars === "string"
      ? splitCharacters(catalog.value.body.chars)
      : [];
    const indexedKanji = readings.status === "fulfilled"
      ? Object.keys(readings.value.readings ?? {})
      : [];
    allCharacters = splitCharacters([
      ...serverCharacters,
      ...indexedKanji,
      ...Object.keys(BUILTIN_TEMPLATES),
    ]);
    pickerModel.setCharacters(allCharacters);
    if (readings.status === "fulfilled") {
      pickerModel.setReadingIndex(readings.value.readings ?? {});
    }
    refreshPickerSearch();
  });
  return characterCatalogPromise;
}

document.getElementById("pick").addEventListener("click", async () => {
  elements.picker.style.display = "flex";
  elements.pickerSearch.focus();
  resetCharacterGrid(pickerModel.setQuery(elements.pickerSearch.value));
  await loadCharacterCatalog();
});

document.getElementById("picker-close").addEventListener("click", () => {
  elements.picker.style.display = "none";
});

elements.picker.addEventListener("click", (event) => {
  if (event.target === elements.picker) elements.picker.style.display = "none";
});

elements.pickerGrid.addEventListener("click", (event) => {
  if (!event.target.classList.contains("pchar")) return;
  commitPickerCharacter(event.target.dataset.character || event.target.textContent);
});

elements.pickerGrid.addEventListener("scroll", () => {
  if (
    shownCharacters < visibleCharacters.length
    && elements.pickerGrid.scrollTop + elements.pickerGrid.clientHeight
      > elements.pickerGrid.scrollHeight - 200
  ) {
    renderCharacterChunk();
  }
});

elements.pickerSearch.addEventListener("compositionstart", () => {
  pickerComposing = true;
});

elements.pickerSearch.addEventListener("compositionend", () => {
  pickerComposing = false;
  refreshPickerSearch();
});

elements.pickerSearch.addEventListener("input", (event) => {
  if (pickerComposing || event.isComposing) return;
  refreshPickerSearch();
});

elements.pickerSearch.addEventListener("keydown", (event) => {
  if (pickerComposing || event.isComposing || event.keyCode === 229) return;
  if (event.key === "Enter") {
    event.preventDefault();
    commitPickerCharacter(pickerModel.commit());
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowRight") {
    event.preventDefault();
    pickerModel.move(1);
    renderActivePickerResult({ scroll: true });
    return;
  }
  if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
    event.preventDefault();
    pickerModel.move(-1);
    renderActivePickerResult({ scroll: true });
    return;
  }
  if (event.key === "Escape") elements.picker.style.display = "none";
});

function correctionLabel(correction) {
  if (correction.error_code === "MISSING_STROKE") {
    return `${correction.template_index + 1}번 획 누락`;
  }
  if (correction.error_code === "EXTRA_STROKE") {
    return `${correction.index + 1}번 획 추가`;
  }
  if (correction.error_code === "POSITION_OFFSET" && correction.index < 0) {
    return "전체 위치";
  }
  return correction.index >= 0 ? `${correction.index + 1}번 획` : "전체 형태";
}

function renderScoreReport(body) {
  const scoreDetails = body.shape_score != null && body.position_score != null
    ? body.score_policy === "recall_shape_only_v1"
      ? `모양 ${escapeHtml(body.shape_score)} · 위치 자유 모드`
      : `형태 ${escapeHtml(body.shape_score)} · 위치 ${escapeHtml(body.position_score)}`
      + ` · AI ${escapeHtml(body.base_model_score)}`
    : `모델 점수 ${escapeHtml(body.base_model_score)}`;
  let html = `<div id="score-big">${escapeHtml(body.score)}점</div>`
    + `<div id="score-sub">${scoreDetails} · ${escapeHtml(body.elapsed)}초</div>`;
  const corrections = body.corrections || [];
  for (const correction of corrections) {
    const label = correctionLabel(correction);
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
    html += '<div class="msg"><b>2단계</b> — 원하는 위치와 크기에서 모양을 기억해 써 보세요. '
      + '시작 위치는 채점하지 않으며, 막히면 <b>한 획 보기</b>로 확인할 수 있습니다.</div>';
  }
  elements.result.innerHTML = html;
}

document.getElementById("go").addEventListener("click", async () => {
  const character = currentCharacter();
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
  clearTeacherState({ clearContext: true });
  const token = coach.beginFinalScore();
  elements.busyOverlay.style.display = "flex";
  try {
    const { status, body } = await api.request(
      "score",
      {
        char: character,
        strokes: toLegacyStrokes(strokes),
        mode: stage === 1 ? "trace" : "recall",
      },
      { signal: token.controller.signal },
    );
    if (!coach.lifecycle.isCurrent(token)) return;
    elements.busyOverlay.style.display = "none";
    if (status !== 200) {
      coach.finishFinalScore(token, false);
      void recordCurrentAttempt("score_failed");
      elements.result.innerHTML = `<div class="msg">${escapeHtml(
        body.message || body.detail || `오류: ${status}`,
      )}</div>`;
      setCoachMessage("최종 채점은 연결되지 않았지만 로컬 코치는 계속 동작합니다.", "nudge", "↯");
      return;
    }
    coach.finishFinalScore(token, true);
    void recordCurrentAttempt("scored", { finalScore: Number(body.score) });
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
    void recordCurrentAttempt("score_failed");
    elements.result.innerHTML = '<div class="msg">네트워크 오류 — 로컬 코치는 계속 사용할 수 있습니다.</div>';
    setCoachMessage("서버 없이도 획별 로컬 교정은 계속 받을 수 있습니다.", "nudge", "↯");
  }
});

window.addEventListener("resize", configureCanvases);
window.addEventListener("pagehide", () => {
  void recordCurrentAttempt("page_hidden", { keepalive: true });
});

setStage(1);
configureCanvases();
void refreshLocalHistory();
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
