import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceSentence,
  autoHintStrokeIndex,
  GUIDE_FULL,
  GUIDE_HIDDEN,
  GUIDE_NEXT_START,
  nextStageAfter,
  nextStepLabel,
  normalizeStage,
  sentenceCharacter,
  sentenceProgressLabel,
  stageConfig,
  startSentencePractice,
  withSentenceStage,
} from "../practice-stages.js";

test("three stages go from full guide to a slight hint to fully hidden", () => {
  assert.equal(stageConfig(1).guide, GUIDE_FULL);
  assert.equal(stageConfig(2).guide, GUIDE_NEXT_START);
  assert.equal(stageConfig(3).guide, GUIDE_HIDDEN);
  assert.equal(stageConfig(2).hintButton, true);
  assert.equal(stageConfig(3).hintButton, false);
});

test("stages keep the trace/recall score contract", () => {
  assert.deepEqual([1, 2, 3].map((stage) => stageConfig(stage).coachMode), ["trace", "recall", "recall"]);
});

test("unknown stage values fall back to stage 1", () => {
  assert.equal(normalizeStage("3"), 3);
  assert.equal(normalizeStage(0), 1);
  assert.equal(normalizeStage(4), 1);
  assert.equal(normalizeStage(undefined), 1);
});

test("next stage stops after stage 3", () => {
  assert.equal(nextStageAfter(1), 2);
  assert.equal(nextStageAfter(2), 3);
  assert.equal(nextStageAfter(3), null);
});

test("stage 2 hints only the stroke written next", () => {
  assert.equal(autoHintStrokeIndex(2, 0, 5), 0);
  assert.equal(autoHintStrokeIndex(2, 3, 5), 3);
  assert.equal(autoHintStrokeIndex(2, 5, 5), null);
  assert.equal(autoHintStrokeIndex(1, 0, 5), null);
  assert.equal(autoHintStrokeIndex(3, 0, 5), null);
});

test("sentence practice walks each character once at the chosen stage", () => {
  let session = startSentencePractice(["食", "べ", "食", "る"], 3, "entry-1");
  assert.deepEqual(session.characters, ["食", "べ", "る"]);
  assert.equal(session.stage, 3);
  assert.equal(sentenceCharacter(session), "食");
  assert.equal(sentenceProgressLabel(session), "문장 연습 1/3 · 가리기");
  assert.equal(nextStepLabel(3, session), "다음 글자 べ →");

  session = advanceSentence(advanceSentence(session));
  assert.equal(sentenceCharacter(session), "る");
  assert.equal(nextStepLabel(3, session), "문장 연습 마치기 ✓");
  assert.equal(advanceSentence(session), null);
});

test("changing the stage mid-sentence keeps the position", () => {
  const session = withSentenceStage(advanceSentence(startSentencePractice(["日", "本"], 1)), 2);
  assert.equal(session.stage, 2);
  assert.equal(sentenceCharacter(session), "本");
});

test("an empty sentence does not start a session", () => {
  assert.equal(startSentencePractice([], 1), null);
  assert.equal(nextStepLabel(3, null), null);
  assert.equal(nextStepLabel(1, null), "2단계로 넘어가기 →");
});
