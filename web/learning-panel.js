import {
  DAILY_SUGGESTIONS,
  entriesForGroup,
  JLPT_NOTICE,
  KANA_VOWELS,
  kanaTable,
  LEARNING_ENTRIES,
  LEARNING_GROUPS,
  LEARNING_SOURCES,
  practiceCharacters,
} from "./learning.js";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const DAILY_AI_GROUP = "daily-ai";
const GENERATION_TIMEOUT_MS = 60000;
const MAX_RECENT_WORDS = 40;
const STATIC_DAILY_ENTRIES = LEARNING_ENTRIES.filter((entry) => entry.kind === "daily");

export function fontSupports(font, text) {
  return Array.from(text).every((char) => font.coverage.some(([start, end]) => char.codePointAt(0) >= start && char.codePointAt(0) <= end));
}

export function renderPathPreview(container, display) {
  container.replaceChildren();
  if (!display) return;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 1 1");
  svg.setAttribute("aria-hidden", "true");
  const group = document.createElementNS(SVG_NS, "g");
  group.setAttribute("transform", `translate(${display.offset.join(" ")}) scale(${display.scale})`);
  for (const d of display.paths) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", String(0.026 / display.scale));
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    group.append(path);
  }
  svg.append(group);
  container.append(svg);
}

function button(text, onClick, attributes = {}) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  element.addEventListener("click", onClick);
  return element;
}

function clip(value, limit) {
  return String(value ?? "").slice(0, limit);
}

// Generated entries are only ever rendered with textContent; this pins their shape.
export function normalizeGeneratedEntry(entry, index = 0) {
  const kind = entry?.kind === "jlpt" ? "jlpt" : "daily";
  return {
    id: typeof entry?.id === "string" && entry.id ? clip(entry.id, 40) : `ai-entry-${Date.now()}-${index}`,
    group: clip(entry?.group, 20),
    text: clip(entry?.text, 40),
    reading: clip(entry?.reading, 80),
    meaning: clip(entry?.meaning, 120),
    usage: clip(entry?.usage, 200),
    basis: clip(entry?.basis, 200),
    kind,
    source: "claude",
    estimated: kind === "jlpt",
  };
}

export class LearningPanel {
  constructor({ onCharacter, onVariant, onMode, requestGeneration }) {
    this.onCharacter = onCharacter;
    this.onVariant = onVariant;
    this.requestGeneration = requestGeneration;
    this.pack = null;
    this.entry = null;
    this.character = "永";
    this.variantId = "default";
    this.fonts = [];
    this.loadedFonts = new Map();
    this.fontRevision = 0;
    this.group = DAILY_AI_GROUP;
    this.shown = Object.create(null);
    this.recentWords = Object.create(null);
    this.generation = null;
    this.generationRevision = 0;
    $("learning-track").addEventListener("change", () => this.renderGroups());
    $("learning-group").addEventListener("change", () => {
      this.group = $("learning-group").value;
      this.clearEntry();
      if ($("learning-track").value === "jlpt") void this.generateJlpt();
      this.renderEntries();
    });
    $("daily-generate").addEventListener("click", () => void this.generateDaily($("daily-situation").value));
    $("daily-situation").addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      void this.generateDaily($("daily-situation").value);
    });
    $("daily-suggestions").replaceChildren(...DAILY_SUGGESTIONS.map((situation) => button(situation, () => {
      $("daily-situation").value = situation;
      void this.generateDaily(situation);
    })));
    $("jlpt-generate").addEventListener("click", () => void this.generateJlpt());
    $("example-font").addEventListener("change", () => void this.updateFont());
    $("practice-variant").addEventListener("change", () => this.onVariant($("practice-variant").value));
    $("practice-mode").addEventListener("change", () => onMode(Number($("practice-mode").value)));
    $("jlpt-notice").textContent = JLPT_NOTICE;
    this.renderGroups();
  }

  async load() {
    const results = await Promise.allSettled([
      fetch(new URL("./data/learning-practice.json", import.meta.url)).then((response) => {
        if (!response.ok) throw new Error("practice pack unavailable");
        return response.json();
      }),
      fetch(new URL("./fonts/manifest.json", import.meta.url)).then((response) => {
        if (!response.ok) throw new Error("font manifest unavailable");
        return response.json();
      }),
    ]);
    if (results[0].status === "fulfilled" && results[0].value.schema_version === 1) {
      this.pack = results[0].value;
      $("learning-status").textContent = `기초 가나 92자 · 일상회화와 JLPT 예시는 AI가 새로 만들어요 · ${Object.keys(this.pack.characters).length}자 로컬 가이드`;
    } else {
      $("learning-status").textContent = "학습 가이드를 불러오지 못했습니다. 페이지를 새로고침해 주세요. 기존 문자 검색은 사용할 수 있습니다.";
    }
    if (results[1].status === "fulfilled") {
      this.fonts = results[1].value.fonts;
      for (const font of this.fonts) $("example-font").add(new Option(font.family, font.id));
      $("example-font").value = "klee-one";
    }
    this.renderEntries();
    this.setCharacter(this.character, this.variantId);
    return this.pack;
  }

  renderGroups() {
    const track = $("learning-track").value;
    const groups = track === "daily" ? [] : LEARNING_GROUPS.filter((group) => group.track === track);
    $("learning-group-field").hidden = track === "daily";
    $("learning-group").replaceChildren(...groups.map((group) => new Option(group.label, group.id)));
    this.group = groups[0]?.id ?? DAILY_AI_GROUP;
    $("daily-ai").hidden = track !== "daily";
    $("jlpt-ai").hidden = track !== "jlpt";
    $("jlpt-notice").hidden = track !== "jlpt";
    this.cancelGeneration();
    this.clearEntry();
    this.setAiStatus(track === "daily" && !this.shown[DAILY_AI_GROUP]?.length
      ? "상황을 입력하면 AI가 연습 문장 1~2개를 만들어 줘요."
      : "");
    if (track === "jlpt") void this.generateJlpt();
    this.renderEntries();
  }

  renderEntries() {
    const list = $("learning-entries");
    const focusedEntry = list.contains(document.activeElement) ? document.activeElement.dataset.entry : null;
    const kana = $("learning-track").value === "kana";
    list.classList.toggle("kana-entries", kana);
    list.replaceChildren(...(kana
      ? this.kanaChartCells()
      : (this.shown[this.group] || []).map((entry) => this.entryButton(entry))));
    if (focusedEntry) {
      Array.from(list.querySelectorAll("button")).find((item) => item.dataset.entry === focusedEntry)?.focus({ preventScroll: true });
    }
  }

  kanaChartCells() {
    const heads = KANA_VOWELS.map((vowel) => {
      const head = document.createElement("span");
      head.className = "kana-head";
      head.textContent = vowel;
      head.setAttribute("aria-hidden", "true");
      return head;
    });
    const cells = kanaTable(this.group).flat().map((entry) => {
      if (entry) return this.entryButton(entry);
      const gap = document.createElement("span");
      gap.className = "kana-gap";
      gap.setAttribute("aria-hidden", "true");
      return gap;
    });
    return [...heads, ...cells];
  }

  entryButton(entry) {
    const item = button("", () => this.selectEntry(entry), { "aria-pressed": this.entry?.id === entry.id, "data-entry": entry.id });
    const text = document.createElement("span");
    text.lang = "ja";
    text.className = "entry-text";
    text.textContent = entry.text;
    const meaning = document.createElement("small");
    meaning.textContent = entry.kind === "kana" ? entry.reading : entry.meaning;
    item.append(text, meaning);
    item.disabled = entry.source !== "claude" && !this.pack;
    return item;
  }

  canPractice(char, entry) {
    return entry.source === "claude" || Boolean(this.pack?.characters[char]);
  }

  clearEntry() {
    this.entry = null;
    $("learning-detail").hidden = true;
  }

  setAiStatus(message, tone = "info") {
    $("ai-status").textContent = message;
    $("ai-status").dataset.tone = tone;
  }

  setGenerating(active) {
    $("daily-generate").disabled = active;
    $("jlpt-generate").disabled = active;
    $("learning-entries").setAttribute("aria-busy", String(active));
  }

  cancelGeneration() {
    this.generationRevision += 1;
    this.generation?.abort();
    this.generation = null;
    this.setGenerating(false);
  }

  async runGeneration(payload, loadingMessage) {
    this.cancelGeneration();
    const controller = new AbortController();
    const revision = this.generationRevision;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, GENERATION_TIMEOUT_MS);
    this.generation = controller;
    this.setGenerating(true);
    this.setAiStatus(loadingMessage, "loading");
    try {
      const { status, body } = await this.requestGeneration(payload, { signal: controller.signal });
      if (revision !== this.generationRevision) return null;
      if (status === 200 && Array.isArray(body?.entries) && body.entries.length) {
        return { entries: body.entries.map(normalizeGeneratedEntry) };
      }
      return {
        code: body?.detail?.code || `HTTP_${status}`,
        message: body?.detail?.message || "AI 생성에 실패했어요.",
      };
    } catch (error) {
      if (revision !== this.generationRevision) return null;
      if (timedOut) return { code: "AI_TIMEOUT", message: "응답이 너무 오래 걸려 중단했어요." };
      if (error.name === "AbortError") return null;
      return { code: "NETWORK", message: "서버에 연결하지 못했어요." };
    } finally {
      clearTimeout(timer);
      if (revision === this.generationRevision) {
        this.generation = null;
        this.setGenerating(false);
      }
    }
  }

  async generateDaily(rawSituation) {
    const situation = String(rawSituation ?? "").trim();
    if (!situation) {
      this.setAiStatus("연습하고 싶은 상황을 먼저 입력해 주세요.", "error");
      $("daily-situation").focus();
      return;
    }
    const result = await this.runGeneration({ kind: "daily", situation }, "AI가 문장을 만드는 중이에요…");
    if (!result) return;
    this.clearEntry();
    if (result.entries) {
      this.shown[DAILY_AI_GROUP] = result.entries;
      this.setAiStatus(`문장 ${result.entries.length}개를 만들었어요. 문장을 누르고 한 글자씩 써 보세요.`, "success");
    } else {
      this.shown[DAILY_AI_GROUP] = STATIC_DAILY_ENTRIES;
      this.setAiStatus(`${result.message} 대신 기본 예시를 보여 드려요.`, "error");
    }
    if ($("learning-track").value === "daily") this.renderEntries();
  }

  async generateJlpt() {
    const level = this.group;
    const recent = this.recentWords[level] || [];
    const result = await this.runGeneration(
      { kind: "jlpt", level, exclude: recent.slice(-MAX_RECENT_WORDS) },
      `AI가 ${level} 단어를 고르는 중이에요…`,
    );
    if (!result) return;
    this.clearEntry();
    if (result.entries) {
      this.shown[level] = result.entries;
      this.recentWords[level] = [...recent, ...result.entries.map((entry) => entry.text)].slice(-MAX_RECENT_WORDS);
      this.setAiStatus(`${level} 단어 ${result.entries.length}개를 새로 골랐어요. ‘다른 단어 받기’를 누르면 또 바뀌어요.`, "success");
    } else {
      this.shown[level] = entriesForGroup(level);
      this.setAiStatus(`${result.message} 대신 기본 예시를 보여 드려요.`, "error");
    }
    this.renderEntries();
  }

  selectEntry(entry) {
    this.entry = entry;
    $("learning-detail").hidden = false;
    $("entry-title").textContent = entry.text;
    $("entry-reading").textContent = `${entry.reading} · ${entry.meaning}`;
    $("entry-usage").textContent = entry.usage || "어휘를 읽고 아래 구성 문자를 한 글자씩 연습하세요.";
    $("entry-basis").textContent = entry.basis;
    const source = LEARNING_SOURCES[entry.source];
    $("entry-source").hidden = !source;
    if (source) { $("entry-source").href = source.url; $("entry-source").textContent = source.label; }
    const chars = practiceCharacters(entry);
    $("entry-characters").replaceChildren(...chars.map((char) => {
      const item = button(char, () => this.onCharacter(char, entry.id), { "aria-label": `${char} 연습`, "data-char": char, lang: "ja" });
      item.disabled = !this.canPractice(char, entry);
      return item;
    }));
    this.renderEntries();
    if (chars[0]) this.onCharacter(chars[0], entry.id);
  }

  setCharacter(char, variantId = "default", display = null, entryId = null) {
    this.character = char;
    this.variantId = variantId;
    if (this.entry && entryId !== this.entry.id) {
      this.entry = null;
      $("learning-detail").hidden = true;
      this.renderEntries();
    }
    $("handwriting-text").textContent = char;
    const forms = this.pack?.characters[char]?.forms || [];
    const practice = forms.filter((form) => form.usage === "practice");
    $("practice-variant").replaceChildren(...(practice.length ? practice : [{ id: "default", label: "기본 획순" }]).map((form) => new Option(form.label, form.id)));
    $("practice-variant").value = variantId;
    $("practice-variant").disabled = practice.length < 2;
    const selected = forms.find((form) => form.id === variantId);
    renderPathPreview($("path-preview"), display || selected?.display);
    $("path-preview-note").textContent = display || selected?.display ? "현재 연습 경로" : "이 문자의 곡선 예시를 불러오는 중";
    $("variant-note").textContent = selected?.note || "이 문자에는 기본 획순을 제공합니다. 자형의 선택지는 개별 검수 후 늘립니다.";
    const reference = forms.find((form) => form.usage === "reference");
    $("variant-reference").hidden = !reference;
    if (reference) {
      renderPathPreview($("variant-reference-preview"), reference.display);
      $("variant-reference-note").textContent = reference.note;
    }
    for (const item of $("entry-characters").querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.char === char));
    void this.updateFont();
  }

  async updateFont() {
    const revision = ++this.fontRevision;
    const font = this.fonts.find((item) => item.id === $("example-font").value);
    $("handwriting-text").style.fontFamily = "serif";
    $("font-license").hidden = !font;
    if (!font) { $("font-status").textContent = "기본 글꼴로 모양을 표시합니다."; return; }
    $("font-license").href = new URL(`./fonts/${font.license}`, import.meta.url);
    $("font-license").textContent = `${font.family} · OFL 1.1 원문`;
    if (!fontSupports(font, this.character)) {
      $("font-status").textContent = `${font.family}에 이 문자가 없어 기본 글꼴로 표시합니다.`;
      return;
    }
    $("font-status").textContent = `${font.family} 불러오는 중…`;
    try {
      if (!this.loadedFonts.has(font.id)) {
        const face = new FontFace(font.family, `url("${new URL(`./fonts/${font.file}`, import.meta.url)}")`);
        this.loadedFonts.set(font.id, face.load().then((loaded) => { document.fonts.add(loaded); return loaded; }));
      }
      await this.loadedFonts.get(font.id);
      if (revision !== this.fontRevision) return;
      $("handwriting-text").style.fontFamily = `"${font.family}", serif`;
      $("font-status").textContent = `${font.family} · 모양 참고용`;
    } catch {
      this.loadedFonts.delete(font.id);
      if (revision === this.fontRevision) $("font-status").textContent = "글꼴을 불러오지 못해 기본 글꼴로 표시합니다.";
    }
  }
}
