export const STROKE_END_EVENT_TYPES = Object.freeze([
  "pointerup",
  "pointercancel",
  "lostpointercapture",
]);

export function bindStrokeEndEvents(target, listener) {
  for (const type of STROKE_END_EVENT_TYPES) {
    target.addEventListener(type, listener);
  }
  return () => {
    for (const type of STROKE_END_EVENT_TYPES) {
      target.removeEventListener(type, listener);
    }
  };
}
