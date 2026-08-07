import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCoachPayload,
  chooseHigherConfidence,
  isMatchingCoachResponse,
  normalizeCoachResponse,
} from "../coach/server-refinement.js";

const token = {
  requestId: "request-7",
  attemptRevision: 4,
};

test("coach payload echoes request identity and converts client metric names", () => {
  const payload = buildCoachPayload({
    token,
    sessionId: "session-1",
    attemptId: "attempt-2",
    char: "永",
    mode: "trace",
    acceptedStrokes: [[{ x: 0.1, y: 0.2, t: 0 }]],
    currentStroke: [{ x: 0.2, y: 0.3, t: 12 }],
    expectedTemplateIndex: 1,
    localDiagnosis: {
      metrics: { pathError: 0.04, directionCosine: 0.9 },
    },
  });

  assert.equal(payload.protocol_version, 1);
  assert.equal(payload.request_id, "request-7");
  assert.equal(payload.attempt_revision, 4);
  assert.equal(payload.expected_template_index, 1);
  assert.deepEqual(payload.current_stroke, [{ x: 0.2, y: 0.3, t: 12 }]);
  assert.deepEqual(payload.client_metrics, {
    path_error: 0.04,
    direction_cosine: 0.9,
  });
});

test("server response normalization produces the browser coach contract", () => {
  const response = normalizeCoachResponse({
    protocol_version: 1,
    request_id: "request-7",
    attempt_revision: 4,
    engine: "geometry+stroke-model",
    matched_template_index: 0,
    expected_template_index: 0,
    match_confidence: 0.93,
    accepted: true,
    severity: "minor",
    intervention: "nudge",
    primary_cue: { code: "END_OFFSET", text: "끝점을 옮겨 보세요.", confidence: 0.91 },
    metrics: { path_error: 0.03, direction_cosine: 0.95 },
    overlay: {
      problem_segment: [[0.2, 0.3], [0.3, 0.4]],
      target_segment: [[0.2, 0.2], [0.3, 0.3]],
      next_start: { x: 0.8, y: 0.2 },
    },
    next_action: { type: "draw_next", template_index: 1, hint_level: 0 },
    latency_ms: 7.2,
  });

  assert.equal(response.requestId, "request-7");
  assert.equal(response.matchConfidence, 0.93);
  assert.equal(response.metrics.pathError, 0.03);
  assert.deepEqual(response.overlay.nextStart, { x: 0.8, y: 0.2 });
  assert.equal(response.nextAction.templateIndex, 1);
  assert.equal(response.advancesPrefix, true);
});

test("only a matching response with materially higher confidence refines local output", () => {
  const local = {
    expectedTemplateIndex: 0,
    matchConfidence: 0.7,
    primaryCue: { confidence: 0.7 },
  };
  const better = {
    expectedTemplateIndex: 0,
    matchConfidence: 0.92,
    primaryCue: { confidence: 0.92 },
  };
  const marginal = { ...better, primaryCue: { confidence: 0.705 } };

  assert.equal(chooseHigherConfidence(local, better), better);
  assert.equal(chooseHigherConfidence(local, marginal), local);
  assert.equal(
    chooseHigherConfidence(local, { ...better, expectedTemplateIndex: 1 }),
    local,
  );
  assert.equal(
    isMatchingCoachResponse(
      { protocol_version: 1, request_id: "request-7", attempt_revision: 4 },
      token,
    ),
    true,
  );
  assert.equal(
    isMatchingCoachResponse(
      { protocol_version: 1, request_id: "old", attempt_revision: 4 },
      token,
    ),
    false,
  );
});

test("a timeout or missing server result preserves the local diagnosis", () => {
  const local = {
    expectedTemplateIndex: 0,
    matchConfidence: 0.82,
    primaryCue: { code: "START_OFFSET", confidence: 0.82 },
  };

  assert.equal(chooseHigherConfidence(local, null), local);
  assert.equal(chooseHigherConfidence(local, undefined), local);
});
