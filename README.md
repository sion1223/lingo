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
```

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

실시간 로컬 코치의 기준선과 검증 결과는
[docs/REALTIME_TUTOR_BASELINE.md](docs/REALTIME_TUTOR_BASELINE.md)에 기록한다.
