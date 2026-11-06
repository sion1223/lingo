// Three-step writing practice and the "practice every character of a sentence"
// queue. Pure data/logic only; app.js owns the canvas and DOM.

export const GUIDE_FULL = "full";
export const GUIDE_NEXT_START = "next-start";
export const GUIDE_HIDDEN = "hidden";

export const FIRST_STAGE = 1;
export const LAST_STAGE = 3;

// coachMode stays "trace"/"recall" so the /score contract and saved attempts
// do not change: stage 2 and 3 are both free-position recall writing.
const STAGES = Object.freeze({
  1: Object.freeze({
    stage: 1,
    coachMode: "trace",
    guide: GUIDE_FULL,
    hintButton: false,
    label: "따라쓰기",
    badge: "1단계: 궤적 따라쓰기",
    startMessage: "선택한 획순 경로를 따라 써 보세요.",
  }),
  2: Object.freeze({
    stage: 2,
    coachMode: "recall",
    guide: GUIDE_NEXT_START,
    hintButton: true,
    label: "힌트쓰기",
    badge: "2단계: 다음 획 시작점만 보기",
    startMessage: "파란 점과 화살표가 다음 획의 시작과 방향이에요. 막히면 한 획 보기를 누르세요.",
  }),
  3: Object.freeze({
    stage: 3,
    coachMode: "recall",
    guide: GUIDE_HIDDEN,
    hintButton: false,
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

// Stage 2's automatic hint: only the start badge and direction arrow of the
// stroke the learner writes next. Null when nothing should be drawn.
export function autoHintStrokeIndex(stage, expectedIndex, strokeCount) {
  if (stageConfig(stage).guide !== GUIDE_NEXT_START) return null;
  if (!Number.isInteger(expectedIndex) || expectedIndex < 0) return null;
  return expectedIndex < strokeCount ? expectedIndex : null;
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
export function nextStepLabel(stage, session) {
  if (session) {
    return hasNextSentenceCharacter(session)
      ? `다음 글자 ${session.characters[session.index + 1]} →`
      : "문장 연습 마치기 ✓";
  }
  const next = nextStageAfter(stage);
  return next ? `${next}단계로 넘어가기 →` : null;
}
