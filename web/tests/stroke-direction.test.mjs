import assert from "node:assert/strict";
import test from "node:test";

import { strokeDirectionMarker } from "../coach/stroke-direction.js";

test("direction marker follows a left-to-right stroke", () => {
  const marker = strokeDirectionMarker([[0.1, 0.4], [0.9, 0.4]]);

  assert.ok(marker);
  assert.ok(marker.tip[0] > marker.tail[0]);
  assert.ok(Math.abs(marker.tip[1] - marker.tail[1]) < 1e-9);
  assert.ok(Math.abs(marker.angle) < 1e-9);
});

test("direction marker reverses with the stroke and accepts rich points", () => {
  const marker = strokeDirectionMarker([
    { x: 0.8, y: 0.2, pressure: 0.4 },
    { x: 0.5, y: 0.4, pressure: 0.5 },
    { x: 0.2, y: 0.6, pressure: 0.6 },
  ]);

  assert.ok(marker);
  assert.ok(marker.tip[0] < marker.tail[0]);
  assert.ok(marker.tip[1] > marker.tail[1]);
  assert.ok(Number.isFinite(marker.angle));
});

test("direction marker ignores a degenerate or invalid stroke", () => {
  assert.equal(strokeDirectionMarker([[0.2, 0.2]]), null);
  assert.equal(strokeDirectionMarker([[0.2, 0.2], [0.2, 0.2]]), null);
  assert.equal(strokeDirectionMarker([[0.2, 0.2], [Number.NaN, 0.3]]), null);
});
