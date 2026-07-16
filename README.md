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
