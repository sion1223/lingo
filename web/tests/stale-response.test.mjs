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

test("starting a new stroke can abort only outstanding coach refinements", () => {
  const lifecycle = new AttemptLifecycle();
  const coachRequest = lifecycle.createRequest("coach");
  const templateRequest = lifecycle.createRequest("template");

  assert.equal(lifecycle.abortKind("coach"), 1);
  assert.equal(coachRequest.controller.signal.aborted, true);
  assert.equal(lifecycle.isCurrent(coachRequest), false);
  assert.equal(lifecycle.isCurrent(templateRequest), true);
});

test("a new diagnosis can abort only an outstanding teacher explanation", () => {
  const lifecycle = new AttemptLifecycle();
  const teacherRequest = lifecycle.createRequest("verbalize");
  const scoreRequest = lifecycle.createRequest("score");

  assert.equal(lifecycle.abortKind("verbalize"), 1);
  assert.equal(teacherRequest.controller.signal.aborted, true);
  assert.equal(lifecycle.isCurrent(teacherRequest), false);
  assert.equal(lifecycle.isCurrent(scoreRequest), true);
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

test("a stale server refinement cannot rewrite the accepted prefix", () => {
  const coach = new LocalCoachController({ templateStrokes: template });
  coach.beginStroke();
  const local = coach.finishStroke(template[0]);
  const token = coach.lifecycle.createRequest("coach");
  const refined = {
    ...local,
    accepted: false,
    advancesPrefix: false,
    nextAction: { type: "retry_current", templateIndex: 0, hintLevel: 0 },
  };

  coach.lifecycle.abortKind("coach");
  assert.equal(coach.applyServerRefinement(token, local, refined), false);
  assert.equal(coach.results.at(-1), local);
  assert.equal(coach.expectedTemplateIndex, 1);
});

test("a current server refinement can correct the last local decision", () => {
  const coach = new LocalCoachController({ templateStrokes: template });
  coach.beginStroke();
  const local = coach.finishStroke(template[0]);
  const token = coach.lifecycle.createRequest("coach");
  const refined = {
    ...local,
    accepted: false,
    advancesPrefix: false,
    nextAction: { type: "retry_current", templateIndex: 0, hintLevel: 0 },
  };

  assert.equal(coach.applyServerRefinement(token, local, refined), true);
  assert.equal(coach.results.at(-1), refined);
  assert.equal(coach.expectedTemplateIndex, 0);
  assert.equal(coach.machine.state, TUTOR_STATES.RETRY_CURRENT_STROKE);
});

test("final scoring stays non-blocking and a new stroke can cancel it", () => {
  const coach = new LocalCoachController({ templateStrokes: template });
  const initialState = coach.machine.state;
  const scoreRequest = coach.beginFinalScore();

  assert.equal(coach.machine.state, initialState);
  assert.equal(coach.beginStroke(), true);
  assert.equal(coach.lifecycle.abortKind("score"), 1);
  assert.equal(scoreRequest.controller.signal.aborted, true);
  assert.equal(coach.lifecycle.isCurrent(scoreRequest), false);
});
