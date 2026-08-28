import assert from "node:assert/strict";
import test from "node:test";

import { buildLocalAttemptRecord } from "../local-attempt-store.js";

const stroke = [
  { x: 0.1, y: 0.2, t: 0, pressure: 0.25, tiltX: 3, tiltY: -2, pointerType: "pen" },
  { x: 0.6, y: 0.7, t: 38, pressure: 0.7, tiltX: 5, tiltY: 1, pointerType: "pen" },
];

const strokeResult = {
  sequence: 0,
  stroke_index: 0,
  stroke,
  matched_template_index: 0,
  accepted: false,
  error_code: "PATH_DEVIATION",
  confidence: 0.93,
  intervention: "pause_and_retry",
  source: "local",
  undone: true,
};

function record(overrides = {}) {
  return buildLocalAttemptRecord({
    sessionId: "session-1",
    attemptId: "attempt-1",
    attemptRevision: 2,
    character: "永",
    mode: "trace",
    startedAt: "2026-08-28T01:00:00.000Z",
    savedAt: "2026-08-28T01:00:01.000Z",
    strokes: [],
    strokeResults: [strokeResult],
    ...overrides,
  });
}

test("local active records retain deleted stroke order and rich pointer samples", () => {
  const saved = record();

  assert.equal(saved.status, "active");
  assert.equal(saved.ended_reason, null);
  assert.equal(saved.stroke_results[0].undone, true);
  assert.deepEqual(saved.stroke_results[0].stroke, stroke);

  const originalX = stroke[0].x;
  const originalErrorCode = strokeResult.error_code;
  stroke[0].x = 0.99;
  strokeResult.error_code = "MUTATED";
  assert.equal(saved.stroke_results[0].stroke[0].x, 0.1);
  assert.equal(saved.stroke_results[0].error_code, "PATH_DEVIATION");
  stroke[0].x = originalX;
  strokeResult.error_code = originalErrorCode;
});

test("local finished records include the final score and end time", () => {
  const saved = record({
    endedReason: "scored",
    endedAt: "2026-08-28T01:00:02.000Z",
    finalScore: 87.5,
  });

  assert.equal(saved.status, "finished");
  assert.equal(saved.ended_reason, "scored");
  assert.equal(saved.ended_at, "2026-08-28T01:00:02.000Z");
  assert.equal(saved.final_score, 87.5);
});
