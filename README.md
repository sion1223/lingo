# 링고 (lingo) — 한자 손글씨 채점

한자 획순/필적을 채점하는 하이브리드 모델(Chandra 비전 + 스트로크 Transformer)과
서빙 스택(RunPod GPU + Supabase 프록시) 코드.

## 문서

- [docs/SERVE.md](docs/SERVE.md) — 서비스 켜기/끄기, 자동 기동 원리, 운영 가이드
- [docs/RUNPOD.md](docs/RUNPOD.md) — RunPod에서 모델 학습 실행하기
- [docs/TODO.md](docs/TODO.md) — 개선 계획

## 폴더 구조

- `scorer/` — 채점 모델 코드 (FastAPI 서버, 학습, 추론) — pod의 `/workspace/lingo/scorer`로 그대로 서빙됨
- `web/` — 아이패드용 그리기 UI (Supabase 배포)
- `kanji/` — KanjiVG SVG 템플릿 데이터
- `checkpoints/` — 학습된 모델 체크포인트
- `docs/` — 운영/학습 문서
- `scripts/` — 로컬 실행 스크립트 (RunPod 세션 켜기/끄기, 데이터셋 파싱, HF 업로드)
- `archive/` — 더 이상 쓰지 않는 옛 코드 (`legacy_stroke_model/`)

`serve.sh`, `jupyter_server_config.py`, `requirements-serve.txt`, `requirements-runpod.txt`,
`runpod_train*.sh`는 RunPod pod의 `/workspace/lingo/` 경로를 그대로 가정하므로 루트에 남겨둠.

## 로컬에서 서버 켜고 끄기

`run_lingo.bat` 더블클릭 (자세한 내용은 [docs/SERVE.md](docs/SERVE.md) 참고).

## 개발·검증

```bash
python -m pip install -r requirements-dev.txt
python scripts/generate_realtime_fixtures.py
python -m pytest -q
node --test web/tests/*.test.mjs
node web/benchmark-local.mjs
python -m scorer.benchmark_realtime --engine geometry-only
python -m scorer.benchmark_realtime --engine geometry+stroke-model
python scripts/validate_runpod.py --help
python scripts/validate_teacher_feedback.py --cases 1000
```

유사 문자 C0 도구는 사람 검토가 없는 morph 샘플을 강한 문자 라벨로 만들지 않고
`ambiguous`로 분리한다. 로컬 CPU에서는 geometry와 경량 stroke checkpoint까지만 측정하며,
Chandra/hybrid 최종 근거는 실제 CUDA RunPod에서 같은 SHA로 다시 측정한다.

```bash
python -m scorer.build_confusion_graph \
  --scope kana --top-k 10 \
  --output artifacts/confusion_graph_kana_seed_v1.json
python -m scorer.evaluate_confusions \
  --backends template_geometry,stroke \
  --output artifacts/confusion_baseline_local.json

# 새 RunPod에서 exact pushed SHA와 checkpoint를 확인한 뒤 실행
python -m scorer.evaluate_confusions \
  --backends stroke,chandra,hybrid --strict-backends \
  --output artifacts/confusion_baseline_runpod.json
```

현재 로컬 기준선과 미실행 gate는
[docs/validation/CONFUSION_BASELINE_20260809.md](docs/validation/CONFUSION_BASELINE_20260809.md)에 기록한다.

로컬 서버는 저장소 루트에서 다음처럼 실행한다.

```bash
python -m uvicorn scorer.server:app --host 127.0.0.1 --port 8000
```

웹앱은 기본적으로 동일 출처의 `/health`, `/template/{char}`, `/coach/stroke`, `/score`를 사용한다.
다른 배포를 연결할 때는 HTML의 빈 `lingo-api-*` meta 값 또는 모듈 로드 전에
`window.LINGO_CONFIG = { apiBaseUrl, edgeEndpoint, apiKey }`를 런타임에서 주입한다.
배포 URL과 키는 소스에 새로 하드코딩하지 않는다.

서버 배포 시 `serve.sh`가 현재 checkout의 SHA를 `BUILD_SHA`로 주입한다. 코치만 의도적으로
기하 모드로 검증할 때는 서버 시작 전에 `COACH_ENGINE=geometry-only`를 설정한다. 기본값 `auto`는
경량 체크포인트를 사용하고, 체크포인트가 없거나 손상되면 자동으로 geometry-only로 강등한다.

Supabase `score` 함수에는 `RUNPOD_BASE_URL`, 안정적인 `lingo` 진입점에는 Pod와 무관한 정적
`web/` 배포 주소인 `LINGO_STATIC_APP_URL`을 secret/env로 설정한다. 실제 RunPod 검증 결과는
원시 응답 대신 `scripts/validate_runpod.py`가 만든 집계 JSON을 바탕으로 문서화한다.

## GPT-5.6 Luna 교사 피드백

`POST /coach/verbalize`와 `POST /coach/summary`는 채점 결과를 새로 판단하지 않고,
`teacher_feedback.v1`의 잠긴 판정과 구조화 evidence만 사용한다. 서버가 근거별로 만든 유한한
안전 문구 후보 중에서 Luna가 학습자 맥락과 호출 목적에 맞는 전략을 고르고, 후보를 섞거나
재작성한 출력은 semantic validator가 폐기한다. 기본 모델은 정확히 `gpt-5.6-luna`이며 API 오류,
timeout, refusal, 스키마·의미 검증 실패 시 기존 채점과 필기 흐름을 막지 않고 결정론적 문구로
강등된다.

로컬에서는 저장소 루트의 추적되지 않는 `.env.local`에 `OPENAI_API_KEY`를 둔다. 키는 서버에서만
읽으며 HTML, 브라우저 설정, Edge 요청 본문에는 넣지 않는다. 실제 API까지 검증하려면 다음을 실행한다.

```bash
python -m pip install -r requirements-serve.txt
python scripts/validate_teacher_feedback.py --cases 1000 --live
```

웹의 `AI 선생님 설명 · 왜?` 버튼은 사용자가 명시적으로 선택했을 때만 이 경로를 호출한다.
원시 획 좌표, 필기 이미지, 필압 시계열, 세션·사용자 식별자는 Luna 요청에 포함하지 않는다.
v1의 문자 범위는 kana/CJK ideograph이며, 그 밖의 radical·문장부호는 기존 로컬 코치만 유지한다.
원격 운영에서는 Edge의 `TEACHER_BASE_URL`을 별도 CPU FastAPI에 연결할 수 있으므로 심층
`/score`용 RunPod가 없어도 Luna 설명은 동작한다. 공개 배포 시 Edge와 FastAPI에 같은
`TEACHER_API_TOKEN`을 설정해야 한다. 삭제된 기존 Pod 대신 심층 `/score`를 다시 쓰려면 새
RunPod 배포가 별도로 필요하다.

실시간 로컬 코치의 기준선과 검증 결과는
[docs/REALTIME_TUTOR_BASELINE.md](docs/REALTIME_TUTOR_BASELINE.md)에 기록한다.
