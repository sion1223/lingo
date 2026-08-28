import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CharacterPickerModel,
  searchCharacters,
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
