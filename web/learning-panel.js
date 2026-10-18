import { entriesForGroup, JLPT_NOTICE, LEARNING_GROUPS, LEARNING_SOURCES, practiceCharacters } from "./learning.js";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";

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

export class LearningPanel {
  constructor({ onCharacter, onVariant, onMode }) {
    this.onCharacter = onCharacter;
    this.onVariant = onVariant;
    this.pack = null;
    this.entry = null;
    this.character = "永";
    this.variantId = "default";
    this.fonts = [];
    this.loadedFonts = new Map();
    this.fontRevision = 0;
    this.group = "greetings";
    $("learning-track").addEventListener("change", () => this.renderGroups());
    $("learning-group").addEventListener("change", () => {
      this.group = $("learning-group").value;
      this.renderEntries();
    });
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
      $("learning-status").textContent = `기초 가나 92자 · 일상 예시 12개 · 급수별 어휘 2개씩 · ${Object.keys(this.pack.characters).length}자 로컬 가이드`;
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
    const groups = LEARNING_GROUPS.filter((group) => group.track === track);
    $("learning-group").replaceChildren(...groups.map((group) => new Option(group.label, group.id)));
    this.group = groups[0].id;
    $("jlpt-notice").hidden = track !== "jlpt";
    this.renderEntries();
  }

  renderEntries() {
    const focusedEntry = $("learning-entries").contains(document.activeElement) ? document.activeElement.dataset.entry : null;
    const entries = entriesForGroup(this.group);
    $("learning-entries").classList.toggle("kana-entries", $("learning-track").value === "kana");
    $("learning-entries").replaceChildren(...entries.map((entry) => {
      const item = button("", () => this.selectEntry(entry), { "aria-pressed": this.entry?.id === entry.id, "data-entry": entry.id });
      const text = document.createElement("span");
      text.lang = "ja";
      text.className = "entry-text";
      text.textContent = entry.text;
      const meaning = document.createElement("small");
      meaning.textContent = entry.kind === "kana" ? entry.reading : entry.meaning;
      item.append(text, meaning);
      item.disabled = !this.pack;
      return item;
    }));
    if (focusedEntry) {
      Array.from($("learning-entries").querySelectorAll("button")).find((item) => item.dataset.entry === focusedEntry)?.focus({ preventScroll: true });
    }
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
      item.disabled = !this.pack?.characters[char];
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
