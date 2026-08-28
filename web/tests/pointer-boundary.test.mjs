import assert from "node:assert/strict";
import test from "node:test";

import {
  bindStrokeEndEvents,
  STROKE_END_EVENT_TYPES,
} from "../coach/pointer-boundary.js";

test("mobile pointer capture loss ends the current stroke", () => {
  const target = new EventTarget();
  const ended = [];
  const unbind = bindStrokeEndEvents(target, (event) => ended.push(event.type));

  for (const type of STROKE_END_EVENT_TYPES) {
    target.dispatchEvent(new Event(type));
  }

  assert.deepEqual(ended, [
    "pointerup",
    "pointercancel",
    "lostpointercapture",
  ]);

  unbind();
  target.dispatchEvent(new Event("lostpointercapture"));
  assert.equal(ended.length, 3);
});
