const JAPANESE_READING_SEPARATORS = /[.\-‐‑‒–—・･\s]/gu;
const ROMAJI_QUERY = /^[a-zāīūēō'\-\s]+$/iu;
const HANGUL_QUERY = /^[가-힣\s]+$/u;

export const CHARACTER_CATEGORIES = Object.freeze([
  "ALL",
  "G1",
  "G2",
  "G3",
  "G4",
  "G5",
  "G6",
  "JOYO",
  "KANA",
  "OTHER",
]);

export const CHARACTER_CATEGORY_LABELS = Object.freeze({
  ALL: "전체",
  G1: "초1",
  G2: "초2",
  G3: "초3",
  G4: "초4",
  G5: "초5",
  G6: "초6",
  JOYO: "기타 상용",
  KANA: "가나",
  OTHER: "기타",
});

const DIGRAPHS = Object.freeze({
  きゃ: "kya", きゅ: "kyu", きょ: "kyo",
  ぎゃ: "gya", ぎゅ: "gyu", ぎょ: "gyo",
  しゃ: "sha", しゅ: "shu", しょ: "sho",
  じゃ: "ja", じゅ: "ju", じょ: "jo",
  ちゃ: "cha", ちゅ: "chu", ちょ: "cho",
  にゃ: "nya", にゅ: "nyu", にょ: "nyo",
  ひゃ: "hya", ひゅ: "hyu", ひょ: "hyo",
  びゃ: "bya", びゅ: "byu", びょ: "byo",
  ぴゃ: "pya", ぴゅ: "pyu", ぴょ: "pyo",
  みゃ: "mya", みゅ: "myu", みょ: "myo",
  りゃ: "rya", りゅ: "ryu", りょ: "ryo",
  ふぁ: "fa", ふぃ: "fi", ふぇ: "fe", ふぉ: "fo",
  てぃ: "ti", でぃ: "di", とぅ: "tu", どぅ: "du",
  うぃ: "wi", うぇ: "we", うぉ: "wo",
  しぇ: "she", じぇ: "je", ちぇ: "che",
  つぁ: "tsa", つぃ: "tsi", つぇ: "tse", つぉ: "tso",
});

const MONOGRAPHS = Object.freeze({
  あ: "a", い: "i", う: "u", え: "e", お: "o",
  か: "ka", き: "ki", く: "ku", け: "ke", こ: "ko",
  が: "ga", ぎ: "gi", ぐ: "gu", げ: "ge", ご: "go",
  さ: "sa", し: "shi", す: "su", せ: "se", そ: "so",
  ざ: "za", じ: "ji", ず: "zu", ぜ: "ze", ぞ: "zo",
  た: "ta", ち: "chi", つ: "tsu", て: "te", と: "to",
  だ: "da", ぢ: "ji", づ: "zu", で: "de", ど: "do",
  な: "na", に: "ni", ぬ: "nu", ね: "ne", の: "no",
  は: "ha", ひ: "hi", ふ: "fu", へ: "he", ほ: "ho",
  ば: "ba", び: "bi", ぶ: "bu", べ: "be", ぼ: "bo",
  ぱ: "pa", ぴ: "pi", ぷ: "pu", ぺ: "pe", ぽ: "po",
  ま: "ma", み: "mi", む: "mu", め: "me", も: "mo",
  や: "ya", ゆ: "yu", よ: "yo",
  ら: "ra", り: "ri", る: "ru", れ: "re", ろ: "ro",
  わ: "wa", ゐ: "wi", ゑ: "we", を: "wo", ん: "n",
  ゔ: "vu",
  ぁ: "a", ぃ: "i", ぅ: "u", ぇ: "e", ぉ: "o",
  ゃ: "ya", ゅ: "yu", ょ: "yo", ゎ: "wa",
});

function hiragana(value) {
  return [...String(value).normalize("NFKC")].map((character) => {
    const codepoint = character.codePointAt(0);
    return codepoint >= 0x30a1 && codepoint <= 0x30f6
      ? String.fromCodePoint(codepoint - 0x60)
      : character;
  }).join("");
}

function normalizedReading(value) {
  return hiragana(value).replace(JAPANESE_READING_SEPARATORS, "");
}

function normalizedRomaji(value) {
  return String(value)
    .normalize("NFKC")
    .trim()
    .toLowerCase()
    .replaceAll("ā", "a")
    .replaceAll("ī", "i")
    .replaceAll("ū", "u")
    .replaceAll("ē", "e")
    .replaceAll("ō", "o")
    .replace(/[\s'\-]/g, "");
}

function lastVowel(value) {
  const match = value.match(/[aeiou](?!.*[aeiou])/);
  return match?.[0] ?? "";
}

export function kanaToRomaji(value) {
  const source = [...normalizedReading(value)];
  let output = "";
  let geminate = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === "っ") {
      geminate = true;
      continue;
    }
    if (character === "ー") {
      output += lastVowel(output);
      continue;
    }
    const pair = `${character}${source[index + 1] ?? ""}`;
    let syllable = DIGRAPHS[pair];
    if (syllable) index += 1;
    else syllable = MONOGRAPHS[character] ?? character.toLowerCase();
    if (geminate && /^[bcdfghjklmpqrstvwxyz]/.test(syllable)) {
      output += syllable[0];
    }
    output += syllable;
    geminate = false;
  }
  return output;
}

export function splitCharacters(value) {
  const source = Array.isArray(value) ? value : [...String(value ?? "")];
  const seen = new Set();
  const result = [];
  for (const item of source) {
    const character = [...String(item ?? "")][0];
    if (!character || seen.has(character)) continue;
    seen.add(character);
    result.push(character);
  }
  return result;
}

export function isKana(value) {
  const character = [...String(value ?? "").normalize("NFKC")][0];
  if (!character) return false;
  const codepoint = character.codePointAt(0);
  return (
    (codepoint >= 0x3040 && codepoint <= 0x30ff)
    || (codepoint >= 0x31f0 && codepoint <= 0x31ff)
  );
}

const KANA_ORDERS = Object.freeze([
  "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん",
  "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン",
].map((order) => Array.from(order)));
const SMALL_KANA = "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ";
const FULL_KANA = "あいうえおつやゆよわアイウエオツヤユヨワ";
const OTHER_JOYO_GRADE = 8;
export const RECOMMENDATION_COUNT = 6;

function baseKana(character) {
  const plain = character.normalize("NFD").replace(/[゙゚]/gu, "");
  const small = SMALL_KANA.indexOf(plain);
  return small >= 0 ? FULL_KANA[small] : plain;
}

// Characters at a similar level: the next kana in gojūon order, the nearest
// kanji of the same school grade in catalog order, or, for kanji outside the
// jōyō catalog, other jōyō kanji with a similar stroke count.
export function recommendSimilarCharacters(
  value,
  { catalogEntries = [], catalogIndex = {}, strokes = null, count = RECOMMENDATION_COUNT } = {},
) {
  const character = splitCharacters(value)[0];
  if (!character) return [];
  if (isKana(character)) {
    const base = baseKana(character);
    const order = KANA_ORDERS.find((letters) => letters.includes(base));
    if (!order) return [];
    const index = order.indexOf(base);
    const picks = [];
    for (let step = 1; picks.length < count && step < order.length; step += 1) {
      const candidate = order[(index + step) % order.length];
      if (candidate !== character) picks.push(candidate);
    }
    return picks;
  }
  const info = catalogIndex?.[character];
  if (info) {
    const sameGrade = catalogEntries
      .filter((entry) => Number(entry.grade) === info.grade)
      .map((entry) => entry.character);
    const position = sameGrade.indexOf(character);
    const picks = [];
    for (let distance = 1; picks.length < count && distance < sameGrade.length; distance += 1) {
      for (const candidate of [sameGrade[position + distance], sameGrade[position - distance]]) {
        if (candidate && picks.length < count) picks.push(candidate);
      }
    }
    return picks;
  }
  if (Number.isFinite(strokes) && strokes > 0) {
    return catalogEntries
      .filter((entry) => Number(entry.grade) === OTHER_JOYO_GRADE)
      .map((entry, order) => ({ character: entry.character, gap: Math.abs(Number(entry.strokes) - strokes), order }))
      .sort((left, right) => left.gap - right.gap || left.order - right.order)
      .slice(0, count)
      .map((entry) => entry.character);
  }
  return [];
}

export function createCatalogIndex(entries = []) {
  const index = Object.create(null);
  for (const [order, entry] of entries.entries()) {
    const character = splitCharacters(entry?.character)[0];
    if (!character || index[character]) continue;
    index[character] = {
      grade: Number(entry.grade),
      strokes: Number(entry.strokes),
      frequency: entry.frequency == null ? null : Number(entry.frequency),
      order,
    };
  }
  return index;
}

export function categoryForCharacter(character, catalogIndex = {}) {
  const grade = Number(catalogIndex?.[character]?.grade);
  if (grade >= 1 && grade <= 6) return `G${grade}`;
  if (grade === 8) return "JOYO";
  if (isKana(character)) return "KANA";
  return "OTHER";
}

export function filterCharactersByCategory(
  characters,
  category = "ALL",
  catalogIndex = {},
) {
  const source = splitCharacters(characters);
  if (category === "ALL") return source;
  return source.filter(
    (character) => categoryForCharacter(character, catalogIndex) === category,
  );
}

export function sortCharactersByCatalog(characters, catalogIndex = {}) {
  return splitCharacters(characters)
    .map((character, originalIndex) => {
      const catalogOrder = Number(catalogIndex?.[character]?.order);
      const category = categoryForCharacter(character, catalogIndex);
      return {
        character,
        originalIndex,
        group: Number.isFinite(catalogOrder) ? 0 : category === "KANA" ? 1 : 2,
        catalogOrder: Number.isFinite(catalogOrder) ? catalogOrder : originalIndex,
      };
    })
    .sort((left, right) => (
      left.group - right.group
      || left.catalogOrder - right.catalogOrder
      || left.originalIndex - right.originalIndex
    ))
    .map(({ character }) => character);
}

function readingValues(readingIndex, character) {
  const values = readingIndex?.[character];
  if (Array.isArray(values)) return values;
  return typeof values === "string" ? [values] : [];
}

export function primaryRomaji(character, readingIndex = {}) {
  const reading = isKana(character)
    ? character
    : readingValues(readingIndex, character)[0];
  return reading ? kanaToRomaji(reading) : "";
}

export function meaningRecord(character, meaningIndex = {}) {
  const record = meaningIndex?.[character];
  if (!Array.isArray(record)) return null;
  const [hun = "", on = "", kun = ""] = record;
  return hun || on || kun ? { hun, on, kun } : null;
}

export function primaryHun(character, meaningIndex = {}) {
  return meaningRecord(character, meaningIndex)?.hun.split(",")[0].trim() ?? "";
}

function hangulMatchRank(character, query, meaningIndex) {
  const hun = meaningRecord(character, meaningIndex)?.hun;
  if (!hun) return null;
  const needle = query.replace(/\s+/gu, " ");
  let best = null;
  for (const sense of hun.split(",")) {
    const words = sense.trim().split(/\s+/u);
    const sound = words.length > 1 ? words.at(-1) : "";
    const gloss = words.slice(0, sound ? -1 : undefined).join(" ");
    if (sense.trim() === needle || gloss === needle) return 1;
    if (sound === needle || gloss.startsWith(needle)) best = 2;
  }
  return best;
}

function queryMatchRank(character, query, readingIndex, meaningIndex = {}) {
  const normalizedQuery = String(query ?? "").normalize("NFKC").trim();
  if (!normalizedQuery) return 0;
  if (character === normalizedQuery) return 0;
  if (HANGUL_QUERY.test(normalizedQuery)) {
    return hangulMatchRank(character, normalizedQuery, meaningIndex);
  }

  const readings = [character, ...readingValues(readingIndex, character)];
  if (ROMAJI_QUERY.test(normalizedQuery)) {
    const needle = normalizedRomaji(normalizedQuery);
    let prefixMatch = false;
    for (const reading of readings) {
      const normalized = normalizedRomaji(reading);
      const romanized = kanaToRomaji(reading);
      if (normalized === needle || romanized === needle) return 1;
      if (normalized.startsWith(needle) || romanized.startsWith(needle)) {
        prefixMatch = true;
      }
    }
    return prefixMatch ? 2 : null;
  }

  const needle = normalizedReading(normalizedQuery);
  let prefixMatch = false;
  for (const reading of readings) {
    const normalized = normalizedReading(reading);
    if (normalized === needle) return 1;
    if (normalized.startsWith(needle)) prefixMatch = true;
  }
  return prefixMatch ? 2 : null;
}

export function searchCharacters(query, characters, readingIndex = {}, meaningIndex = {}) {
  const source = splitCharacters(characters);
  const normalizedQuery = String(query ?? "").normalize("NFKC").trim();
  if (!normalizedQuery) return source;

  return source
    .map((character, index) => ({
      character,
      index,
      rank: queryMatchRank(character, normalizedQuery, readingIndex, meaningIndex),
    }))
    .filter(({ rank }) => rank !== null)
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .map(({ character }) => character);
}

export class CharacterPickerModel {
  constructor(characters = [], readingIndex = {}, catalogIndex = {}) {
    this.characters = splitCharacters(characters);
    this.readingIndex = readingIndex;
    this.meaningIndex = {};
    this.catalogIndex = catalogIndex;
    this.category = "ALL";
    this.query = "";
    this.matches = [...this.characters];
    this.activeIndex = 0;
    this.selectedCharacter = null;
  }

  setCharacters(characters) {
    this.characters = splitCharacters(characters);
    return this.refresh();
  }

  setReadingIndex(readingIndex = {}) {
    this.readingIndex = readingIndex;
    return this.refresh();
  }

  setMeaningIndex(meaningIndex = {}) {
    this.meaningIndex = meaningIndex;
    return this.refresh();
  }

  setCatalogIndex(catalogIndex = {}) {
    this.catalogIndex = catalogIndex;
    return this.refresh();
  }

  setCategory(category = "ALL") {
    this.category = CHARACTER_CATEGORIES.includes(category) ? category : "ALL";
    return this.refresh();
  }

  setQuery(query) {
    this.query = String(query ?? "");
    return this.refresh();
  }

  // Clear the search and make `character` the active result, switching to its
  // category when the current filter hides it.
  focusCharacter(character) {
    const target = splitCharacters(character)[0];
    this.query = "";
    this.refresh();
    if (!target || !this.characters.includes(target)) return false;
    if (!this.matches.includes(target)) {
      this.category = categoryForCharacter(target, this.catalogIndex);
      this.refresh();
    }
    this.activeIndex = Math.max(0, this.matches.indexOf(target));
    return this.matches[this.activeIndex] === target;
  }

  refresh() {
    const searched = searchCharacters(
      this.query,
      this.characters,
      this.readingIndex,
      this.meaningIndex,
    );
    this.matches = filterCharactersByCategory(
      searched,
      this.category,
      this.catalogIndex,
    );
    this.activeIndex = 0;
    return [...this.matches];
  }

  move(delta) {
    if (!this.matches.length) return null;
    this.activeIndex = (
      this.activeIndex + Number(delta) + this.matches.length
    ) % this.matches.length;
    return this.matches[this.activeIndex];
  }

  commit(index = this.activeIndex) {
    const selected = this.matches[index] ?? null;
    this.selectedCharacter = selected;
    return selected;
  }
}
