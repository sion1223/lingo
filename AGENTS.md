# Codex repository instructions

이 파일의 범위는 저장소 전체다. 하위 디렉터리에 더 구체적인 `AGENTS.md`가 생기면 해당 범위에서는 하위 지시가 우선한다.

## 현재 최우선 목표

완료 후 한 번에 점수를 보여 주는 현재 흐름을, 사용자가 획을 쓰는 동안과 펜을 뗀 직후 단계적으로 개입하는 **실시간 선생님형 필기 튜터**로 개선한다.

구현 기준 문서는 다음 두 개다.

1. 전체 아키텍처와 Phase 0~5 계약: [`docs/REALTIME_TUTOR_IMPLEMENTATION.md`](docs/REALTIME_TUTOR_IMPLEMENTATION.md)
2. 현재 다음 작업과 실제 배포 검증 계약: [`docs/NEXT_PHASE_RUNPOD_VALIDATION.md`](docs/NEXT_PHASE_RUNPOD_VALIDATION.md)

작업을 시작하기 전에 두 문서를 모두 읽는다. 현재 기본 작업 순서는 다음과 같다.

1. 현재 구현이 Phase 0과 Phase 1 완료 조건을 충족하는지 근거 기반으로 감사한다.
2. 미완료 항목이 있으면 해당 항목만 보완한다.
3. Phase 2 경량 서버 코치를 구현한다.
4. 실제 RunPod GPU Pod에서 direct API, Supabase Edge Function, 브라우저 E2E를 수행한다.
5. 정확한 SHA와 실제 p50/p95, 실패 사항, Pod Stop 증거를 보고한다.

Phase 3 이상은 Phase 2와 실제 RunPod 검증이 통과된 뒤 별도 PR로 진행한다.

## 현재 코드에서 먼저 읽을 파일

1. `web/index.html` — Pointer Events, 캔버스, 현재 사후 채점 UX
2. `web/edge-score.ts` — Supabase Edge Function과 RunPod 프록시
3. `web/edge-app.ts` — 항상 켜진 앱 배포 후보와 중복 HTML 여부
4. `scorer/server.py` — FastAPI 엔드포인트와 모델 로딩
5. `scorer/chandra_scorer.py` — Chandra 기반 심층 채점과 반사실 분석
6. `scorer/hybrid.py` — Chandra + 경량 좌표 모델 결합
7. `scorer/feedback.py` — 획 매칭, 기하 피드백, 그래디언트 경로
8. `scorer/model.py`, `scorer/data.py`, `scorer/synth.py` — 경량 좌표 Scorer와 학습 데이터 계약
9. `serve.sh`, `runpod-session.ps1`, `README-SERVE.md` — 실제 RunPod 기동과 운영 경로
10. `docs/TODO.md` — 기타 제품 개선 항목

## 제품 불변조건

- 피드백은 사용자가 `채점하기`를 누르기 전에도 제공되어야 한다.
- 필기 중 매 포인트를 네트워크로 보내지 않는다.
- `pointermove` 피드백은 브라우저 로컬 계산이어야 한다.
- 펜을 뗀 뒤에는 로컬 결과를 먼저 보여 주고, 경량 서버 결과가 도착하면 보정한다.
- Chandra 비전 타워와 획별 반사실 분석은 실시간 hot path에 넣지 않는다.
- 한 시점에 행동 가능한 교정은 최대 1개만 보여 준다.
- 자동으로 사용자 획을 지우거나 정답 경로에 스냅하지 않는다.
- 서버가 꺼져 있어도 geometry-only 피드백이 동작해야 한다.
- Pod가 Stop된 상태에서 새로 앱을 열어도 최소 핵심 문자 연습이 가능해야 한다.
- 색만으로 오류를 전달하지 않는다.
- 자연어보다 오류 코드, 신뢰도, 좌표, 벡터, 다음 행동을 먼저 설계한다.

## 호환성 규칙

- 기존 `POST /score` 요청·응답 계약을 깨지 않는다.
- 기존 `[x, y]` 포인트 배열을 계속 지원한다.
- rich point `{x, y, t, pressure, ...}`를 추가할 때는 이전 형식과 양방향 호환되게 한다.
- 기존 1단계 따라쓰기와 2단계 암기쓰기 기능을 삭제하지 않는다.
- 기존 점수 척도를 근거 없이 변경하지 않는다.
- 체크포인트와 KanjiVG 데이터 형식을 마이그레이션하지 않는다.
- 오래된 PR #1은 통째로 merge/cherry-pick하지 않는다. DTW 아이디어만 테스트와 함께 다시 구현한다.

## 아키텍처 규칙

세 경로를 분리한다.

1. **로컬 코치**: `pointermove`와 즉시 `pointerup` 피드백. 순수 기하 계산, 네트워크 없음.
2. **경량 코치**: `POST /coach/stroke`. 기존 좌표 `Scorer`를 최대 1회 호출하고, 모델이 없으면 geometry-only 폴백.
3. **심층 최종채점**: 기존 `POST /score`. Chandra/Hybrid와 반사실 분석. UI 비차단.

실시간 획 매칭은 완성 글자용 전역 Hungarian 결과에 의존하지 않는다. 인정된 획 prefix를 유지하는 인과적·단조 매칭을 사용한다.

프런트엔드는 우선 네이티브 ES module로 분리한다. 실시간 튜터 작업만을 이유로 React/Vue 등의 전면 재작성을 하지 않는다.

앱 정적 자산과 모델 서버 수명을 분리한다. RunPod가 꺼지면 앱 자체도 열리지 않는 구조는 offline local coach 완료 조건을 만족하지 않는다.

## 현재 구현 순서

### Gate A — Phase 0/1 진행 감사

- 현재 branch, HEAD, main merge-base, dirty tree를 기록한다.
- Phase 0/1 완료 조건을 `PASS`, `FAIL`, `NOT RUN`으로 판정한다.
- 기존 테스트를 실제 실행한다.
- 테스트와 근거가 없는 구현은 완료로 인정하지 않는다.
- 결과를 `docs/validation/PHASE01_STATUS_<YYYYMMDD>.md`에 남긴다.

### Phase 2 — 경량 서버 코치

- `scorer/realtime.py`, `scorer/schemas.py`, 벤치마크 및 계약 테스트를 추가한다.
- geometry-only와 geometry+stroke-model 모드를 지원한다.
- `POST /coach/stroke`와 분리된 `/health` 상태를 추가한다.
- `/health`에 배포된 정확한 `build_sha`를 노출한다.
- `web/edge-score.ts`에 coach 라우팅과 2.5초 timeout을 추가한다.
- 로컬 결과를 먼저 표시하고 서버 결과는 비동기로 보정한다.
- 기존 `/score`를 회귀시키지 않는다.

### Gate B — 로컬 테스트

- Python, JavaScript, 계약, geometry fallback, stale response, 지연시간 테스트를 모두 실행한다.
- 로컬 Gate가 실패하면 RunPod에 배포하지 않는다.

### Gate C — 실제 RunPod 검증

- GitHub에 push된 정확한 SHA를 실제 RunPod Pod에 checkout한다.
- 실제 GPU, 체크포인트, direct proxy URL에서 `/health`, `/template`, `/coach/stroke`, `/score`를 호출한다.
- geometry-only와 geometry+stroke-model을 실제 Pod에서 각각 검증한다.
- 실제 배포된 Supabase Edge Function을 경유해 health/template/coach/score를 검증한다.
- direct 및 Edge coach p50/p95를 실제 측정한다.
- 브라우저 E2E와 서버 offline fresh-load를 검증한다.
- 작업 종료 시 실제 Pod를 Stop하고 상태를 확인한다.

상세 절차와 합격 조건은 `docs/NEXT_PHASE_RUNPOD_VALIDATION.md`를 따른다.

## RunPod 실기 검증 규칙

RunPod 실제 검증은 필수다.

- localhost, mock HTTP, CI, CPU-only 테스트는 대체물이 아니다.
- RunPod 접근 권한이 없으면 완료가 아니라 `미완료`로 보고한다.
- Pod 내부 수정 작업 트리로 테스트하지 않는다. 정확한 push SHA를 사용한다.
- `/health.build_sha`가 대상 SHA와 다르면 검증을 중단한다.
- API key, service role key, SSH key, 전체 env 덤프를 기록하지 않는다.
- 사용자 지시 없이 Pod를 `Terminate`하지 않는다.
- 성공·실패와 관계없이 끝에 Pod를 `Stop`한다.
- 실제로 실행하지 않은 테스트를 성공했다고 쓰지 않는다.
- 실제 측정하지 않은 latency를 추정하지 않는다.

## 코딩 규칙

- 먼저 관련 코드를 읽고 기존 유틸리티를 재사용한다. 동일한 정규화·재샘플링 로직을 여러 곳에 복제하지 않는다.
- 계산 로직과 DOM 조작을 분리한다. 알고리즘은 브라우저 없이 테스트 가능한 순수 함수로 작성한다.
- 사용자 입력, 문자 변경, undo, clear 후 도착한 오래된 응답을 `request_id`, `attempt_revision`, `AbortController`로 폐기한다.
- 임계값은 이름 있는 상수 또는 설정 객체로 둔다. 코드 곳곳에 magic number를 흩뿌리지 않는다.
- 오류 문구를 상태 판정의 키로 쓰지 않는다. 안정적인 오류 코드를 사용한다.
- 예외를 삼키지 않는다. 사용자에게는 안전한 메시지를, 개발 로그에는 원인을 남긴다.
- 성능 개선을 주장할 때는 실제 p50/p95 측정값과 측정 환경을 기록한다.
- 대형 체크포인트, 모델 캐시, 원시 사용자 로그, 생성된 이미지, 훈련 로그를 Git에 추가하지 않는다.
- 새 의존성은 필요한 최소 범위로 제한하고 이유를 문서화한다.
- 비밀키, service role key, 개인 URL, 임시 RunPod 주소를 새 소스에 커밋하지 않는다.
- 운영 URL은 환경설정에서 주입하며, 검증 보고서에는 필요 시 비밀이 아닌 host 메타데이터만 남긴다.

## 테스트 규칙

변경에 맞는 테스트가 없으면 기능이 완료된 것이 아니다.

최소 검증 항목:

- 완벽한 획, 시작/종점 이동, 경로 이탈, 방향 반전, 너무 짧거나 긴 획
- 잘못된 순서, 추가/누락 획, 0길이/중복점, NaN/Infinity
- geometry-only 폴백
- 경량 모델 로드 실패 폴백
- undo/clear/문자 변경 뒤 stale response 폐기
- 기존 `/score` 회귀 테스트
- 기존 배열 포인트와 rich point의 호환성
- 로컬 분석 지연시간
- direct RunPod 및 Edge Function 원격 계약
- Pod Stop 뒤 fresh-load local-only 동작

권장 명령은 구현하면서 저장소에 실제로 동작하도록 추가한다.

```bash
python -m pytest -q
node --test web/tests/*.test.mjs
python -m scorer.benchmark_realtime --engine geometry-only
python -m scorer.benchmark_realtime --engine geometry+stroke-model
python scripts/validate_runpod.py --help
```

명령이 아직 존재하지 않으면 해당 Phase에서 만들고 문서화한다. 실행하지 않은 테스트를 통과했다고 보고하지 않는다.

## Git 및 작업 단위

- 기존 사용자의 변경을 되돌리거나 관련 없는 파일을 정리하지 않는다.
- 기능 변경, 테스트, 문서 갱신을 함께 제공하되 검토 가능한 작은 커밋으로 나눈다.
- Phase를 넘나드는 거대한 커밋을 만들지 않는다.
- 생성물이나 포맷 변경만으로 대규모 diff를 만들지 않는다.
- 구현 커밋과 실제 RunPod 검증 보고서 커밋을 분리한다.
- 원격 검증 전에 대상 커밋을 push한다.
- 작업 종료 전에 `git diff`, 테스트 결과, 새 파일 크기, 비밀정보 포함 여부를 확인한다.
- Phase 3은 Phase 2와 RunPod Gate가 통과된 뒤 별도 PR로 진행한다.

## 결과 보고 형식

Codex의 최종 보고에는 다음을 반드시 포함한다.

1. Phase 0/1 감사 결과
2. 구현한 Phase와 구현하지 않은 Phase
3. branch, 정확한 HEAD, main merge-base
4. 변경 파일과 책임
5. 실행한 로컬 명령 및 실제 결과
6. 실제 RunPod Pod와 GPU 환경
7. 배포된 `build_sha` 일치 여부
8. direct/Edge 요청 수, status, timeout 수
9. 실제 측정한 direct/Edge coach p50/p95/max
10. `/score` 실제 p50/p95와 UI 비차단 결과
11. geometry-only 실제 폴백 결과
12. 서버 offline fresh-load 결과
13. iPad/Apple Pencil 검증 여부
14. Pod Stop 확인
15. 검증하지 못한 기기·환경
16. 남은 위험과 다음 단일 작업 단위

RunPod를 실제로 시작하지 않았거나 접속하지 못했다면 첫 문단에서 명시하고 완료를 주장하지 않는다.
