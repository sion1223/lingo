import assert from "node:assert/strict";
import test from "node:test";

import {
  applyServerStrokeResult,
  buildAttemptPayload,
  createStrokeResult,
  markLastStrokeUndone,
} from "../attempt-log.js";

const richStroke = [
  { x: 0.1, y: 0.2, t: 0, pressure: 0.3, tiltX: 2, tiltY: -1, pointerType: "pen" },
  { x: 0.4, y: 0.6, t: 42, pressure: 0.7, tiltX: 4, tiltY: 0, pointerType: "pen" },
];

function diagnosis(overrides = {}) {
  return {
    engine: "geometry-only",
    matchedTemplateIndex: 0,
    accepted: true,
    matchConfidence: 0.94,
    intervention: "silent",
    primaryCue: null,
    ...overrides,
  };
}

test("attempt batch preserves point order, timing, pressure, tilt, and pointer type", () => {
  const result = createStrokeResult({
    sequence: 0,
    strokeIndex: 0,
    stroke: richStroke,
    diagnosis: diagnosis(),
  });
  const payload = buildAttemptPayload({
    sessionId: "session-1",
    attemptId: "attempt-1",
    attemptRevision: 4,
    character: "語",
    mode: "recall",
    endedReason: "scored",
    startedAt: "2026-08-21T00:00:00.000Z",
    endedAt: "2026-08-21T00:00:01.000Z",
    strokes: [richStroke],
    strokeResults: [result],
    finalScore: 88.5,
  });

  assert.deepEqual(payload.strokes[0], richStroke);
  assert.deepEqual(payload.stroke_results[0].stroke, richStroke);
  assert.equal(payload.stroke_results[0].sequence, 0);
  assert.equal(payload.stroke_results[0].matched_template_index, 0);
  assert.equal(payload.training_consent, false);
  assert.equal(payload.client_version, "web-v2");
});

test("undone strokes remain in append-only history and server refinement is recorded", () => {
  const history = [createStrokeResult({
    sequence: 0,
    strokeIndex: 0,
    stroke: richStroke,
    diagnosis: diagnosis(),
  })];

  assert.equal(applyServerStrokeResult(history, 0, diagnosis({
    engine: "geometry+stroke-model",
    accepted: false,
    intervention: "pause_and_retry",
    primaryCue: { code: "PATH_DEVIATION", confidence: 0.91 },
  })), true);
  assert.equal(history[0].source, "server");
  assert.equal(history[0].error_code, "PATH_DEVIATION");
  assert.equal(markLastStrokeUndone(history, 0), true);
  assert.equal(history[0].undone, true);
  assert.deepEqual(history[0].stroke, richStroke);
});
