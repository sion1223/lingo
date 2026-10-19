// Which template strokes "한 획 보기" shows in recall mode: the first press shows
// the stroke the learner has to write next, each further press before writing it
// adds the following stroke, and once the learner moves on it starts over there.
export const NO_HINT = Object.freeze({ from: 0, count: 0 });

export function advanceHint(reveal, expectedIndex, strokeCount) {
  if (!(expectedIndex >= 0) || expectedIndex >= strokeCount) return reveal;
  if (reveal.count === 0 || reveal.from !== expectedIndex) {
    return { from: expectedIndex, count: 1 };
  }
  return { from: reveal.from, count: Math.min(reveal.count + 1, strokeCount - reveal.from) };
}

export function hintedStrokeIndexes(reveal, strokeCount) {
  const end = Math.min(reveal.from + reveal.count, strokeCount);
  return Array.from({ length: Math.max(0, end - reveal.from) }, (_, offset) => reveal.from + offset);
}
