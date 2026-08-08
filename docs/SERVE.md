# 링고 채점 서비스 운영 가이드

## 구성

```
아이패드(애플펜슬) ──> 항상 켜진 web/ 정적 배포 ──> Supabase score 함수
                     (로컬 코치, Pod 독립)       ├─ RUNPOD_BASE_URL ──> RunPod GPU `/coach`, `/score`
                                └─ 사용자가 `왜?` 선택
                                                  └─ TEACHER_BASE_URL ──> CPU FastAPI ──> GPT-5.6 Luna
```

- **웹앱**: `web/` 디렉터리를 항상 켜진 정적 호스트에 한 묶음으로 배포한다. Supabase의
  안정적인 `lingo` 진입점은 `LINGO_STATIC_APP_URL` 환경변수의 주소로만 리다이렉트하며 HTML을
  별도로 복사해 갖지 않는다. 따라서 Pod가 Stop이어도 앱 셸과 내장 핵심 5문자 코치는 열린다.
- **RunPod pod**: 기존 `lingo-scorer` ID `l8faq6mx5shxpc`는 2026-08-08 조회 시 계정에 존재하지
  않는다. 심층 `/score`를 다시 운영하려면 새 Pod와 볼륨을 만들고 `RUNPOD_BASE_URL` 및 세션
  스크립트의 Pod ID를 새 값으로 갱신해야 한다.
- **채점 서버 주소**: Supabase `score` 함수의 `RUNPOD_BASE_URL` 환경변수로만 주입한다.
- **제출 기록**: Supabase `submissions` 테이블 (문자, 획, 점수, 리포트 자동 저장)

## 배포 환경변수

| 위치 | 이름 | 용도 |
|---|---|---|
| Supabase `score` 함수 | `RUNPOD_BASE_URL` | RunPod 포트 8000 proxy origin |
| Supabase `score` 함수 | `TEACHER_BASE_URL` | 선택 사항. 별도 teacher FastAPI origin; 미설정 시 `RUNPOD_BASE_URL` 사용 |
| Supabase `score` 함수 + FastAPI 서버 | `TEACHER_API_TOKEN` | 두 위치에 같은 임의 secret 설정; 브라우저에는 노출 금지 |
| Supabase `lingo` 함수 | `LINGO_STATIC_APP_URL` | 항상 켜진 정적 `web/` 배포 주소 |
| RunPod 서버 | `BUILD_SHA` | `/health.build_sha`; `serve.sh`가 현재 HEAD로 자동 설정 |
| RunPod 서버 | `COACH_ENGINE` | `auto`(기본) 또는 의도적 `geometry-only` 검증 |
| FastAPI 서버 | `OPENAI_API_KEY` | GPT-5.6 Luna 서버 전용 비밀키; 브라우저에 노출 금지 |
| FastAPI 서버 | `OPENAI_TEACHER_TIMEOUT_SECONDS` | 교사 renderer timeout, 기본 8초 |
| FastAPI 서버 | `TEACHER_FEEDBACK_CACHE_SIZE` | 검증된 문장 LRU 크기, 기본 128 |
| FastAPI 서버 | `TEACHER_MAX_CONCURRENCY` | 프로세스당 Luna 동시 호출 상한, 기본 4(1~32) |
| 별도 teacher FastAPI | `LINGO_SERVICE_MODE=teacher-only` | coach/deep scorer preload를 건너뛰고 `/health`를 teacher 기준으로 응답 |

정적 HTML에는 배포 과정에서 `window.LINGO_CONFIG = { edgeEndpoint, apiKey }` 또는 동등한
meta 설정을 주입한다. service role key는 브라우저에 넣지 않는다.

`/coach/verbalize`와 `/coach/summary`는 GPU 채점 모델을 로드하지 않는다. 별도 CPU 배포에서는
`LINGO_SERVICE_MODE=teacher-only`를 설정하고 Edge의 `TEACHER_BASE_URL`을 그 origin으로 지정한다.
`TEACHER_BASE_URL`이 없을 때만 기존 `RUNPOD_BASE_URL`을 함께 사용한다. 따라서 삭제된 GPU Pod를
재생성하지 않아도 Luna 설명만 독립 운영할 수 있다. 반대로 심층 `/score`는 `RUNPOD_BASE_URL`의
새 채점 배포가 있어야 한다.

브라우저는 provider를 기다리는 동안 기본 설명을 즉시 표시하고, 검증된 Luna 응답이 오면 교체한다.
현재 Edge/browser hard timeout은 각각 25초/28초이며 FastAPI의 기본 8초는 SDK의 개별
connect/read/write phase timeout이다. 전체 wall time은 더 길 수 있으므로 바깥 hard timeout을 더
크게 둔다.

공개 배포에서는 `TEACHER_API_TOKEN`을 반드시 설정하고, Supabase/API gateway에서 사용자별
호출 제한·일일 예산도 별도로 건다. 서버의 `TEACHER_MAX_CONCURRENCY`는 순간 부하와 threadpool
고갈을 막지만 누적 API 비용 한도는 아니다. 이번 작업에서는 공개 origin이나 유료 RunPod를
생성하지 않았다.

## RunPod 켜기 / 끄기

현재 계정에는 기존 `lingo-scorer` Pod가 남아 있지 않으므로 아래 절차는 새 Pod를 만든 뒤 새 ID와
주소를 설정한 경우에만 적용된다. Stop/볼륨 과금은 선택한 GPU와 현재 RunPod 요금을 확인한다.

- **Windows 자동 실행(권장)**: `run_lingo.bat` 더블클릭. Pod를 켜고 서버가
  준비될 때까지 기다린다. 사용을 마친 뒤 Enter/Ctrl+C를 누르거나 창을
  닫으면 Pod도 자동으로 Stop된다. 첫 실행 때만 RunPod API key를 입력하며,
  Windows DPAPI로 현재 사용자에게만 복호화되게 저장된다. 키를 바꾸려면
  `run_lingo.bat -ResetKey`를 실행한다. Pod가 이미 켜져 있으면 현재 로컬 창이
  제어를 인계하고, 창을 닫을 때 함께 Stop한다.
- **끄기**: RunPod 콘솔(https://console.runpod.io/pods) → 새 scorer Pod → Stop
- **켜기**: 같은 화면에서 Start → 준비 상태와 `/health` 확인
- Claude에게 "채점 서버 켜줘/꺼줘"라고 해도 됨 (runpod MCP 연결됨)
- 웹앱 상단 상태표시등: 초록=온라인, 노랑=모델 로드 중, 빨강=꺼짐

pod를 **Stop**하면 GPU 과금이 멈추고 /workspace 볼륨(코드·체크포인트·모델캐시)은
유지된다. **Terminate**는 볼륨까지 삭제되므로 주의.

## 자동 기동 원리

pod env `JUPYTER_CONFIG_DIR=/workspace/.jupyter` 때문에 부팅 때마다 Jupyter가
`/workspace/.jupyter/jupyter_server_config.py`를 실행 →
`/workspace/lingo/serve.sh` → venv 활성화 후 uvicorn(포트 8000) 기동.
`serve.sh`는 uvicorn 전에 checkout의 정확한 SHA를 `BUILD_SHA`로 내보낸다.

## 서버 SSH 접속 (관리용)

```
ssh -i ~/.ssh/lingo_runpod -p <포트> root@<IP>
```
IP/포트는 pod 재시작 때마다 바뀜 — RunPod 콘솔 Connect 탭에서 확인.
로그: `/workspace/logs/server.log`

## 파일 위치 (pod 볼륨 /workspace)

- `lingo/scorer/` 코드, `lingo/checkpoints/chandra_scorer.pt` 체크포인트
- `lingo/kanji/` KanjiVG SVG 템플릿
- `hf/` Chandra 백본 캐시 (~16GB, 첫 로드 때 다운로드 후 재사용)
