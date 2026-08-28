import {
  clamp,
  computePartialMetrics,
  computeStrokeMetrics,
  sanitizeStroke,
  toLegacyStroke,
  toLegacyStrokes,
} from "./metrics.js";
import { cueDecision, ERROR_CODES, selectPrimaryCue } from "./policy.js";

function matchCost(metrics, mode = "trace") {
  const directionPenalty = (1 - metrics.directionCosine) * 0.035;
  if (mode === "recall") {
    return metrics.formError + directionPenalty;
  }
  return (
    metrics.pathError
    + metrics.startError * 0.25
    + metrics.endError * 0.1
    + directionPenalty
  );
}

function safeMetrics(stroke, template) {
  try {
    return computeStrokeMetrics(stroke, template);
  } catch (error) {
    if (error instanceof TypeError || error instanceof RangeError) return null;
    throw error;
  }
}

function uncertainDiagnosis(stroke, expectedTemplateIndex) {
  let anchor;
  try {
    const clean = sanitizeStroke(stroke);
    if (clean.length) anchor = { x: clean[0][0], y: clean[0][1] };
  } catch {
    anchor = undefined;
  }
  const primaryCue = selectPrimaryCue(null, { anchor });
  return {
    engine: "geometry-only",
    matchedTemplateIndex: null,
    expectedTemplateIndex,
    matchConfidence: 0,
    ...cueDecision(primaryCue),
    advancesPrefix: false,
    primaryCue,
    metrics: null,
    overlay: {},
    nextAction: { type: "keep_drawing", templateIndex: expectedTemplateIndex, hintLevel: 0 },
  };
}

export function analyzeCompletedStroke({
  stroke,
  templateStrokes,
  expectedTemplateIndex = 0,
  mode = "trace",
}) {
  if (!Array.isArray(templateStrokes) || templateStrokes.length === 0) {
    return uncertainDiagnosis(stroke, expectedTemplateIndex);
  }
  if (expectedTemplateIndex >= templateStrokes.length) {
    let clean = [];
    try {
      clean = sanitizeStroke(stroke);
    } catch {
      // The stable EXTRA_STROKE code is more useful once the template is complete.
    }
    const anchor = clean.length ? { x: clean[0][0], y: clean[0][1] } : undefined;
    const primaryCue = selectPrimaryCue(null, { extraStroke: true, anchor });
    return {
      engine: "geometry-only",
      matchedTemplateIndex: null,
      expectedTemplateIndex,
      matchConfidence: 0.98,
      ...cueDecision(primaryCue),
      advancesPrefix: false,
      primaryCue,
      metrics: null,
      overlay: {},
      nextAction: { type: "retry_current", templateIndex: expectedTemplateIndex, hintLevel: 0 },
    };
  }

  const expectedMetrics = safeMetrics(stroke, templateStrokes[expectedTemplateIndex]);
  if (!expectedMetrics) return uncertainDiagnosis(stroke, expectedTemplateIndex);
  const expectedCost = matchCost(expectedMetrics, mode);
  let matchedTemplateIndex = expectedTemplateIndex;
  let metrics = expectedMetrics;
  let wrongOrder = false;

  if (expectedTemplateIndex + 1 < templateStrokes.length) {
    const nextMetrics = safeMetrics(stroke, templateStrokes[expectedTemplateIndex + 1]);
    if (nextMetrics) {
      const nextCost = matchCost(nextMetrics, mode);
      const positionSupportsNext = mode === "recall" || nextMetrics.startError < 0.12;
      if (nextCost + 0.025 < expectedCost * 0.72 && positionSupportsNext) {
        matchedTemplateIndex = expectedTemplateIndex + 1;
        metrics = nextMetrics;
        wrongOrder = true;
      }
    }
  }

  const matchConfidence = clamp(
    1 - matchCost(metrics, mode) / (mode === "recall" ? 0.28 : 0.22),
  );
  const primaryCue = selectPrimaryCue(metrics, {
    wrongOrder,
    expectedTemplateIndex,
    matchConfidence,
    mode,
    anchor: { x: metrics.alignedUser[0][0], y: metrics.alignedUser[0][1] },
  });
  const decision = cueDecision(primaryCue);
  const accepted = wrongOrder ? false : decision.accepted;
  const nextTemplateIndex = accepted ? expectedTemplateIndex + 1 : expectedTemplateIndex;
  const nextStartPoint = templateStrokes[nextTemplateIndex]?.[0];

  return {
    engine: "geometry-only",
    matchedTemplateIndex,
    expectedTemplateIndex,
    matchConfidence,
    accepted,
    advancesPrefix: accepted,
    severity: wrongOrder ? "major" : decision.severity,
    intervention: wrongOrder ? "pause_and_retry" : decision.intervention,
    primaryCue,
    metrics,
    overlay: {
      problemSegment: mode === "recall"
        ? metrics.formProblemSegment
        : metrics.problemSegment,
      targetSegment: mode === "recall"
        ? metrics.formTargetSegment
        : metrics.targetSegment,
      nextStart: mode !== "recall" && nextStartPoint
        ? { x: nextStartPoint[0], y: nextStartPoint[1] }
        : null,
    },
    nextAction: {
      type: accepted ? (nextStartPoint ? "draw_next" : "complete") : "retry_current",
      templateIndex: nextTemplateIndex,
      hintLevel: 0,
    },
  };
}

export function analyzePartialStroke(
  stroke,
  templateStrokes,
  expectedTemplateIndex,
  { mode = "trace" } = {},
) {
  if (mode === "recall") return null;
  const template = templateStrokes?.[expectedTemplateIndex];
  if (!template) return null;
  try {
    const metrics = computePartialMetrics(stroke, template);
    if (!metrics || metrics.confidence < 0.01) return null;
    return {
      code: ERROR_CODES.PATH_DEVIATION,
      confidence: metrics.confidence,
      overlay: {
        problemSegment: metrics.problemSegment,
        targetSegment: metrics.targetSegment,
      },
    };
  } catch (error) {
    if (error instanceof TypeError || error instanceof RangeError) return null;
    throw error;
  }
}

export { toLegacyStroke, toLegacyStrokes };
