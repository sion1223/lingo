import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CHARACTER_CATEGORY_LABELS,
  CharacterPickerModel,
  categoryForCharacter,
  createCatalogIndex,
  primaryRomaji,
  searchCharacters,
  sortCharactersByCatalog,
  splitCharacters,
} from "../character-picker.js";

test("picker keeps Japanese IME input as a draft until an explicit commit", () => {
  const picker = new CharacterPickerModel("永水語");

  const matches = picker.setQuery("語");

  assert.deepEqual(matches, ["語"]);
  assert.equal(picker.selectedCharacter, null);
  assert.equal(picker.commit(), "語");
  assert.equal(picker.selectedCharacter, "語");
});

test("picker iterates supplementary kanji as complete Unicode characters", () => {
  assert.deepEqual(splitCharacters("日𠮷語"), ["日", "𠮷", "語"]);
  assert.deepEqual(searchCharacters("𠮷", "日𠮷語"), ["𠮷"]);
});

test("romaji finds kana and kanji readings without a Japanese keyboard", () => {
  const readings = {
    川: ["かわ"],
    河: ["かわ"],
  };

  assert.deepEqual(searchCharacters("ka", "かカきキ", readings), ["か", "カ"]);
  assert.deepEqual(searchCharacters("kawa", "川河山", readings), ["川", "河"]);
});

test("Enter-style commit chooses the active result instead of the first typed glyph", () => {
  const picker = new CharacterPickerModel("日本語", {
    日: ["にち", "ひ"],
    本: ["ほん", "もと"],
    語: ["ご", "かたる"],
  });

  picker.setQuery("go");
  assert.equal(picker.selectedCharacter, null);
  assert.equal(picker.commit(0), "語");
});

test("generated KANJIDIC2 readings make common kanji searchable by romaji", () => {
  const readingIndex = JSON.parse(readFileSync(
    new URL("../data/kanji-readings.json", import.meta.url),
    "utf8",
  )).readings;

  const kawaMatches = searchCharacters("kawa", "乾川河山", readingIndex);
  assert.deepEqual(kawaMatches.slice(0, 2), ["川", "河"]);
  assert.ok(kawaMatches.includes("乾"));
  assert.equal(searchCharacters("go", "語山", readingIndex)[0], "語");
});

test("generated catalog keeps education kanji by grade before other joyo kanji", () => {
  const catalog = JSON.parse(readFileSync(
    new URL("../data/kanji-catalog.json", import.meta.url),
    "utf8",
  ));
  const expectedCounts = {
    "1": 80,
    "2": 160,
    "3": 200,
    "4": 202,
    "5": 193,
    "6": 191,
    other_joyo: 1110,
  };

  assert.deepEqual(catalog.counts, expectedCounts);
  assert.equal(catalog.entries.length, 2136);
  assert.equal(new Set(catalog.entries.map(({ character }) => character)).size, 2136);
  assert.ok(catalog.entries.slice(0, 1026).every(({ grade }) => grade >= 1 && grade <= 6));
  assert.ok(catalog.entries.slice(1026).every(({ grade }) => grade === 8));
});

test("catalog categories, ordering, and displayed romaji share one index", () => {
  const catalog = JSON.parse(readFileSync(
    new URL("../data/kanji-catalog.json", import.meta.url),
    "utf8",
  ));
  const readings = JSON.parse(readFileSync(
    new URL("../data/kanji-readings.json", import.meta.url),
    "utf8",
  )).readings;
  const catalogIndex = createCatalogIndex(catalog.entries);
  const reversed = [...catalog.entries].reverse().slice(0, 12).map(({ character }) => character);

  assert.deepEqual(
    sortCharactersByCatalog([...reversed, "あ", "々"], catalogIndex),
    [...reversed].reverse().concat("あ", "々"),
  );
  assert.equal(categoryForCharacter("一", catalogIndex), "G1");
  assert.equal(categoryForCharacter("歳", catalogIndex), "JOYO");
  assert.equal(categoryForCharacter("あ", catalogIndex), "KANA");
  assert.equal(CHARACTER_CATEGORY_LABELS.G1, "초1");
  assert.equal(primaryRomaji("語", readings), "go");
  assert.equal(primaryRomaji("し", readings), "shi");

  const picker = new CharacterPickerModel("一二歳あ", readings, catalogIndex);
  assert.deepEqual(picker.setCategory("G1"), ["一", "二"]);
  assert.deepEqual(picker.setCategory("JOYO"), ["歳"]);
  assert.deepEqual(picker.setCategory("KANA"), ["あ"]);
});
