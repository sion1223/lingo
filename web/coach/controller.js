import { analyzeCompletedStroke, analyzePartialStroke } from "./local-matcher.js";

export const TUTOR_STATES = Object.freeze({
  IDLE: "IDLE",
  READY_TO_DRAW: "READY_TO_DRAW",
  DRAWING: "DRAWING",
  LOCAL_REVIEW: "LOCAL_REVIEW",
  WAITING_SERVER_REFINEMENT: "WAITING_SERVER_REFINEMENT",
  READY_NEXT_STROKE: "READY_NEXT_STROKE",
  RETRY_CURRENT_STROKE: "RETRY_CURRENT_STROKE",
  FINALIZING: "FINALIZING",
  SUMMARY: "SUMMARY",
});

const DRAWABLE_STATES = new Set([
  TUTOR_STATES.READY_TO_DRAW,
  TUTOR_STATES.READY_NEXT_STROKE,
  TUTOR_STATES.RETRY_CURRENT_STROKE,
  TUTOR_STATES.SUMMARY,
]);

function requestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class AttemptLifecycle {
  constructor() {
    this.revision = 0;
    this.requests = new Map();
  }

  invalidate() {
    this.revision += 1;
    for (const request of this.requests.values()) request.controller.abort();
    this.requests.clear();
    return this.revision;
  }

  createRequest(kind) {
    const token = {
      kind,
      requestId: requestId(),
      attemptRevision: this.revision,
      controller: new AbortController(),
    };
    this.requests.set(token.requestId, token);
    return token;
  }

  abortKind(kind) {
    let aborted = 0;
    for (const [id, request] of this.requests) {
      if (request.kind !== kind) continue;
      request.controller.abort();
      this.requests.delete(id);
      aborted += 1;
    }
    return aborted;
  }

  isCurrent(token) {
    return Boolean(
      token
      && token.attemptRevision === this.revision
      && !token.controller.signal.aborted
      && this.requests.get(token.requestId) === token,
    );
  }

  finish(token) {
    if (token) this.requests.delete(token.requestId);
  }
}

export class TutorStateMachine {
  constructor(onChange = () => {}) {
    this.state = TUTOR_STATES.IDLE;
    this.onChange = onChange;
  }

  set(nextState) {
    const previous = this.state;
    this.state = nextState;
    if (previous !== nextState) this.onChange(nextState, previous);
  }

  ready() {
    this.set(TUTOR_STATES.READY_TO_DRAW);
  }

  beginStroke() {
    if (!DRAWABLE_STATES.has(this.state)) return false;
    this.set(TUTOR_STATES.DRAWING);
    return true;
  }

  review() {
    if (this.state !== TUTOR_STATES.DRAWING) return false;
    this.set(TUTOR_STATES.LOCAL_REVIEW);
    return true;
  }

  resolve(accepted) {
    if (this.state !== TUTOR_STATES.LOCAL_REVIEW) return false;
    this.set(
      accepted ? TUTOR_STATES.READY_NEXT_STROKE : TUTOR_STATES.RETRY_CURRENT_STROKE,
    );
    return true;
  }

  finalizing() {
    this.set(TUTOR_STATES.FINALIZING);
  }

  summary() {
    this.set(TUTOR_STATES.SUMMARY);
  }
}

export class WarningHysteresis {
  constructor({ startConfidence = 0.85, releaseConfidence = 0.6, holdMs = 150, cooldownMs = 600 } = {}) {
    this.settings = { startConfidence, releaseConfidence, holdMs, cooldownMs };
    this.reset();
  }

  reset() {
    this.active = null;
    this.candidate = null;
    this.candidateSince = 0;
    this.lastHiddenAt = Number.NEGATIVE_INFINITY;
  }

  update(diagnosis, now = performance.now()) {
    const code = diagnosis?.code ?? null;
    const confidence = diagnosis?.confidence ?? 0;
    if (this.active) {
      if (code === this.active.code && confidence >= this.settings.releaseConfidence) {
        this.active = diagnosis;
        return this.active;
      }
      if (confidence < this.settings.releaseConfidence || code !== this.active.code) {
        this.active = null;
        this.lastHiddenAt = now;
      }
    }

    if (!code || confidence < this.settings.startConfidence) {
      this.candidate = null;
      return null;
    }
    if (now - this.lastHiddenAt < this.settings.cooldownMs) return null;
    if (this.candidate?.code !== code) {
      this.candidate = diagnosis;
      this.candidateSince = now;
      return null;
    }
    this.candidate = diagnosis;
    if (now - this.candidateSince >= this.settings.holdMs) {
      this.active = diagnosis;
      this.candidate = null;
      return this.active;
    }
    return null;
  }
}

export class LocalCoachController {
  constructor({ templateStrokes = null, mode = "trace", onStateChange } = {}) {
    this.lifecycle = new AttemptLifecycle();
    this.machine = new TutorStateMachine(onStateChange);
    this.hysteresis = new WarningHysteresis();
    this.templateStrokes = templateStrokes;
    this.mode = mode === "recall" ? "recall" : "trace";
    this.expectedTemplateIndex = 0;
    this.results = [];
    this.machine.ready();
  }

  reset(templateStrokes = this.templateStrokes) {
    this.lifecycle.invalidate();
    this.templateStrokes = templateStrokes;
    this.expectedTemplateIndex = 0;
    this.results = [];
    this.hysteresis.reset();
    this.machine.ready();
  }

  replaceTemplate(templateStrokes) {
    this.templateStrokes = templateStrokes;
  }

  setMode(mode) {
    this.mode = mode === "recall" ? "recall" : "trace";
    this.hysteresis.reset();
  }

  beginStroke() {
    this.hysteresis.reset();
    return this.machine.beginStroke();
  }

  analyzePartial(stroke, now = performance.now()) {
    if (this.machine.state !== TUTOR_STATES.DRAWING) return null;
    const diagnosis = analyzePartialStroke(
      stroke,
      this.templateStrokes,
      this.expectedTemplateIndex,
      { mode: this.mode },
    );
    return this.hysteresis.update(diagnosis, now);
  }

  finishStroke(stroke) {
    if (!this.machine.review()) return null;
    const diagnosis = analyzeCompletedStroke({
      stroke,
      templateStrokes: this.templateStrokes,
      expectedTemplateIndex: this.expectedTemplateIndex,
      mode: this.mode,
    });
    if (diagnosis.advancesPrefix) this.expectedTemplateIndex += 1;
    this.results.push(diagnosis);
    this.hysteresis.reset();
    this.machine.resolve(diagnosis.accepted);
    return diagnosis;
  }

  applyServerRefinement(token, localDiagnosis, refinedDiagnosis) {
    if (!this.lifecycle.isCurrent(token)) return false;
    const index = this.results.length - 1;
    if (
      index < 0
      || this.results[index] !== localDiagnosis
      || refinedDiagnosis?.expectedTemplateIndex
        !== localDiagnosis.expectedTemplateIndex
    ) {
      return false;
    }
    if (localDiagnosis.advancesPrefix && !refinedDiagnosis.advancesPrefix) {
      this.expectedTemplateIndex = Math.max(0, this.expectedTemplateIndex - 1);
    } else if (!localDiagnosis.advancesPrefix && refinedDiagnosis.advancesPrefix) {
      this.expectedTemplateIndex += 1;
    }
    this.results[index] = refinedDiagnosis;
    this.machine.set(
      refinedDiagnosis.accepted
        ? TUTOR_STATES.READY_NEXT_STROKE
        : TUTOR_STATES.RETRY_CURRENT_STROKE,
    );
    return true;
  }

  undoLast() {
    this.lifecycle.invalidate();
    const removed = this.results.pop();
    if (removed?.advancesPrefix) {
      this.expectedTemplateIndex = Math.max(0, this.expectedTemplateIndex - 1);
    }
    this.hysteresis.reset();
    this.machine.ready();
    return removed;
  }

  beginFinalScore() {
    return this.lifecycle.createRequest("score");
  }

  finishFinalScore(token, succeeded) {
    if (!this.lifecycle.isCurrent(token)) return false;
    this.lifecycle.finish(token);
    if (succeeded) this.machine.summary();
    else this.machine.ready();
    return true;
  }
}
