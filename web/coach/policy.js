import { clamp } from "./metrics.js";

export const ERROR_CODES = Object.freeze({
  START_OFFSET: "START_OFFSET",
  END_OFFSET: "END_OFFSET",
  PATH_DEVIATION: "PATH_DEVIATION",
  DIRECTION_REVERSED: "DIRECTION_REVERSED",
  WRONG_ORDER: "WRONG_ORDER",
  EXTRA_STROKE: "EXTRA_STROKE",
  TOO_SHORT: "TOO_SHORT",
  TOO_LONG: "TOO_LONG",
  UNCERTAIN_MATCH: "UNCERTAIN_MATCH",
});

export const DEFAULT_POLICY = Object.freeze({
  startNudge: 0.045,
  startRetry: 0.16,
  endNudge: 0.055,
  endRetry: 0.18,
  pathNudge: 0.048,
  pathRetry: 0.14,
  shortNudge: 0.72,
  shortRetry: 0.48,
  longNudge: 1.34,
  longRetry: 1.72,
});

function confidence(value, nudge, retry) {
  return clamp(0.55 + 0.44 * (value - nudge) / Math.max(retry - nudge, 1e-6));
}

function koreanDirection({ dx, dy }) {
  const horizontal = dx > 0.018 ? "오른쪽" : dx < -0.018 ? "왼쪽" : "";
  const vertical = dy > 0.018 ? "아래" : dy < -0.018 ? "위" : "";
  return [horizontal, vertical].filter(Boolean).join("·") || "표시된 방향";
}

function candidate({ code, priority, confidence: diagnosisConfidence, major, text, anchor, vector }) {
  return {
    code,
    priority,
    confidence: clamp(diagnosisConfidence),
    major,
    text,
    anchor,
    vector,
  };
}

export function selectPrimaryCue(metrics, context = {}, thresholds = DEFAULT_POLICY) {
  if (context.extraStroke) {
    return candidate({
      code: ERROR_CODES.EXTRA_STROKE,
      priority: 110,
      confidence: 0.98,
      major: true,
      text: "예상 획을 모두 썼습니다. 이 획은 지우고 채점을 확인해 보세요.",
      anchor: context.anchor,
    });
  }
  if (context.wrongOrder) {
    return candidate({
      code: ERROR_CODES.WRONG_ORDER,
      priority: 105,
      confidence: context.matchConfidence ?? 0.9,
      major: true,
      text: `${context.expectedTemplateIndex + 1}번 획을 먼저 써 보세요.`,
      anchor: context.anchor,
    });
  }
  if (!metrics) {
    return candidate({
      code: ERROR_CODES.UNCERTAIN_MATCH,
      priority: 1,
      confidence: 0.4,
      major: false,
      text: "가이드를 불러오지 못해 이 획은 모양만 기록했습니다.",
      anchor: context.anchor,
    });
  }

  const cues = [];
  if (metrics.looksReversed || metrics.directionCosine < -0.45) {
    cues.push(candidate({
      code: ERROR_CODES.DIRECTION_REVERSED,
      priority: 100,
      confidence: Math.max(0.86, clamp(-metrics.directionCosine)),
      major: true,
      text: "획의 방향이 반대예요. 반대쪽 끝에서 시작해 다시 써 보세요.",
      anchor: { x: metrics.alignedUser[0][0], y: metrics.alignedUser[0][1] },
      vector: metrics.startVector,
    }));
  }
  if (metrics.startError > thresholds.startNudge) {
    cues.push(candidate({
      code: ERROR_CODES.START_OFFSET,
      priority: 95,
      confidence: confidence(metrics.startError, thresholds.startNudge, thresholds.startRetry),
      major: metrics.startError >= thresholds.startRetry,
      text: `시작점을 ${koreanDirection(metrics.startVector)}으로 옮겨 보세요.`,
      anchor: { x: metrics.alignedUser[0][0], y: metrics.alignedUser[0][1] },
      vector: metrics.startVector,
    }));
  }
  if (metrics.pathError > thresholds.pathNudge) {
    const problemAnchor = metrics.problemSegment.at(
      Math.floor(metrics.problemSegment.length / 2),
    );
    cues.push(candidate({
      code: ERROR_CODES.PATH_DEVIATION,
      priority: 85,
      confidence: confidence(metrics.pathError, thresholds.pathNudge, thresholds.pathRetry),
      major: metrics.pathError >= thresholds.pathRetry,
      text: "강조된 구간을 점선 가이드 쪽으로 붙여 보세요.",
      anchor: problemAnchor ? { x: problemAnchor[0], y: problemAnchor[1] } : undefined,
    }));
  }
  if (metrics.endError > thresholds.endNudge) {
    cues.push(candidate({
      code: ERROR_CODES.END_OFFSET,
      priority: 60,
      confidence: confidence(metrics.endError, thresholds.endNudge, thresholds.endRetry),
      major: metrics.endError >= thresholds.endRetry,
      text: `끝점을 ${koreanDirection(metrics.endVector)}으로 마무리해 보세요.`,
      anchor: { x: metrics.alignedUser.at(-1)[0], y: metrics.alignedUser.at(-1)[1] },
      vector: metrics.endVector,
    }));
  }
  if (metrics.lengthRatio < thresholds.shortNudge) {
    cues.push(candidate({
      code: ERROR_CODES.TOO_SHORT,
      priority: 52,
      confidence: confidence(
        thresholds.shortNudge - metrics.lengthRatio,
        0,
        thresholds.shortNudge - thresholds.shortRetry,
      ),
      major: metrics.lengthRatio <= thresholds.shortRetry,
      text: "획이 짧아요. 가이드의 끝점까지 조금 더 이어 보세요.",
      anchor: { x: metrics.alignedUser.at(-1)[0], y: metrics.alignedUser.at(-1)[1] },
      vector: metrics.endVector,
    }));
  }
  if (metrics.lengthRatio > thresholds.longNudge) {
    cues.push(candidate({
      code: ERROR_CODES.TOO_LONG,
      priority: 52,
      confidence: confidence(
        metrics.lengthRatio - thresholds.longNudge,
        0,
        thresholds.longRetry - thresholds.longNudge,
      ),
      major: metrics.lengthRatio >= thresholds.longRetry,
      text: "획이 길어요. 표시된 끝점에서 조금 일찍 멈춰 보세요.",
      anchor: { x: metrics.alignedUser.at(-1)[0], y: metrics.alignedUser.at(-1)[1] },
      vector: metrics.endVector,
    }));
  }

  cues.sort((left, right) => (
    right.priority * right.confidence - left.priority * left.confidence
  ));
  return cues[0] ?? null;
}

export function cueDecision(primaryCue) {
  if (!primaryCue) {
    return { accepted: true, severity: "none", intervention: "silent" };
  }
  if (primaryCue.major && primaryCue.confidence >= 0.82) {
    return { accepted: false, severity: "major", intervention: "pause_and_retry" };
  }
  return { accepted: true, severity: "minor", intervention: "nudge" };
}
