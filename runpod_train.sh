#!/usr/bin/env bash
# RunPod 학습 파이프라인 — Chandra 비전 인코더 전이학습.
# 사용: bash runpod_train.sh
set -euo pipefail
cd "$(dirname "$0")"

pip install -q -r requirements-runpod.txt

N_CHARS="${N_CHARS:-2000}"     # 0 = kanji 폴더 전체(획수 조건 통과분)
EPOCHS="${EPOCHS:-3}"
BATCH="${BATCH:-16}"
WORKERS="${WORKERS:-8}"

# GPU 수 자동 감지 -> 2대 이상이면 torchrun(DDP)
GPUS="${GPUS:-$(nvidia-smi -L | wc -l)}"
if [ "$GPUS" -gt 1 ]; then
  LAUNCH="torchrun --nproc_per_node=$GPUS -m"
else
  LAUNCH="python -m"
fi

# Chandra OCR (datalab-to/chandra) 비전 인코더 전이학습
$LAUNCH scorer.train_chandra \
  --kanji-dir kanji \
  --model-id "${CHANDRA_ID:-datalab-to/chandra}" \
  --n-chars "$N_CHARS" --epochs "$EPOCHS" \
  --batch-size "$BATCH" --workers "$WORKERS" \
  --unfreeze-last "${UNFREEZE_LAST:-0}" \
  --out checkpoints/chandra_scorer.pt

echo "done. checkpoints/ 를 다운로드해 로컬 추론에 사용하세요."
