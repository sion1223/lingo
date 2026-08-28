const CONFIDENCE_MARGIN = 0.01;

function finite(value) {
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function metricsFromServer(metrics) {
  if (!metrics || typeof metrics !== "object") return null;
  return {
    startError: finite(metrics.start_error),
    endError: finite(metrics.end_error),
    pathError: finite(metrics.path_error),
    shapeError: finite(metrics.shape_error),
    formError: finite(metrics.form_error),
    directionCosine: finite(metrics.direction_cosine),
    lengthRatio: finite(metrics.length_ratio),
    bboxShift: metrics.bbox_shift ?? null,
    scaleRatio: finite(metrics.scale_ratio),
    curvatureHotspot: metrics.curvature_hotspot ?? null,
    modelQuality: finite(metrics.model_quality),
    reverseProbability: finite(metrics.reverse_probability),
    orderErrorProbability: finite(metrics.order_error_probability),
  };
}

export function buildCoachPayload({
  token,
  sessionId,
  attemptId,
  char,
  mode,
  acceptedStrokes,
  currentStroke,
  expectedTemplateIndex,
  localDiagnosis,
}) {
  const pathError = finite(localDiagnosis?.metrics?.pathError);
  const directionCosine = finite(localDiagnosis?.metrics?.directionCosine);
  const clientMetrics = {};
  if (pathError !== null) clientMetrics.path_error = pathError;
  if (directionCosine !== null) clientMetrics.direction_cosine = directionCosine;
  return {
    protocol_version: 1,
    request_id: token.requestId,
    session_id: sessionId,
    attempt_id: attemptId,
    attempt_revision: token.attemptRevision,
    char,
    mode,
    accepted_strokes: acceptedStrokes,
    current_stroke: currentStroke,
    expected_template_index: expectedTemplateIndex,
    client_metrics: Object.keys(clientMetrics).length ? clientMetrics : null,
  };
}

export function isMatchingCoachResponse(body, token) {
  return Boolean(
    body
    && token
    && body.protocol_version === 1
    && body.request_id === token.requestId
    && body.attempt_revision === token.attemptRevision
  );
}

export function normalizeCoachResponse(body) {
  if (!body || body.protocol_version !== 1) {
    throw new TypeError("unsupported coach response");
  }
  const nextAction = body.next_action ?? {};
  const expectedTemplateIndex = Number(body.expected_template_index);
  const nextTemplateIndex = Number(nextAction.template_index);
  const accepted = body.accepted === true;
  return {
    protocolVersion: 1,
    requestId: body.request_id,
    attemptRevision: Number(body.attempt_revision),
    engine: body.engine,
    matchedTemplateIndex: body.matched_template_index ?? null,
    expectedTemplateIndex,
    matchConfidence: finite(body.match_confidence) ?? 0,
    accepted,
    advancesPrefix: Boolean(
      accepted
      && body.matched_template_index === expectedTemplateIndex
      && nextTemplateIndex === expectedTemplateIndex + 1
    ),
    severity: body.severity,
    intervention: body.intervention,
    primaryCue: body.primary_cue ?? null,
    metrics: metricsFromServer(body.metrics),
    overlay: {
      problemSegment: body.overlay?.problem_segment ?? [],
      targetSegment: body.overlay?.target_segment ?? [],
      nextStart: body.overlay?.next_start ?? null,
    },
    nextAction: {
      type: nextAction.type,
      templateIndex: nextTemplateIndex,
      hintLevel: Number(nextAction.hint_level ?? 0),
    },
    latencyMs: finite(body.latency_ms),
  };
}

export function diagnosisConfidence(diagnosis) {
  return finite(diagnosis?.primaryCue?.confidence)
    ?? finite(diagnosis?.matchConfidence)
    ?? 0;
}

export function chooseHigherConfidence(localDiagnosis, serverDiagnosis) {
  if (!serverDiagnosis || !localDiagnosis) return localDiagnosis;
  if (
    serverDiagnosis.expectedTemplateIndex
    !== localDiagnosis.expectedTemplateIndex
  ) {
    return localDiagnosis;
  }
  return diagnosisConfidence(serverDiagnosis)
      > diagnosisConfidence(localDiagnosis) + CONFIDENCE_MARGIN
    ? serverDiagnosis
    : localDiagnosis;
}
