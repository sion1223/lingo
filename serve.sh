#!/bin/bash
# lingo 채점 서버 기동 스크립트 — pod가 켜질 때마다 실행돼도 안전(멱등).
# /workspace 는 볼륨이라 stop/start 후에도 유지된다.
cd /workspace/lingo || exit 1
export HF_HOME=/workspace/hf
export BUILD_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
mkdir -p /workspace/logs

if [ ! -d /workspace/venv ]; then
    python -m venv --system-site-packages /workspace/venv
fi
source /workspace/venv/bin/activate
pip install -q -r requirements-serve.txt >> /workspace/logs/pip.log 2>&1

if ! curl -s -m 2 http://127.0.0.1:8000/health > /dev/null; then
    echo "[serve.sh] starting server $(date)" >> /workspace/logs/server.log
    nohup python -m uvicorn scorer.server:app --host 0.0.0.0 --port 8000 \
        >> /workspace/logs/server.log 2>&1 &
fi
