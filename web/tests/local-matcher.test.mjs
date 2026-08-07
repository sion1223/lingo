import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  analyzeCompletedStroke,
  analyzePartialStroke,
  toLegacyStroke,
} from "../coach/local-matcher.js";

const fixtures = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/realtime-strokes.json", import.meta.url), "utf8"),
);
const entries = Object.entries(fixtures.characters);

test("perfect strokes are accepted for all Phase 1 characters", () => {
  for (const [character, fixture] of entries) {
    const result = analyzeCompletedStroke({
      stroke: fixture.cases.perfect[0],
      templateStrokes: fixture.template,
      expectedTemplateIndex: 0,
    });
    assert.equal(result.accepted, true, character);
    assert.equal(result.primaryCue, null, character);
    assert.equal(result.matchedTemplateIndex, 0, character);
  }
});

test("controlled start offsets produce one actionable START_OFFSET cue", () => {
  for (const [character, fixture] of entries) {
    const result = analyzeCompletedStroke({
      stroke: fixture.cases.start_offset[0],
      templateStrokes: fixture.template,
      expectedTemplateIndex: 0,
    });
    assert.equal(result.primaryCue?.code, "START_OFFSET", character);
    assert.equal(typeof result.primaryCue.text, "string", character);
    assert.ok(result.primaryCue.vector, character);
  }
});

test("reversed strokes are rejected with DIRECTION_REVERSED", () => {
  for (const [character, fixture] of entries) {
    const result = analyzeCompletedStroke({
      stroke: fixture.cases.direction_reversed[0],
      templateStrokes: fixture.template,
      expectedTemplateIndex: 0,
    });
    assert.equal(result.primaryCue?.code, "DIRECTION_REVERSED", character);
    assert.equal(result.accepted, false, character);
    assert.equal(result.nextAction.type, "retry_current", character);
  }
});

test("large middle-path deviations are diagnosed without changing endpoints", () => {
  for (const [character, fixture] of entries) {
    const result = analyzeCompletedStroke({
      stroke: fixture.cases.path_deviation[0],
      templateStrokes: fixture.template,
      expectedTemplateIndex: 0,
    });
    assert.equal(result.primaryCue?.code, "PATH_DEVIATION", character);
    assert.ok(result.overlay.problemSegment.length >= 2, character);
    assert.ok(result.overlay.targetSegment.length >= 2, character);
  }
});

test("partial matching compares a user prefix with a template prefix", () => {
  const fixture = fixtures.characters.水;
  const template = fixture.template[0];
  const prefix = template.slice(0, Math.max(3, Math.ceil(template.length / 2)));
  const warning = analyzePartialStroke(prefix, fixture.template, 0);
  assert.equal(warning, null);
});

test("causal matching catches the next stroke without remapping accepted history", () => {
  for (const [character, fixture] of entries) {
    const result = analyzeCompletedStroke({
      stroke: fixture.template[1],
      templateStrokes: fixture.template,
      expectedTemplateIndex: 0,
    });
    assert.equal(result.primaryCue?.code, "WRONG_ORDER", character);
    assert.equal(result.accepted, false, character);
    assert.equal(result.expectedTemplateIndex, 0, character);
    assert.equal(result.matchedTemplateIndex, 1, character);
  }
});

test("a stroke after the template prefix is complete is marked extra", () => {
  const fixture = fixtures.characters.日;
  const result = analyzeCompletedStroke({
    stroke: fixture.template.at(-1),
    templateStrokes: fixture.template,
    expectedTemplateIndex: fixture.template.length,
  });
  assert.equal(result.primaryCue?.code, "EXTRA_STROKE");
  assert.equal(result.accepted, false);
});

test("legacy arrays and rich points produce the same geometry", () => {
  const fixture = fixtures.characters.木;
  const legacy = fixture.template[0];
  const rich = legacy.map(([x, y], index) => ({
    x,
    y,
    t: index * 12,
    pressure: 0.4,
    pointerType: "pen",
  }));
  const fromLegacy = analyzeCompletedStroke({
    stroke: legacy,
    templateStrokes: fixture.template,
    expectedTemplateIndex: 0,
  });
  const fromRich = analyzeCompletedStroke({
    stroke: rich,
    templateStrokes: fixture.template,
    expectedTemplateIndex: 0,
  });
  assert.deepEqual(toLegacyStroke(rich), legacy);
  assert.equal(fromRich.metrics.pathError, fromLegacy.metrics.pathError);
  assert.equal(fromRich.metrics.directionCosine, fromLegacy.metrics.directionCosine);
});

test("invalid and degenerate strokes fail safely", () => {
  const fixture = fixtures.characters.日;
  for (const stroke of [
    [{ x: Number.NaN, y: 0.2 }],
    [{ x: 0.2, y: Number.POSITIVE_INFINITY }],
    [[0.2, 0.2], [0.2, 0.2], [0.2, 0.2]],
  ]) {
    const result = analyzeCompletedStroke({
      stroke,
      templateStrokes: fixture.template,
      expectedTemplateIndex: 0,
    });
    assert.ok(result.primaryCue, JSON.stringify(stroke));
    assert.equal(result.engine, "geometry-only");
  }
});
