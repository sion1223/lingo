import assert from "node:assert/strict";
import test from "node:test";

import { advanceHint, hintedStrokeIndexes, NO_HINT } from "../hint-reveal.js";

test("one-stroke hint starts at the stroke the learner writes next", () => {
  let reveal = advanceHint(NO_HINT, 2, 5);
  assert.deepEqual(hintedStrokeIndexes(reveal, 5), [2]);

  reveal = advanceHint(reveal, 2, 5);
  assert.deepEqual(hintedStrokeIndexes(reveal, 5), [2, 3]);

  // After writing strokes 3 and 4 the next press starts over at stroke 5.
  reveal = advanceHint(reveal, 4, 5);
  assert.deepEqual(hintedStrokeIndexes(reveal, 5), [4]);
  assert.deepEqual(hintedStrokeIndexes(advanceHint(reveal, 4, 5), 5), [4]);
});

test("one-stroke hint does nothing once every stroke is written", () => {
  assert.deepEqual(advanceHint(NO_HINT, 5, 5), NO_HINT);
  assert.deepEqual(hintedStrokeIndexes(NO_HINT, 5), []);
});
