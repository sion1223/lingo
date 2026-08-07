import assert from "node:assert/strict";
import test from "node:test";

import {
  AttemptLifecycle,
  LocalCoachController,
  TUTOR_STATES,
} from "../coach/controller.js";

const template = [[[0.1, 0.1], [0.9, 0.1]]];

test("undo, clear, and character changes invalidate stale requests", () => {
  const lifecycle = new AttemptLifecycle();
  const first = lifecycle.createRequest("score");
  assert.equal(lifecycle.isCurrent(first), true);
  const nextRevision = lifecycle.invalidate();
  assert.equal(nextRevision, 1);
  assert.equal(first.controller.signal.aborted, true);
  assert.equal(lifecycle.isCurrent(first), false);

  const second = lifecycle.createRequest("template");
  assert.equal(second.attemptRevision, 1);
  assert.equal(lifecycle.isCurrent(second), true);
  lifecycle.finish(second);
  assert.equal(lifecycle.isCurrent(second), false);
});

test("state machine accepts a good stroke and undo restores the expected prefix", () => {
  const coach = new LocalCoachController({ templateStrokes: template });
  assert.equal(coach.machine.state, TUTOR_STATES.READY_TO_DRAW);
  assert.equal(coach.beginStroke(), true);
  assert.equal(coach.machine.state, TUTOR_STATES.DRAWING);

  const diagnosis = coach.finishStroke(template[0]);
  assert.equal(diagnosis.accepted, true);
  assert.equal(coach.expectedTemplateIndex, 1);
  assert.equal(coach.machine.state, TUTOR_STATES.READY_NEXT_STROKE);

  const revision = coach.lifecycle.revision;
  coach.undoLast();
  assert.equal(coach.expectedTemplateIndex, 0);
  assert.equal(coach.lifecycle.revision, revision + 1);
  assert.equal(coach.machine.state, TUTOR_STATES.READY_TO_DRAW);
});

test("reset after a character change returns to a clean attempt", () => {
  const coach = new LocalCoachController({ templateStrokes: template });
  coach.beginStroke();
  coach.finishStroke(template[0]);
  coach.reset([[[0.2, 0.2], [0.2, 0.8]]]);
  assert.equal(coach.expectedTemplateIndex, 0);
  assert.equal(coach.results.length, 0);
  assert.equal(coach.machine.state, TUTOR_STATES.READY_TO_DRAW);
});

test("geometry fallback without a template does not invent an accepted prefix", () => {
  const coach = new LocalCoachController({ templateStrokes: null });
  coach.beginStroke();
  const diagnosis = coach.finishStroke([[0.1, 0.1], [0.9, 0.1]]);
  assert.equal(diagnosis.accepted, true);
  assert.equal(diagnosis.advancesPrefix, false);
  assert.equal(coach.expectedTemplateIndex, 0);
});
