import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { allowsRemoteScoring, entriesForGroup, JLPT_NOTICE, LEARNING_ENTRIES, practiceCharacters, practiceForm } from "../learning.js";
import { fontSupports } from "../learning-panel.js";
import { analyzeCompletedStroke } from "../coach/local-matcher.js";

const pack = JSON.parse(readFileSync(new URL("../data/learning-practice.json", import.meta.url), "utf8"));
const fonts = JSON.parse(readFileSync(new URL("../fonts/manifest.json", import.meta.url), "utf8")).fonts;

test("menus cover basic kana and small explicitly estimated JLPT vocabulary sets", () => {
  assert.equal(entriesForGroup("hiragana").length, 46);
  assert.equal(entriesForGroup("katakana").length, 46);
  assert.equal(new Set(LEARNING_ENTRIES.map((entry) => entry.id)).size, LEARNING_ENTRIES.length);
  assert.match(JLPT_NOTICE, /전체 목록을 공개하지/);
  for (const level of ["N5", "N4", "N3", "N2", "N1"]) {
    const entries = entriesForGroup(level);
    assert.equal(entries.length, 2);
    for (const entry of entries) {
      assert.equal(entry.estimated, true);
      assert.equal(entry.source, "jlpt");
      assert.match(entry.basis, /편집자 추정/);
    }
  }
  assert.equal(LEARNING_ENTRIES.filter((entry) => entry.kind === "daily").length, 12);
});

test("every menu character has an offline practice template; phrases are not sent as characters", () => {
  for (const entry of LEARNING_ENTRIES) {
    assert.ok(entry.reading && entry.meaning && entry.basis, entry.id);
    const characters = practiceCharacters(entry);
    assert.ok(characters.length > 0);
    for (const char of characters) {
      assert.equal(Array.from(char).length, 1);
      assert.ok(practiceForm(pack, char), `${entry.id}: ${char}`);
    }
  }
  assert.deepEqual(practiceCharacters({ text: "水、水！" }), ["水"]);
});

test("reference forms and unknown variants cannot become a practice template or remote score", () => {
  assert.ok(practiceForm(pack, "木", "kaisho-hook"));
  assert.equal(practiceForm(pack, "令", "print-reference"), null);
  for (const id of ["Kaisho", "Insatsu", "unknown", "../06728", null]) {
    assert.equal(practiceForm(pack, "木", id), null);
    assert.equal(allowsRemoteScoring(id), false);
  }
  assert.equal(allowsRemoteScoring("kaisho-hook"), false);
  assert.equal(allowsRemoteScoring("default"), true);
});

test("reviewed 木 uses its selected paths and still rejects reversed and out-of-order strokes", () => {
  const alternate = practiceForm(pack, "木", "kaisho-hook").strokes;
  assert.notDeepEqual(alternate[1], practiceForm(pack, "木").strokes[1]);
  for (let i = 0; i < alternate.length; i += 1) {
    const result = analyzeCompletedStroke({ stroke: alternate[i], templateStrokes: alternate, expectedTemplateIndex: i });
    assert.equal(result.accepted, true);
    assert.equal(result.matchedTemplateIndex, i);
  }
  const reversed = analyzeCompletedStroke({ stroke: [...alternate[1]].reverse(), templateStrokes: alternate, expectedTemplateIndex: 1 });
  assert.equal(reversed.accepted, false);
  assert.equal(reversed.primaryCue.code, "DIRECTION_REVERSED");
  const swapped = analyzeCompletedStroke({ stroke: alternate[1], templateStrokes: alternate, expectedTemplateIndex: 0 });
  assert.equal(swapped.accepted, false);
  assert.equal(swapped.primaryCue.code, "WRONG_ORDER");
});

test("official font files and OFL notices match audit hashes and cover the menu", () => {
  assert.equal(fonts.length, 3);
  for (const font of fonts) {
    const file = readFileSync(new URL(`../fonts/${font.file}`, import.meta.url));
    const license = readFileSync(new URL(`../fonts/${font.license}`, import.meta.url));
    assert.equal(createHash("sha256").update(file).digest("hex"), font.sha256);
    assert.equal(createHash("sha256").update(license).digest("hex"), font.license_sha256);
    assert.match(license.toString(), /SIL OPEN FONT LICENSE Version 1\.1/);
    assert.equal(font.modified, false);
    for (const char of Object.keys(pack.characters)) assert.equal(fontSupports(font, char), true, `${font.family}: ${char}`);
    assert.equal(fontSupports(font, "🦄"), false);
  }
});

test("kana menus are laid out as the gojūon chart with gaps", async () => {
  const { kanaTable, KANA_VOWELS } = await import("../learning.js");
  const hiragana = kanaTable("hiragana").map((row) => row.map((entry) => entry?.text ?? "·").join(""));
  assert.equal(KANA_VOWELS.join(""), "aiueo");
  assert.deepEqual(hiragana, [
    "あいうえお", "かきくけこ", "さしすせそ", "たちつてと", "なにぬねの",
    "はひふへほ", "まみむめも", "や·ゆ·よ", "らりるれろ", "わ···を", "ん····",
  ]);
  const katakana = kanaTable("katakana").flat().filter(Boolean);
  assert.equal(katakana.length, 46);
  assert.equal(kanaTable("katakana")[9][4].text, "ヲ");
});

test("generated entries keep a fixed shape and stay practicable", async () => {
  const { normalizeGeneratedEntry } = await import("../learning-panel.js");
  const entry = normalizeGeneratedEntry({ id: "ai-daily-1", kind: "daily", text: "水をください", reading: "みずをください", meaning: "물을 주세요", extra: "<b>x</b>" });
  assert.equal(entry.source, "claude");
  assert.equal(entry.kind, "daily");
  assert.equal("extra" in entry, false);
  assert.deepEqual(practiceCharacters(entry), ["水", "を", "く", "だ", "さ", "い"]);
  assert.equal(normalizeGeneratedEntry({ kind: "jlpt", text: "準備" }).estimated, true);
});
