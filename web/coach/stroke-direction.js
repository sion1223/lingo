import { clamp, distance, toXY } from "./metrics.js";

const EPSILON = 1e-7;

export function strokeDirectionMarker(points, fraction = 0.62) {
  if (!Array.isArray(points) || points.length < 2) return null;
  const clean = [];
  try {
    for (const point of points) {
      const next = toXY(point);
      if (!clean.length || distance(clean.at(-1), next) > EPSILON) clean.push(next);
    }
  } catch {
    return null;
  }
  if (clean.length < 2) return null;

  const lengths = [];
  let total = 0;
  for (let index = 1; index < clean.length; index += 1) {
    const length = distance(clean[index - 1], clean[index]);
    lengths.push(length);
    total += length;
  }
  if (total < EPSILON) return null;

  const target = total * clamp(Number(fraction), 0.15, 0.9);
  let traversed = 0;
  let segmentIndex = 0;
  for (; segmentIndex < lengths.length - 1; segmentIndex += 1) {
    if (traversed + lengths[segmentIndex] >= target) break;
    traversed += lengths[segmentIndex];
  }
  const start = clean[segmentIndex];
  const end = clean[segmentIndex + 1];
  const segmentLength = Math.max(lengths[segmentIndex], EPSILON);
  const ratio = clamp((target - traversed) / segmentLength);
  const tip = [
    start[0] + (end[0] - start[0]) * ratio,
    start[1] + (end[1] - start[1]) * ratio,
  ];
  const unit = [
    (end[0] - start[0]) / segmentLength,
    (end[1] - start[1]) / segmentLength,
  ];
  const markerLength = Math.min(0.055, Math.max(0.012, total * 0.22), total * 0.45);
  const tail = [
    tip[0] - unit[0] * markerLength,
    tip[1] - unit[1] * markerLength,
  ];
  return {
    tail,
    tip,
    angle: Math.atan2(unit[1], unit[0]),
  };
}
