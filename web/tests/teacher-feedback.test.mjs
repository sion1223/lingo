import assert from "node:assert/strict";
import test from "node:test";

import { AttemptLifecycle } from "../coach/controller.js";
import {
  buildTeacherFeedbackRequest,
  isCurrentTeacherContext,
  localTeacherFallback,
  normalizeTeacherEnvelope,
  tryBuildTeacherFeedbackRequest,
} from "../coach/teacher-feedback.js";

function diagnosis(overrides = {}) {
  return {
    accepted: false,
    severity: "major",
    matchConfidence: 0.93,
    expectedTemplateIndex: 1,
    nextAction: { type: "retry_current", templateIndex: 1, hintLevel: 0 },
    primaryCue: {
      code: "TOO_LONG",
      confidence: 0.94,
      text: "획이 길어요. 표시된 끝점에서 조금 일찍 멈춰 보세요.",
    },
    ...overrides,
  };
}

test("teacher request contains only versioned structured evidence", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-1",
    diagnosis: diagnosis({
      evidenceCodes: ["TOO_LONG", "STROKE_TOO_VERTICAL"],
      nearestCompetitor: "り",
      targetMargin: -0.56,
      targetFeatureProfile: {
        relative_length: "shorter_than_observed",
        start_height: "middle",
      },
      observedFeatureProfile: {
        relative_length: "too_long",
        start_height: "high",
      },
      strokes: "raw-strokes-must-not-leak",
      image: "raw-image-must-not-leak",
      userId: "private-user-must-not-leak",
    }),
    targetChar: "い",
    nearestCompetitor: "り",
    mode: "recall",
    totalStrokes: 2,
    attemptNumber: 3,
    sameErrorCount: 2,
  });

  assert.equal(request.schema_version, "teacher_feedback.v1");
  assert.deepEqual(Object.keys(request), [
    "schema_version",
    "locale",
    "learner",
    "task",
    "locked_decision",
    "evidence",
    "teaching_policy",
  ]);
  assert.deepEqual(request.task, {
    target_char: "い",
    nearest_competitor: "り",
    mode: "recall",
    critical_stroke: 1,
    total_strokes: 2,
  });
  assert.equal(request.locked_decision.next_action, "RETRY_CRITICAL_STROKE");
  assert.equal(request.evidence.critical_region, "stroke_2");
  assert.equal(request.evidence.target_margin, -0.56);
  assert.equal(request.evidence.target_feature_profile.relative_length, "shorter_than_observed");
  assert.equal(request.evidence.target_feature_profile.start_height, "middle");

  const serialized = JSON.stringify(request);
  for (const forbidden of [
    "raw-strokes-must-not-leak",
    "raw-image-must-not-leak",
    "private-user-must-not-leak",
    "session_id",
    "attempt_id",
    "pressure",
  ]) {
    assert.equal(serialized.includes(forbidden), false, forbidden);
  }
});

test("optional teacher evidence fields remain explicit nulls", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-2",
    diagnosis: diagnosis({ targetMargin: null }),
    targetChar: "永",
    totalStrokes: 5,
  });
  assert.equal(request.task.nearest_competitor, null);
  assert.equal(request.evidence.target_margin, null);
});

test("teacher request makes acceptance and locked action coherent", () => {
  const rejected = buildTeacherFeedbackRequest({
    decisionId: "decision-rejected-action",
    diagnosis: diagnosis({ nextAction: null }),
    targetChar: "永",
    totalStrokes: 5,
  });
  const accepted = buildTeacherFeedbackRequest({
    decisionId: "decision-accepted-action",
    diagnosis: diagnosis({ accepted: true }),
    targetChar: "永",
    totalStrokes: 5,
  });

  assert.equal(rejected.locked_decision.next_action, "RETRY_CRITICAL_STROKE");
  assert.equal(accepted.locked_decision.next_action, "KEEP_DRAWING");
});

test("teacher task preserves one supplementary Unicode character", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-unicode",
    diagnosis: diagnosis(),
    targetChar: "𠮷",
    totalStrokes: 6,
  });
  assert.equal(request.task.target_char, "𠮷");
  assert.equal([...request.task.target_char].length, 1);
});

test("teacher task rejects characters outside the kana and CJK domain", () => {
  assert.throws(
    () => buildTeacherFeedbackRequest({
      decisionId: "decision-hangul",
      diagnosis: diagnosis(),
      targetChar: "가",
      totalStrokes: 2,
    }),
    /kana or CJK/,
  );
  assert.throws(
    () => buildTeacherFeedbackRequest({
      decisionId: "decision-punctuation",
      diagnosis: diagnosis(),
      targetChar: "い",
      nearestCompetitor: "?",
      totalStrokes: 2,
    }),
    /kana or CJK/,
  );
  assert.equal(
    tryBuildTeacherFeedbackRequest({
      decisionId: "decision-served-punctuation",
      diagnosis: diagnosis(),
      targetChar: "。",
      totalStrokes: 1,
    }),
    null,
  );
});

test("an extra stroke does not create an out-of-range critical stroke", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-extra",
    diagnosis: diagnosis({
      expectedTemplateIndex: 2,
      primaryCue: {
        code: "EXTRA_STROKE",
        confidence: 0.98,
        text: "이 획은 지우고 채점을 확인해 보세요.",
      },
    }),
    targetChar: "い",
    totalStrokes: 2,
  });
  assert.equal(request.task.critical_stroke, null);
  assert.equal(request.evidence.critical_region, null);
});

test("teacher envelope must preserve all locked response fields", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-3",
    diagnosis: diagnosis(),
    targetChar: "永",
    totalStrokes: 5,
  });
  const feedback = {
    schema_version: "teacher_feedback.v1",
    decision_id: "decision-3",
    error_code: "TOO_LONG",
    next_action: "RETRY_CRITICAL_STROKE",
    strategy: "direct_correction",
    primary_text: "이 획을 조금 짧게 써 보세요.",
    secondary_text: "표시된 끝점에서 멈추면 됩니다.",
    spoken_text: "이 획을 조금 짧게 써 보세요.",
    emphasis_target: "critical_stroke",
  };

  assert.equal(
    normalizeTeacherEnvelope({ feedback, source: "luna" }, request).source,
    "luna",
  );
  for (const changed of [
    { decision_id: "old-decision" },
    { error_code: "TOO_SHORT" },
    { next_action: "DRAW_NEXT_STROKE" },
    { strategy: "unapproved_strategy" },
    { spoken_text: "" },
    { emphasis_target: "unapproved_target" },
  ]) {
    assert.throws(
      () => normalizeTeacherEnvelope({ feedback: { ...feedback, ...changed }, source: "luna" }, request),
      /invalid or unlocked/,
    );
  }
  assert.throws(
    () => normalizeTeacherEnvelope({ feedback, source: "unknown" }, request),
    /invalid or unlocked/,
  );
  assert.throws(
    () => normalizeTeacherEnvelope({
      feedback: { ...feedback, primary_text: "가".repeat(101), secondary_text: "" },
      source: "luna",
    }, request),
    /invalid or unlocked/,
  );
});

test("teacher output length uses Unicode code points like the backend", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-output-unicode",
    diagnosis: diagnosis(),
    targetChar: "𠮷",
    totalStrokes: 5,
  });
  request.teaching_policy.max_characters = 45;
  const primaryText = `𠀀${"가".repeat(44)}`;
  const feedback = {
    schema_version: "teacher_feedback.v1",
    decision_id: request.locked_decision.decision_id,
    error_code: request.locked_decision.error_code,
    next_action: request.locked_decision.next_action,
    strategy: "direct_correction",
    primary_text: primaryText,
    secondary_text: "",
    spoken_text: primaryText,
    emphasis_target: "critical_stroke",
  };

  const normalized = normalizeTeacherEnvelope(
    { feedback, source: "luna" },
    request,
  );

  assert.equal([...normalized.feedback.primary_text].length, 45);
  assert.equal(normalized.feedback.primary_text.length, 46);
});

test("deterministic local fallback preserves locked fields", () => {
  const request = buildTeacherFeedbackRequest({
    decisionId: "decision-4",
    diagnosis: diagnosis(),
    targetChar: "永",
    totalStrokes: 5,
  });
  const fallback = localTeacherFallback(request, "이 획을 조금 짧게 써 보세요.");
  assert.equal(fallback.source, "fallback");
  assert.equal(fallback.feedback.decision_id, request.locked_decision.decision_id);
  assert.equal(fallback.feedback.error_code, request.locked_decision.error_code);
  assert.equal(fallback.feedback.next_action, request.locked_decision.next_action);
});

test("revision and decision identity prevent stale teacher feedback from rendering", () => {
  const lifecycle = new AttemptLifecycle();
  const token = lifecycle.createRequest("verbalize");
  const context = { request: { locked_decision: { decision_id: "decision-current" } } };
  assert.equal(
    isCurrentTeacherContext(lifecycle, token, "decision-current", context),
    true,
  );
  assert.equal(
    isCurrentTeacherContext(
      lifecycle,
      token,
      "decision-current",
      { request: { locked_decision: { decision_id: "decision-new" } } },
    ),
    false,
  );
  lifecycle.invalidate();
  assert.equal(token.controller.signal.aborted, true);
  assert.equal(
    isCurrentTeacherContext(lifecycle, token, "decision-current", context),
    false,
  );
});
