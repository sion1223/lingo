import assert from "node:assert/strict";
import test from "node:test";

import { WarningHysteresis } from "../coach/controller.js";
import { cueDecision, selectPrimaryCue } from "../coach/policy.js";

function metrics(overrides = {}) {
  const line = [[0.1, 0.1], [0.9, 0.1]];
  return {
    startError: 0,
    endError: 0,
    pathError: 0,
    shapeError: 0,
    directionCosine: 1,
    lengthRatio: 1,
    looksReversed: false,
    startVector: { dx: 0, dy: 0 },
    endVector: { dx: 0, dy: 0 },
    alignedUser: line,
    problemSegment: line,
    ...overrides,
  };
}

test("policy emits at most one cue and respects teaching priority", () => {
  const cue = selectPrimaryCue(metrics({
    startError: 0.11,
    pathError: 0.09,
    endError: 0.1,
    startVector: { dx: -0.11, dy: 0 },
  }));
  assert.equal(cue.code, "START_OFFSET");
  assert.equal(Array.isArray(cue), false);
  assert.equal(cueDecision(cue).accepted, true);
});

test("major direction reversal pauses for a retry", () => {
  const cue = selectPrimaryCue(metrics({
    looksReversed: true,
    directionCosine: -1,
    startVector: { dx: 0.8, dy: 0 },
  }));
  assert.equal(cue.code, "DIRECTION_REVERSED");
  assert.deepEqual(cueDecision(cue), {
    accepted: false,
    severity: "major",
    intervention: "pause_and_retry",
  });
});

test("a translated but correctly shaped stroke gets a nudge instead of a retry", () => {
  const cue = selectPrimaryCue(metrics({
    startError: 0.2,
    endError: 0.2,
    pathError: 0.2,
    shapeError: 0,
    directionCosine: 1,
    lengthRatio: 1,
    startVector: { dx: 0, dy: -0.2 },
    endVector: { dx: 0, dy: -0.2 },
  }));

  assert.equal(cue.code, "START_OFFSET");
  assert.deepEqual(cueDecision(cue), {
    accepted: true,
    severity: "minor",
    intervention: "nudge",
  });
});

test("length thresholds expose stable TOO_SHORT and TOO_LONG codes", () => {
  assert.equal(selectPrimaryCue(metrics({ lengthRatio: 0.6, lengthExcess: -0.3 })).code, "TOO_SHORT");
  assert.equal(
    selectPrimaryCue(metrics({ lengthRatio: 1.5, lengthExcess: 0.3, endOvershoot: 0.2 })).code,
    "TOO_LONG",
  );
});

test("length cues need a real distance, not just a ratio", () => {
  // A dot traced with hand shake: long ratio, but the end never passes the guide.
  assert.equal(selectPrimaryCue(metrics({ lengthRatio: 1.6, lengthExcess: 0.05, endOvershoot: 0.01 })), null);
  // A tiny dot that is a bit longer but by less than the minimum distance.
  assert.equal(selectPrimaryCue(metrics({ lengthRatio: 1.6, lengthExcess: 0.03, endOvershoot: 0.03 })), null);
  // A tiny tick stopped a hair early.
  assert.equal(selectPrimaryCue(metrics({ lengthRatio: 0.6, lengthExcess: -0.02 })), null);
});

test("hysteresis waits 150ms, releases below 0.60, and applies cooldown", () => {
  const hysteresis = new WarningHysteresis();
  const warning = { code: "PATH_DEVIATION", confidence: 0.9 };
  assert.equal(hysteresis.update(warning, 0), null);
  assert.equal(hysteresis.update(warning, 149), null);
  assert.equal(hysteresis.update(warning, 150)?.code, "PATH_DEVIATION");
  assert.equal(hysteresis.update({ ...warning, confidence: 0.59 }, 200), null);
  assert.equal(hysteresis.update(warning, 700), null);
  assert.equal(hysteresis.update(warning, 800), null);
  assert.equal(hysteresis.update(warning, 950)?.code, "PATH_DEVIATION");
});
