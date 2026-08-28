import assert from "node:assert/strict";
import test from "node:test";

import { fitTemplateToWriting } from "../coach/template-fit.js";

const template = [
  [[0.1, 0.2], [0.7, 0.2]],
  [[0.7, 0.2], [0.7, 0.8]],
];

function transform(strokes, scale, dx, dy) {
  return strokes.map((stroke) => stroke.map(([x, y]) => ({
    x: x * scale + dx,
    y: y * scale + dy,
    pressure: 0.5,
  })));
}

test("completed reference is translated and uniformly scaled onto the user's writing", () => {
  const user = transform(template, 0.55, 0.31, 0.12);
  const fitted = fitTemplateToWriting(template, user);

  for (let strokeIndex = 0; strokeIndex < template.length; strokeIndex += 1) {
    for (let pointIndex = 0; pointIndex < template[strokeIndex].length; pointIndex += 1) {
      assert.deepEqual(
        fitted[strokeIndex][pointIndex].map((value) => Number(value.toFixed(8))),
        [user[strokeIndex][pointIndex].x, user[strokeIndex][pointIndex].y]
          .map((value) => Number(value.toFixed(8))),
      );
    }
  }
});

test("partial writing anchors the remaining example strokes to the written prefix", () => {
  const userFirstStroke = transform([template[0]], 0.7, 0.2, 0.35);
  const fitted = fitTemplateToWriting(template, userFirstStroke);

  assert.deepEqual(
    fitted[0].map((point) => point.map((value) => Number(value.toFixed(8)))),
    userFirstStroke[0].map(({ x, y }) => [x, y].map((value) => Number(value.toFixed(8)))),
  );
  assert.ok(fitted[1][0][0] > 0.6);
  assert.ok(fitted[1][0][1] > 0.45);
});
