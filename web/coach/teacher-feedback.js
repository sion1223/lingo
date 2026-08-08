export const TEACHER_FEEDBACK_SCHEMA_VERSION = "teacher_feedback.v1";

export const TEACHER_STRATEGIES = Object.freeze([
  "direct_correction",
  "brief_contrast",
  "micro_drill",
]);

const NEXT_ACTIONS = Object.freeze({
  retry_current: "RETRY_CRITICAL_STROKE",
  draw_next: "DRAW_NEXT_STROKE",
  complete: "COMPLETE_CHARACTER",
  keep_drawing: "KEEP_DRAWING",
});

const PROFILE_KEYS = new Set([
  "primary_direction",
  "relative_length",
  "start_height",
  "end_height",
  "start_position",
  "end_position",
  "path_shape",
  "stroke_order",
  "stroke_count",
]);
const EMPHASIS_TARGETS = new Set([
  "critical_stroke",
  "target_char",
  "competitor",
  "next_action",
  "whole_character",
  "none",
]);
const STABLE_CODE = /^[A-Z][A-Z0-9_]{0,63}$/;
const SUPPORTED_TEACHER_CHARACTER = /^[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u{20000}-\u{323af}]$/u;

function finiteConfidence(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(1, Math.max(0, number));
}

function stableStringList(values, fallback) {
  const source = Array.isArray(values) ? values : [];
  const cleaned = source
    .filter((value) => typeof value === "string" && STABLE_CODE.test(value));
  return [...new Set(cleaned.length ? cleaned : [fallback])].slice(0, 32);
}

function safeProfile(profile) {
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) return {};
  const result = {};
  for (const [key, value] of Object.entries(profile)) {
    if (
      PROFILE_KEYS.has(key)
      && typeof value === "string"
      && value.length > 0
    ) {
      result[key] = value.slice(0, 64);
    }
  }
  return result;
}

function adjustmentDirection(vector) {
  if (!vector || typeof vector !== "object") return null;
  const dx = Number(vector.dx);
  const dy = Number(vector.dy);
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return null;
  const horizontal = dx > 0.018 ? "right" : dx < -0.018 ? "left" : "same_x";
  const vertical = dy > 0.018 ? "down" : dy < -0.018 ? "up" : "same_y";
  return `${horizontal}_${vertical}`;
}

function profilesForDiagnosis(diagnosis) {
  const suppliedTarget = safeProfile(diagnosis?.targetFeatureProfile);
  const suppliedObserved = safeProfile(diagnosis?.observedFeatureProfile);
  if (Object.keys(suppliedTarget).length || Object.keys(suppliedObserved).length) {
    return {
      target_feature_profile: suppliedTarget,
      observed_feature_profile: suppliedObserved,
    };
  }

  const code = diagnosis?.primaryCue?.code;
  const vectorDirection = adjustmentDirection(diagnosis?.primaryCue?.vector);
  const target = {};
  const observed = {};
  if (code === "TOO_SHORT") {
    target.relative_length = "longer_than_observed";
    observed.relative_length = "too_short";
  } else if (code === "TOO_LONG") {
    target.relative_length = "shorter_than_observed";
    observed.relative_length = "too_long";
  } else if (code === "DIRECTION_REVERSED") {
    target.primary_direction = "opposite_of_observed";
    observed.primary_direction = "reversed";
  } else if (code === "START_OFFSET") {
    target.start_position = vectorDirection || "template_start";
    observed.start_position = "offset_from_template";
  } else if (code === "END_OFFSET") {
    target.end_position = vectorDirection || "template_end";
    observed.end_position = "offset_from_template";
  } else if (code === "PATH_DEVIATION") {
    target.path_shape = "template_path";
    observed.path_shape = "deviates_from_template";
  } else if (code === "WRONG_ORDER") {
    target.stroke_order = "expected_stroke_first";
    observed.stroke_order = "different_stroke_first";
  } else if (code === "EXTRA_STROKE") {
    target.stroke_count = "template_complete";
    observed.stroke_count = "extra_stroke";
  }
  return {
    target_feature_profile: target,
    observed_feature_profile: observed,
  };
}

function nextActionCode(diagnosis) {
  const supplied = NEXT_ACTIONS[diagnosis?.nextAction?.type];
  if (diagnosis?.accepted === true) {
    return supplied && supplied !== "RETRY_CRITICAL_STROKE"
      ? supplied
      : "KEEP_DRAWING";
  }
  return "RETRY_CRITICAL_STROKE";
}

function criticalStrokeIndex(diagnosis) {
  const rawSupplied = diagnosis?.criticalStroke;
  const supplied = Number(rawSupplied);
  if (
    rawSupplied !== null
    && rawSupplied !== undefined
    && Number.isInteger(supplied)
    && supplied >= 0
  ) return supplied;
  const expected = Number(diagnosis?.expectedTemplateIndex);
  return Number.isInteger(expected) && expected >= 0 ? expected : null;
}

function firstUnicodeCharacter(value) {
  return typeof value === "string" ? [...value.trim()][0] ?? "" : "";
}

/**
 * Builds the versioned teacher contract from a diagnosis only. It intentionally
 * has no stroke, image, pressure, session, attempt-id, or user-id parameter.
 */
export function buildTeacherFeedbackRequest({
  decisionId,
  diagnosis,
  targetChar,
  nearestCompetitor = null,
  mode = "trace",
  totalStrokes,
  attemptNumber = 1,
  sameErrorCount = 1,
}) {
  const errorCode = diagnosis?.primaryCue?.code;
  if (typeof errorCode !== "string" || !STABLE_CODE.test(errorCode)) {
    throw new TypeError("an actionable diagnosis is required");
  }
  if (typeof decisionId !== "string" || !decisionId) {
    throw new TypeError("decisionId is required");
  }
  const normalizedTargetChar = firstUnicodeCharacter(targetChar);
  if (!SUPPORTED_TEACHER_CHARACTER.test(normalizedTargetChar)) {
    throw new TypeError("targetChar must be one kana or CJK ideograph");
  }

  const normalizedTotalStrokes = Math.max(
    1,
    Math.min(64, Number.isInteger(totalStrokes) ? totalStrokes : 1),
  );
  const candidateCriticalStroke = criticalStrokeIndex(diagnosis);
  const criticalStroke = candidateCriticalStroke !== null
    && candidateCriticalStroke < normalizedTotalStrokes
    ? candidateCriticalStroke
    : null;
  const task = {
    target_char: normalizedTargetChar,
    nearest_competitor: null,
    mode: mode === "recall" ? "recall" : "trace",
    critical_stroke: criticalStroke,
    total_strokes: normalizedTotalStrokes,
  };
  const normalizedCompetitor = firstUnicodeCharacter(nearestCompetitor);
  if (
    normalizedCompetitor
    && !SUPPORTED_TEACHER_CHARACTER.test(normalizedCompetitor)
  ) {
    throw new TypeError("nearestCompetitor must be one kana or CJK ideograph");
  }
  if (normalizedCompetitor && normalizedCompetitor !== task.target_char) {
    task.nearest_competitor = normalizedCompetitor;
  }

  const evidence = {
    target_margin: null,
    critical_region: criticalStroke === null ? null : `stroke_${criticalStroke + 1}`,
    ...profilesForDiagnosis(diagnosis),
  };
  const rawTargetMargin = diagnosis?.targetMargin;
  const targetMargin = Number(rawTargetMargin);
  if (
    rawTargetMargin !== null
    && rawTargetMargin !== undefined
    && Number.isFinite(targetMargin)
  ) evidence.target_margin = targetMargin;

  return {
    schema_version: TEACHER_FEEDBACK_SCHEMA_VERSION,
    locale: "ko",
    learner: {
      level: "beginner",
      attempt_number: Math.min(
        10_000,
        Math.max(1, Math.trunc(Number(attemptNumber) || 1)),
      ),
      same_error_count: Math.min(
        10_000,
        Math.max(1, Math.trunc(Number(sameErrorCount) || 1)),
      ),
      preferred_length: "short",
    },
    task,
    locked_decision: {
      decision_id: decisionId,
      error_code: errorCode,
      evidence_codes: stableStringList(diagnosis?.evidenceCodes, errorCode),
      severity: diagnosis?.severity === "major" ? "major" : "minor",
      confidence: finiteConfidence(
        diagnosis?.primaryCue?.confidence ?? diagnosis?.matchConfidence,
      ),
      accepted: diagnosis?.accepted === true,
      next_action: nextActionCode(diagnosis),
    },
    evidence,
    teaching_policy: {
      allowed_strategies: [...TEACHER_STRATEGIES],
      max_sentences: 2,
      max_characters: 100,
      must_preserve_locked_fields: true,
      forbidden: [
        "change_diagnosis",
        "invent_score",
        "invent_evidence",
        "give_multiple_actions",
      ],
    },
  };
}

export function tryBuildTeacherFeedbackRequest(options) {
  try {
    return buildTeacherFeedbackRequest(options);
  } catch (error) {
    if (error instanceof TypeError) return null;
    throw error;
  }
}

function validText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function codePointLength(value) {
  return [...value].length;
}

export function normalizeTeacherEnvelope(body, request) {
  const feedback = body?.feedback;
  const locked = request?.locked_decision;
  const allowedSources = ["luna", "cache", "fallback"];
  const primaryText = typeof feedback?.primary_text === "string"
    ? feedback.primary_text.trim()
    : "";
  const secondaryText = typeof feedback?.secondary_text === "string"
    ? feedback.secondary_text.trim()
    : "";
  const spokenText = typeof feedback?.spoken_text === "string"
    ? feedback.spoken_text.trim()
    : "";
  if (
    !feedback
    || !allowedSources.includes(body?.source)
    || feedback.schema_version !== TEACHER_FEEDBACK_SCHEMA_VERSION
    || feedback.decision_id !== locked?.decision_id
    || feedback.error_code !== locked?.error_code
    || feedback.next_action !== locked?.next_action
    || !request.teaching_policy.allowed_strategies.includes(feedback.strategy)
    || !validText(primaryText)
    || !validText(spokenText)
    || !EMPHASIS_TARGETS.has(feedback.emphasis_target)
    || codePointLength(primaryText) + codePointLength(secondaryText)
      > request.teaching_policy.max_characters
    || codePointLength(spokenText) > request.teaching_policy.max_characters
  ) {
    throw new TypeError("invalid or unlocked teacher feedback response");
  }

  return {
    source: body.source,
    feedback: {
      ...feedback,
      primary_text: primaryText,
      secondary_text: secondaryText,
      spoken_text: spokenText,
    },
  };
}

export function localTeacherFallback(request, primaryText) {
  const locked = request.locked_decision;
  const fallbackText = validText(primaryText)
    ? primaryText.trim()
    : "표시된 획을 가이드에 맞춰 한 번 더 써 보세요.";
  return {
    source: "fallback",
    feedback: {
      schema_version: TEACHER_FEEDBACK_SCHEMA_VERSION,
      decision_id: locked.decision_id,
      error_code: locked.error_code,
      next_action: locked.next_action,
      strategy: "direct_correction",
      primary_text: fallbackText,
      secondary_text: "",
      spoken_text: fallbackText,
      emphasis_target: "critical_stroke",
    },
  };
}

export function isCurrentTeacherContext(lifecycle, token, requestedDecisionId, currentContext) {
  return Boolean(
    lifecycle?.isCurrent(token)
    && requestedDecisionId
    && currentContext?.request?.locked_decision?.decision_id === requestedDecisionId
  );
}
