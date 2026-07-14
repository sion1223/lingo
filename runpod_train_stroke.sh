#!/usr/bin/env bash
# 경량 좌표 스트로크 모델: 전체 문자 인식 사전학습 -> 채점 미세조정.
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-/workspace/lingo-stroke-venv}"
if [[ ! -d "$VENV_DIR" ]]; then
  python -m venv --system-site-packages "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
export HF_HOME="${HF_HOME:-/workspace/hf}"
pip install -q -r requirements-runpod.txt

N_CHARS="${N_CHARS:-0}"
MAX_STROKES="${MAX_STROKES:-24}"
WORKERS="${WORKERS:-8}"

python -m scorer.train_recognizer \
  --kanji-dir kanji --n-chars "$N_CHARS" --max-strokes "$MAX_STROKES" \
  --epochs "${RECOGNIZER_EPOCHS:-6}" --batch-size "${RECOGNIZER_BATCH:-256}" \
  --samples-per-char "${RECOGNIZER_SAMPLES:-20}" --workers "$WORKERS" \
  --out checkpoints/stroke_recognizer.pt

python -m scorer.train_scorer \
  --kanji-dir kanji --n-chars "$N_CHARS" --max-strokes "$MAX_STROKES" \
  --epochs "${SCORER_EPOCHS:-10}" --batch-size "${SCORER_BATCH:-256}" \
  --samples-per-char "${SCORER_SAMPLES:-24}" --workers "$WORKERS" \
  --pretrained checkpoints/stroke_recognizer.pt \
  --out checkpoints/stroke_scorer.pt

if [[ "${CALIBRATE:-1}" == "1" && -f checkpoints/chandra_scorer.pt ]]; then
  python -m scorer.calibrate_hybrid \
    --chandra-checkpoint checkpoints/chandra_scorer.pt \
    --stroke-checkpoint checkpoints/stroke_scorer.pt \
    --samples "${CALIBRATION_SAMPLES:-256}" \
    --out checkpoints/hybrid_config.json
fi

echo "stroke training done: checkpoints/stroke_recognizer.pt checkpoints/stroke_scorer.pt"
