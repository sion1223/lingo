import { performance } from "node:perf_hooks";
import { readFileSync } from "node:fs";

import { analyzeCompletedStroke, analyzePartialStroke } from "./coach/local-matcher.js";

const fixtures = JSON.parse(
  readFileSync(new URL("../tests/fixtures/realtime-strokes.json", import.meta.url), "utf8"),
);

function percentile(values, quantile) {
  const ordered = [...values].sort((left, right) => left - right);
  const position = (ordered.length - 1) * quantile;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  const fraction = position - lower;
  return ordered[lower] * (1 - fraction) + ordered[upper] * fraction;
}

function measure(iterations, callback) {
  const samples = [];
  for (let index = 0; index < iterations; index += 1) {
    const started = performance.now();
    callback(index);
    samples.push(performance.now() - started);
  }
  return {
    samples: iterations,
    p50_ms: Number(percentile(samples, 0.5).toFixed(3)),
    p95_ms: Number(percentile(samples, 0.95).toFixed(3)),
    max_ms: Number(Math.max(...samples).toFixed(3)),
  };
}

const characters = Object.values(fixtures.characters);
const completedCases = ["perfect", "start_offset", "path_deviation", "direction_reversed"];
for (let warmup = 0; warmup < 100; warmup += 1) {
  const fixture = characters[warmup % characters.length];
  analyzeCompletedStroke({
    stroke: fixture.cases.perfect[0],
    templateStrokes: fixture.template,
    expectedTemplateIndex: 0,
  });
}

const pointerUp = measure(2_000, (index) => {
  const fixture = characters[index % characters.length];
  const caseName = completedCases[index % completedCases.length];
  analyzeCompletedStroke({
    stroke: fixture.cases[caseName][0],
    templateStrokes: fixture.template,
    expectedTemplateIndex: 0,
  });
});

const pointerMove = measure(5_000, (index) => {
  const fixture = characters[index % characters.length];
  const stroke = fixture.cases.path_deviation[0];
  const prefixLength = Math.max(3, Math.ceil(stroke.length * 0.7));
  analyzePartialStroke(stroke.slice(0, prefixLength), fixture.template, 0);
});

console.log(JSON.stringify({
  runtime: `${process.release.name} ${process.version}`,
  platform: `${process.platform} ${process.arch}`,
  pointermove: pointerMove,
  pointerup: pointerUp,
}, null, 2));

