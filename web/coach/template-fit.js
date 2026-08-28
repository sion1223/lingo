import { toXY } from "./metrics.js";

const MIN_DIAGONAL = 1e-6;

function cloneTemplate(templateStrokes) {
  return templateStrokes.map((stroke) => stroke.map((point) => [...toXY(point)]));
}

function strokePoints(strokes) {
  return strokes.flatMap((stroke) => stroke.map((point) => toXY(point)));
}

function bounds(points) {
  if (points.length === 0) return null;
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const [x, y] of points) {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }
  return {
    center: [(minX + maxX) / 2, (minY + maxY) / 2],
    diagonal: Math.hypot(maxX - minX, maxY - minY),
  };
}

export function fitTemplateToWriting(templateStrokes, userStrokes) {
  if (!Array.isArray(templateStrokes) || templateStrokes.length === 0) return [];
  if (!Array.isArray(userStrokes) || userStrokes.length === 0) {
    return cloneTemplate(templateStrokes);
  }
  const referenceCount = Math.min(templateStrokes.length, userStrokes.length);
  const templateBounds = bounds(strokePoints(templateStrokes.slice(0, referenceCount)));
  const userBounds = bounds(strokePoints(userStrokes));
  if (!templateBounds || !userBounds) return cloneTemplate(templateStrokes);
  const scale = (
    templateBounds.diagonal >= MIN_DIAGONAL
    && userBounds.diagonal >= MIN_DIAGONAL
  )
    ? userBounds.diagonal / templateBounds.diagonal
    : 1;
  return templateStrokes.map((stroke) => stroke.map((point) => {
    const [x, y] = toXY(point);
    return [
      userBounds.center[0] + (x - templateBounds.center[0]) * scale,
      userBounds.center[1] + (y - templateBounds.center[1]) * scale,
    ];
  }));
}
