#!/usr/bin/env bash
# RunPod 학습 파이프라인.
# 사용: bash runpod_train.sh            # Chandra 전이학습 (기본)
#       MODE=stroke bash runpod_train.sh  # 경량 스트로크 모델 2단계 학습
set -euo pipefail
cd "$(dirname "$0")"

pip install -q -r requirements-runpod.txt

MODE="${MODE:-chandra}"
N_CHARS="${N_CHARS:-2000}"     # 0 = kanji 폴더 전체(획수 조건 통과분)
EPOCHS="${EPOCHS:-3}"
BATCH="${BATCH:-16}"
WORKERS="${WORKERS:-8}"

if [ "$MODE" = "chandra" ]; then
  # Chandra OCR (datalab-to/chandra) 비전 인코더 전이학습
  python -m scorer.train_chandra \
    --kanji-dir kanji \
    --model-id "${CHANDRA_ID:-datalab-to/chandra}" \
    --n-chars "$N_CHARS" --epochs "$EPOCHS" \
    --batch-size "$BATCH" --workers "$WORKERS" \
    --unfreeze-last "${UNFREEZE_LAST:-0}" \
    --out checkpoints/chandra_scorer.pt
else
  # 경량 스트로크 Transformer: 1단계 인식 사전학습 -> 2단계 채점 전이
  python -m scorer.train_recognizer \
    --kanji-dir kanji --n-chars "$N_CHARS" --epochs "${EPOCHS_STAGE1:-10}" \
    --batch-size 64 --workers "$WORKERS" --max-strokes 24 \
    --out checkpoints/recognizer.pt
  python -m scorer.train_scorer \
    --kanji-dir kanji --n-chars "$N_CHARS" --epochs "$EPOCHS" \
    --batch-size 48 --workers "$WORKERS" --max-strokes 24 \
    --pretrained checkpoints/recognizer.pt \
    --out checkpoints/scorer.pt
fi

echo "done. checkpoints/ 를 다운로드해 로컬 추론에 사용하세요."
