import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceSentence,
  GUIDE_FULL,
  GUIDE_HIDDEN,
  GUIDE_MASKED,
  nextMaskRound,
  nextStageAfter,
  nextStepLabel,
  normalizeStage,
  passesStage,
  randomStrokeMask,
  rewindStrokeIndex,
  sentenceCharacter,
  sentenceProgressLabel,
  STAGE2_MASK_ROUNDS,
  STAGE2_PASS_SCORE,
  stageConfig,
  startMaskRounds,
  startSentencePractice,
  withSentenceStage,
} from "../practice-stages.js";

function sequence(...values) {
  let index = 0;
  return () => values[index++ % values.length];
}

test("three stages go from full guide to random masks to fully hidden", () => {
  assert.equal(stageConfig(1).guide, GUIDE_FULL);
  assert.equal(stageConfig(2).guide, GUIDE_MASKED);
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

test("only stage 2 needs a passing score", () => {
  assert.equal(passesStage(2, STAGE2_PASS_SCORE), true);
  assert.equal(passesStage(2, STAGE2_PASS_SCORE - 0.1), false);
  assert.equal(passesStage(2, null), false);
  assert.equal(passesStage(2, "92.5"), true);
  assert.equal(passesStage(1, 10), true);
  assert.equal(passesStage(3, null), true);
});

test("a mask hides about half the strokes but always leaves some visible", () => {
  for (const count of [2, 3, 5, 10]) {
    const visible = randomStrokeMask(count, Math.random);
    assert.ok(visible.length >= 1, `count ${count}`);
    assert.ok(visible.length < count, `count ${count}`);
    assert.deepEqual(visible, [...visible].sort((a, b) => a - b));
    assert.ok(visible.every((index) => index >= 0 && index < count));
  }
  assert.equal(randomStrokeMask(10, Math.random).length, 5);
  assert.deepEqual(randomStrokeMask(1), []);
  assert.deepEqual(randomStrokeMask(0), []);
});

test("the next mask differs from the previous one when possible", () => {
  const random = sequence(0, 0, 0, 0.99, 0.5, 0.1);
  let rounds = startMaskRounds(4, random);
  assert.equal(rounds.round, 0);
  assert.equal(rounds.total, STAGE2_MASK_ROUNDS);
  for (let round = 1; round < STAGE2_MASK_ROUNDS; round += 1) {
    const next = nextMaskRound(rounds, random);
    assert.equal(next.round, round);
    assert.notDeepEqual(next.visible, rounds.visible);
    rounds = next;
  }
  assert.equal(nextMaskRound(rounds, random), null);
});

test("next-step label walks masks, then sentence characters, then stages", () => {
  const rounds = startMaskRounds(4, Math.random);
  assert.equal(nextStepLabel(2, null, rounds), `다음 마스크 2/${STAGE2_MASK_ROUNDS} →`);
  let last = rounds;
  while (nextMaskRound(last)) last = nextMaskRound(last);
  assert.equal(nextStepLabel(2, null, last), "3단계로 넘어가기 →");
  // Mask rounds only matter in stage 2.
  assert.equal(nextStepLabel(1, null, rounds), "2단계로 넘어가기 →");
  assert.equal(nextStepLabel(3, null, null), null);
});

test("score feedback points back at the stroke to rewrite", () => {
  assert.equal(rewindStrokeIndex({ index: 2, error_code: "DIRECTION" }, 5), 2);
  assert.equal(rewindStrokeIndex({ error_code: "MISSING_STROKE", index: -1, template_index: 3 }, 3), 3);
  assert.equal(rewindStrokeIndex({ error_code: "MISSING_STROKE", template_index: 7 }, 4), 4);
  assert.equal(rewindStrokeIndex({ error_code: "POSITION_OFFSET", index: -1 }, 4), null);
  assert.equal(rewindStrokeIndex(null, 4), null);
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
