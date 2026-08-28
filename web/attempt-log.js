import { clamp } from "./coach/metrics.js";

export const ATTEMPT_CLIENT_VERSION = "web-v2";
export const AUTO_RETRY_MIN_CONFIDENCE = 0.82;

export function cloneStroke(stroke) {
  return stroke.map((point) => (
    Array.isArray(point) ? [...point] : { ...point }
  ));
}

export function createStrokeResult({ sequence, strokeIndex, stroke, diagnosis }) {
  return {
    sequence,
    stroke_index: strokeIndex,
    stroke: cloneStroke(stroke),
    matched_template_index: diagnosis?.matchedTemplateIndex ?? null,
    accepted: Boolean(diagnosis?.accepted),
    error_code: diagnosis?.primaryCue?.code ?? null,
    confidence: clamp(
      diagnosis?.primaryCue?.confidence ?? diagnosis?.matchConfidence ?? 0,
    ),
    intervention: diagnosis?.intervention ?? "silent",
    source: diagnosis?.engine === "geometry-only" ? "local" : "server",
    undone: false,
  };
}

export function applyServerStrokeResult(history, sequence, diagnosis) {
  const result = history.find((entry) => entry.sequence === sequence);
  if (!result || result.undone) return false;
  Object.assign(result, {
    matched_template_index: diagnosis?.matchedTemplateIndex ?? null,
    accepted: Boolean(diagnosis?.accepted),
    error_code: diagnosis?.primaryCue?.code ?? null,
    confidence: clamp(
      diagnosis?.primaryCue?.confidence ?? diagnosis?.matchConfidence ?? 0,
    ),
    intervention: diagnosis?.intervention ?? "silent",
    source: "server",
  });
  return true;
}

export function markLastStrokeUndone(history, strokeIndex) {
  const result = [...history].reverse().find((entry) => (
    !entry.undone && entry.stroke_index === strokeIndex
  ));
  if (!result) return false;
  result.undone = true;
  return true;
}

export function shouldAutoRetryStroke(diagnosis) {
  const confidence = diagnosis?.primaryCue?.confidence
    ?? diagnosis?.matchConfidence
    ?? 0;
  return Boolean(
    diagnosis?.accepted === false
    && diagnosis?.severity === "major"
    && diagnosis?.intervention === "pause_and_retry"
    && confidence >= AUTO_RETRY_MIN_CONFIDENCE
  );
}

export function buildAttemptPayload({
  sessionId,
  attemptId,
  attemptRevision,
  character,
  mode,
  endedReason,
  startedAt,
  endedAt,
  strokes,
  strokeResults,
  finalScore = null,
}) {
  return {
    protocol_version: 1,
    session_id: sessionId,
    attempt_id: attemptId,
    attempt_revision: attemptRevision,
    char: character,
    mode,
    ended_reason: endedReason,
    started_at: startedAt,
    ended_at: endedAt,
    strokes: strokes.map(cloneStroke),
    stroke_results: strokeResults.map((entry) => ({
      ...entry,
      stroke: entry.stroke ? cloneStroke(entry.stroke) : null,
    })),
    final_score: Number.isFinite(finalScore) ? finalScore : null,
    training_consent: false,
    client_version: ATTEMPT_CLIENT_VERSION,
  };
}
