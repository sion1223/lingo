// Three-step writing practice and the "practice every character of a sentence"
// queue. Pure data/logic only; app.js owns the canvas and DOM.

export const GUIDE_FULL = "full";
export const GUIDE_MASKED = "masked";
export const GUIDE_HIDDEN = "hidden";

export const FIRST_STAGE = 1;
export const LAST_STAGE = 3;

// Stage 2 shows a random part of the strokes; each mask must be scored at
// least STAGE2_PASS_SCORE before the learner moves on.
export const STAGE2_PASS_SCORE = 80;
export const STAGE2_MASK_ROUNDS = 3;
export const MASK_HIDDEN_RATIO = 0.5;
const MASK_REROLL_ATTEMPTS = 6;

// coachMode stays "trace"/"recall" so the /score contract and saved attempts
// do not change: stage 2 and 3 are both free-position recall writing.
const STAGES = Object.freeze({
  1: Object.freeze({
    stage: 1,
    coachMode: "trace",
    guide: GUIDE_FULL,
    hintButton: false,
    passScore: null,
    label: "따라쓰기",
    badge: "1단계: 궤적 따라쓰기",
    startMessage: "선택한 획순 경로를 따라 써 보세요.",
  }),
  2: Object.freeze({
    stage: 2,
    coachMode: "recall",
    guide: GUIDE_MASKED,
    hintButton: true,
    passScore: STAGE2_PASS_SCORE,
    label: "힌트쓰기",
    badge: `2단계: 일부 획 가리기 · ${STAGE2_PASS_SCORE}점 통과`,
    startMessage: `몇 획은 가려져 있어요. 채점에서 ${STAGE2_PASS_SCORE}점 이상이면 다음으로 넘어가요.`,
  }),
  3: Object.freeze({
    stage: 3,
    coachMode: "recall",
    guide: GUIDE_HIDDEN,
    hintButton: false,
    passScore: null,
    label: "가리기",
    badge: "3단계: 가이드 없이 쓰기",
    startMessage: "가이드 없이 기억만으로 써 보세요. 다 쓰면 예시와 비교해 보여 드려요.",
  }),
});

export function normalizeStage(value) {
  const stage = Number(value);
  return Number.isInteger(stage) && stage >= FIRST_STAGE && stage <= LAST_STAGE
    ? stage
    : FIRST_STAGE;
}

export function stageConfig(stage) {
  return STAGES[normalizeStage(stage)];
}

export function nextStageAfter(stage) {
  const current = normalizeStage(stage);
  return current < LAST_STAGE ? current + 1 : null;
}

export function passesStage(stage, score) {
  const { passScore } = stageConfig(stage);
  if (passScore == null) return true;
  const value = Number(score);
  return score != null && Number.isFinite(value) && value >= passScore;
}

// Sorted indexes of the strokes left visible. At least one stroke is hidden,
// and at least one stays visible when there are two or more strokes.
export function randomStrokeMask(strokeCount, random = Math.random, avoid = null) {
  const count = Math.max(0, Math.floor(Number(strokeCount) || 0));
  if (count <= 1) return [];
  const hidden = Math.min(count - 1, Math.max(1, Math.round(count * MASK_HIDDEN_RATIO)));
  const avoidKey = avoid ? avoid.join(",") : null;
  let visible = [];
  for (let attempt = 0; attempt < MASK_REROLL_ATTEMPTS; attempt += 1) {
    const order = Array.from({ length: count }, (_value, index) => index);
    for (let index = order.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(random() * (index + 1));
      [order[index], order[swap]] = [order[swap], order[index]];
    }
    visible = order.slice(hidden).sort((a, b) => a - b);
    if (visible.join(",") !== avoidKey) break;
  }
  return visible;
}

export function startMaskRounds(strokeCount, random = Math.random) {
  return Object.freeze({
    round: 0,
    total: STAGE2_MASK_ROUNDS,
    strokeCount,
    visible: Object.freeze(randomStrokeMask(strokeCount, random)),
  });
}

export function hasNextMaskRound(rounds) {
  return Boolean(rounds && rounds.round + 1 < rounds.total);
}

export function nextMaskRound(rounds, random = Math.random) {
  if (!hasNextMaskRound(rounds)) return null;
  return Object.freeze({
    ...rounds,
    round: rounds.round + 1,
    visible: Object.freeze(randomStrokeMask(rounds.strokeCount, random, rounds.visible)),
  });
}

// Which written stroke a score correction points at, so the learner can go
// back and rewrite from there. Null for whole-character feedback.
export function rewindStrokeIndex(correction, strokeCount) {
  const raw = correction?.error_code === "MISSING_STROKE"
    ? correction.template_index
    : correction?.index;
  if (!Number.isInteger(raw) || raw < 0) return null;
  return Math.min(raw, Math.max(0, strokeCount));
}

export function startSentencePractice(characters, stage, entryId = null) {
  const queue = [...new Set(characters ?? [])].filter(Boolean);
  if (queue.length === 0) return null;
  return Object.freeze({
    characters: Object.freeze(queue),
    stage: normalizeStage(stage),
    index: 0,
    entryId,
  });
}

export function sentenceCharacter(session) {
  return session ? session.characters[session.index] ?? null : null;
}

export function hasNextSentenceCharacter(session) {
  return Boolean(session && session.index + 1 < session.characters.length);
}

export function advanceSentence(session) {
  if (!hasNextSentenceCharacter(session)) return null;
  return Object.freeze({ ...session, index: session.index + 1 });
}

export function withSentenceStage(session, stage) {
  return session ? Object.freeze({ ...session, stage: normalizeStage(stage) }) : null;
}

export function sentenceProgressLabel(session) {
  if (!session) return "";
  return `문장 연습 ${session.index + 1}/${session.characters.length} · ${stageConfig(session.stage).label}`;
}

// Label of the button shown after a character is complete; null hides it.
export function nextStepLabel(stage, session, maskRounds = null) {
  if (stageConfig(stage).guide === GUIDE_MASKED && hasNextMaskRound(maskRounds)) {
    return `다음 마스크 ${maskRounds.round + 2}/${maskRounds.total} →`;
  }
  if (session) {
    return hasNextSentenceCharacter(session)
      ? `다음 글자 ${session.characters[session.index + 1]} →`
      : "문장 연습 마치기 ✓";
  }
  const next = nextStageAfter(stage);
  return next ? `${next}단계로 넘어가기 →` : null;
}
