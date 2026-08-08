# 다음 실행 지시서: Phase 2 경량 코치와 RunPod 실기 검증

> 상태: **다음 작업 단위의 실행 계약**  
> 적용 대상: Codex 및 이 저장소에서 구현·검증하는 모든 개발 에이전트  
> 선행 문서: [`REALTIME_TUTOR_IMPLEMENTATION.md`](REALTIME_TUTOR_IMPLEMENTATION.md)  
> 최우선 원칙: **로컬·mock·CI 테스트만으로 완료 처리하지 않는다. 실제 RunPod GPU Pod를 시작하고, 배포된 정확한 커밋으로 원격 E2E를 수행한 뒤 반드시 Stop한다.**

---

## 0. 이번 작업 단위의 결과물

이번 작업은 다음 네 가지를 순서대로 완료해야 한다.

1. 현재 구현 진행분이 Phase 0과 Phase 1의 완료 조건을 실제로 만족하는지 감사한다.
2. 미완료 항목이 있으면 먼저 보완하고, 완료된 경우 Phase 2 경량 서버 코치를 구현한다.
3. 실제 RunPod Pod에서 직접 API, Supabase 프록시, 브라우저 경로를 검증한다.
4. 정확한 커밋·환경·명령·응답·지연시간·실패 사항·Pod 종료 증거를 검증 보고서로 남긴다.

이번 단위의 기본 범위는 **Phase 2까지**다. Phase 3 적응형 선생님 정책은 Phase 2와 RunPod 실기 게이트가 모두 통과된 뒤 별도 PR에서 시작한다.

---

## 1. 절대 규칙

- RunPod 실기 검증은 선택 사항이 아니다.
- `localhost`, fake server, mocked HTTP, CI runner, CPU-only 환경은 RunPod 검증을 대체하지 못한다.
- 실제 RunPod 접근 권한이나 체크포인트가 없어 실기 검증을 못 하면 작업 상태는 `미완료`다. 로컬 테스트 통과만으로 `완료`라고 보고하지 않는다.
- 실제로 실행하지 않은 명령, 확인하지 않은 UI, 측정하지 않은 지연시간을 추정해 기록하지 않는다.
- 테스트할 소스는 GitHub에 push된 **정확한 커밋 SHA**여야 한다. Pod 내부의 수정된 작업 트리로 테스트하지 않는다.
- Pod에서는 `Stop`만 사용한다. 사용자 지시 없이 `Terminate`하여 볼륨을 삭제하지 않는다.
- 테스트 성공·실패와 관계없이 작업 종료 시 Pod를 Stop한다. 예외 경로에도 `finally`/`trap` 성격의 종료 절차를 둔다.
- RunPod API key, Supabase service role key, bearer token, 개인 SSH key, 전체 환경변수 덤프를 로그나 Git에 남기지 않는다.
- Pod ID, GPU 종류, 리전, 이미지 이름, 커밋 SHA, 체크포인트 SHA-256은 검증 재현에 필요한 비밀이 아닌 메타데이터로 기록할 수 있다.
- 원시 사용자 필기 로그나 개인정보가 포함된 기존 제출 데이터를 테스트에 사용하지 않는다. 합성 fixture만 사용한다.

---

## 2. Gate A — 현재 진행분 감사

코드를 추가하기 전에 현재 상태를 고정한다.

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git merge-base HEAD origin/main
git log --oneline --decorate -20
```

### 2.1 반드시 확인할 항목

- Phase 0 테스트와 기준선 문서가 실제로 존재하는가.
- 기존 `POST /score` 계약을 고정하는 회귀 테스트가 있는가.
- `web/index.html` 모듈화 뒤 기존 따라쓰기·암기쓰기 기능이 유지되는가.
- rich point와 기존 `[x, y]` 입력이 모두 동작하는가.
- 로컬 코치가 네트워크 없이 동작하는가.
- `request_id`, `attempt_revision`, `AbortController`가 stale response를 폐기하는가.
- 한 번에 하나의 cue만 표시하는가.
- 로컬 p50/p95가 실제 측정되었는가.
- 새 소스에 배포 URL이나 비밀키가 하드코딩되지 않았는가.

### 2.2 감사 결과 처리

- 일부 항목이 미완료면 Phase 2를 시작하기 전에 해당 항목만 보완한다.
- 이미 구현된 코드를 단순히 다시 작성하지 않는다.
- 테스트가 없는 기존 구현은 완료로 인정하지 않는다.
- 감사 결과는 `docs/validation/PHASE01_STATUS_<YYYYMMDD>.md`에 남긴다.
- 보고서에는 각 완료 조건을 `PASS`, `FAIL`, `NOT RUN`으로 명시하고 근거 파일·테스트 명령·실제 결과를 연결한다.

---

## 3. 이번 구현 범위 — Phase 2 경량 서버 코치

### 3.1 구조화된 스키마

`scorer/schemas.py` 또는 동등한 파일에 다음 계약을 명시한다.

- legacy point `[x, y]`
- rich point `{x, y, t, pressure, tiltX, tiltY, pointerType}`
- `CoachStrokeRequest`
- `CoachStrokeResponse`
- 안정적인 오류 코드 enum
- `protocol_version`
- `request_id`, `attempt_revision`
- engine mode: `geometry-only`, `geometry+stroke-model`

검증 실패는 일관된 4xx 오류 코드로 반환하고, 내부 파일 경로나 예외 전체를 응답에 노출하지 않는다.

### 3.2 경량 코치 엔진

`scorer/realtime.py` 또는 동등한 파일에 다음을 구현한다.

- rich point에서 좌표와 메타데이터 분리
- 중복점·0길이·과도한 점 수·NaN/Infinity 방어
- 호길이 재샘플링
- 인정된 획 prefix를 유지하는 인과적·단조 획 매칭
- banded DTW 또는 동등한 제한 정렬
- 시작점·종점·경로·형상·방향·길이 지표
- 기존 경량 `Scorer` 선택적 1회 추론
- 기하 증거와 모델 증거 결합
- 한 개의 primary cue와 구조화 overlay 생성

실시간 엔진에서 다음은 금지한다.

- Chandra 비전 타워 호출
- 획별 `N+1` 반사실 추론
- 요청당 경량 모델 forward 여러 번 반복
- 문구 문자열을 상태 판정 키로 사용
- 사용자 획 자동 수정 또는 정답 경로 스냅

### 3.3 서버 엔드포인트

`scorer/server.py`에 다음을 추가하되 기존 `/score` 계약을 유지한다.

```text
POST /coach/stroke
GET  /health
```

`/health`는 최소한 다음 상태를 구분한다.

```json
{
  "ok": true,
  "protocol_version": 1,
  "build_sha": "deployed-git-sha",
  "coach_ready": true,
  "coach_engine": "geometry+stroke-model",
  "deep_score_ready": false,
  "deep_model_loading": true
}
```

요구사항:

- `build_sha`는 배포 시 주입된 정확한 Git SHA다.
- RunPod 실기 검증은 `/health.build_sha`가 테스트 대상 SHA와 일치하지 않으면 즉시 실패한다.
- 경량 모델과 Chandra 모델 로딩 상태를 분리한다.
- Chandra 로딩 중에도 geometry-only 또는 경량 모델 코치가 응답해야 한다.
- 경량 체크포인트가 없거나 로드에 실패하면 서버 전체를 죽이지 말고 `geometry-only`로 강등한다.
- 의도적인 geometry-only 검증을 위한 명시적 환경설정 또는 시작 옵션을 제공한다. 체크포인트 파일을 삭제하거나 영구 변경하지 않는다.

### 3.4 Edge Function

`web/edge-score.ts` 또는 분리된 함수에 다음을 구현한다.

- `action: "coach"`를 `/coach/stroke`로 전달
- coach hard timeout 2.5초
- 기존 `health`, `template`, `score` 동작 유지
- upstream URL은 배포 환경변수에서 로드
- upstream URL 누락 시 안전한 503
- coach 요청마다 DB insert 금지
- 시도 종료 시 필요한 이벤트만 배치 저장
- 저장 실패가 코칭 응답을 실패시키지 않음
- upstream의 `build_sha`, engine, elapsed를 진단용으로 보존

### 3.5 프런트엔드 연결

- `pointerup` 직후 로컬 결과를 먼저 표시한다.
- 서버 요청은 그 뒤 비동기로 실행한다.
- 서버가 느리거나 실패해도 로컬 결과를 유지한다.
- 서버 결과가 더 높은 신뢰도를 가질 때만 부드럽게 보정한다.
- 사용자가 undo, clear, 문자 변경, 재시도를 하면 이전 응답을 무시한다.
- 최종 `/score`는 UI 입력을 막지 않는다.

### 3.6 정적 프런트와 서버 오프라인 조건

현재 저장소에는 `web/index.html`, Supabase `edge-app.ts`, RunPod 루트 서빙이 혼재한다. 실제 배포 경로를 먼저 확인하고 단일 소스와 배포 절차를 정한다.

다음 테스트를 통과하지 못하면 “서버가 꺼져 있어도 연습 가능” 조건은 실패다.

1. Pod가 Stop된 상태에서 새 브라우저 창으로 공개 앱 URL을 연다.
2. 앱 셸과 로컬 코치 코드가 로드된다.
3. 최소 핵심 문자 `永`, `水`, `木`, `日`, `語`의 템플릿을 사용할 수 있다.
4. 획을 쓰면 로컬 geometry 피드백이 동작한다.
5. 원격 보정과 최종채점만 오프라인으로 표시된다.

필요하면 다음 중 하나를 구현한다.

- 항상 켜진 정적 호스팅에 `web/` 번들을 배포
- 핵심 템플릿을 작은 정적 asset으로 포함
- 이전에 사용한 템플릿을 IndexedDB/Cache Storage에 저장
- PWA service worker로 앱 셸과 핵심 asset 캐시

Supabase Edge Function 안에 수동 복사된 거대한 HTML 문자열과 `web/index.html`이 서로 다른 버전으로 남지 않게 한다. 동일 빌드 산출물이나 명시적인 생성 절차를 사용한다.

---

## 4. Gate B — RunPod 전 로컬 검증

아래 명령은 실제 저장소에 존재하고 성공해야 한다. 명령이 없으면 먼저 추가한다.

```bash
python -m pytest -q
node --test web/tests/*.test.mjs
python -m scorer.benchmark_realtime --engine geometry-only
python -m scorer.benchmark_realtime --engine geometry+stroke-model
```

최소 테스트:

- legacy/rich point 동등성
- 완벽한 획과 주요 합성 오류
- 잘못된 순서와 추가·누락 획
- 0길이·중복점·NaN·Infinity·과도한 점 수
- geometry-only 폴백
- 모델 로드 실패 폴백
- `/coach/stroke` 계약
- 기존 `/score` 계약
- undo/clear/문자 변경 뒤 stale response 폐기
- coach timeout 뒤 로컬 피드백 유지

Gate B가 실패하면 RunPod에 배포하지 않는다.

---

## 5. 자동 원격 검증 도구

사람이 `curl` 몇 번 실행한 것만으로 완료 처리하지 않는다. 반복 가능한 원격 검증 도구를 추가한다.

권장 위치:

```text
scripts/validate_runpod.py
```

최소 옵션:

```text
--base-url
--edge-url
--expected-sha
--chars 永,水,木,日,語
--coach-requests 50
--score-requests 10
--output <local-json-path>
```

도구는 다음을 수행한다.

- `/health`와 `build_sha` 검증
- `/template/{char}` 검증
- legacy/rich coach 요청
- 오류 입력의 4xx 검증
- geometry-only와 geometry+stroke-model 응답 검증
- `/score` 회귀 스키마 검증
- direct Pod 및 Edge Function 각각의 p50/p95 계산
- HTTP status 분포와 timeout 횟수 기록
- 응답의 비밀·원시 사용자 데이터 미기록

생성한 원시 JSON과 전체 로그는 기본적으로 Git에 커밋하지 않는다. 요약 결과만 검증 보고서에 남긴다.

---

## 6. Gate C — 실제 RunPod 배포 및 검증

### 6.1 테스트 대상 고정

코드 변경과 로컬 테스트를 완료한 뒤 먼저 push한다.

```bash
git status --short
git rev-parse HEAD
git push origin HEAD
```

Pod에서 다음을 확인한다.

```bash
cd /workspace/lingo
git fetch --all --prune
git checkout <target-branch-or-detached-sha>
git reset --hard <exact-tested-sha>
git status --short
git rev-parse HEAD
```

조건:

- `git status --short`가 비어 있어야 한다.
- Pod의 HEAD가 원격 검증 대상 SHA와 정확히 일치해야 한다.
- Pod에서 직접 수정한 파일이 있으면 검증을 중단하고 별도 커밋·push 후 처음부터 다시 진행한다.

### 6.2 환경 증거 수집

비밀을 제외하고 다음을 기록한다.

```bash
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
python --version
python - <<'PY'
import torch
print('torch', torch.__version__)
print('cuda', torch.version.cuda)
print('cuda_available', torch.cuda.is_available())
print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')
PY
sha256sum checkpoints/chandra_scorer.pt 2>/dev/null || true
sha256sum checkpoints/stroke_scorer.pt 2>/dev/null || true
sha256sum checkpoints/hybrid_config.json 2>/dev/null || true
```

또한 다음을 기록한다.

- Pod ID 또는 이름
- GPU 종류와 VRAM
- 리전
- 컨테이너/템플릿 이미지
- 볼륨 경로
- 배포 시작·종료 UTC 시각
- 환경변수 **이름 목록**과 필수 값 존재 여부
- 모델 및 템플릿 파일 존재 여부

환경변수 값 전체를 출력하지 않는다.

### 6.3 실제 cold start

Pod가 이미 실행 중이면 먼저 안전하게 Stop한 뒤 cold start를 측정한다.

측정 구간:

1. Pod Start 요청 시각
2. RunPod proxy가 HTTP 응답을 시작한 시각
3. `/health.coach_ready == true` 시각
4. `/health.deep_score_ready == true` 시각
5. 첫 성공 `/coach/stroke` 시각
6. 첫 성공 `/score` 시각

`serve.sh`, Jupyter 자동 기동, 모델 캐시 상태가 실제 운영과 같아야 한다. 별도 임시 개발 서버만 띄워 성공 처리하지 않는다.

### 6.4 직접 Pod API 테스트

실제 proxy URL에 대해 최소 다음을 수행한다.

| 테스트 | 최소 조건 |
|---|---|
| `GET /health` | build SHA, coach/deep 상태, GPU 정보 확인 |
| `GET /template/永` | 유효 템플릿 반환 |
| legacy coach | `[x,y]` 입력 성공 |
| rich coach | 시간·필압 포함 입력 성공 |
| 완벽한 획 | 허용 또는 경미 cue |
| 시작점 이동 | `START_OFFSET` 계열 cue |
| 방향 반전 | `DIRECTION_REVERSED` 계열 cue |
| 잘못된 순서 | 안정적인 순서 오류 코드 |
| 잘못된 입력 | NaN/Infinity/빈 획/과도한 점 수가 안전한 4xx |
| `POST /score` | 기존 응답 키와 점수 범위 유지 |
| Chandra 로딩 중 coach | 경량 또는 geometry-only 응답 성공 |

### 6.5 실제 geometry-only 모드

동일한 실제 Pod에서 경량 체크포인트를 삭제하지 않고 명시적 설정으로 geometry-only 모드를 실행한다.

검증 조건:

- `/health.coach_engine == "geometry-only"`
- `/coach/stroke`가 성공
- 응답이 fallback임을 구조화 필드로 표시
- 프런트엔드가 이를 오류 팝업으로 취급하지 않음
- 기존 `/score` 경로와 체크포인트 파일이 손상되지 않음

검증 후 기본 운영 모드로 되돌리고 `/health.coach_engine == "geometry+stroke-model"`을 다시 확인한다.

### 6.6 실제 성능 측정

warm-up과 측정 요청을 분리한다.

- coach warm-up: 최소 5회
- direct coach 측정: 최소 50회
- Edge coach 측정: 최소 50회
- `/score` warm-up: 최소 1회
- `/score` 측정: 최소 10회
- 문자: `永`, `水`, `木`, `日`, `語`
- 입력 유형: 정상, 시작점 오류, 방향 반전, 경로 이탈을 섞는다.
- 동시성 1 결과를 기본으로 기록하고, 가능하면 동시성 3도 별도로 기록한다.

필수 기록:

- p50, p95, max
- HTTP status 분포
- timeout 수
- cold/warm 구분
- engine mode
- GPU 사용량과 최대 VRAM의 관찰값

목표:

- direct `/coach/stroke` warm p95: 400ms 이하
- Edge 경유 coach 사용자 관측 p95: 700ms 이하 목표
- coach client timeout: 2.5초
- `/score`: 엄격한 임계값보다 UI 비차단 여부와 실제 p50/p95 기록이 우선

목표를 넘으면 수치를 숨기지 않는다. 원인을 분해하고 다음 최적화 항목을 적는다.

### 6.7 Supabase Edge Function 실제 E2E

로컬 Deno 실행만으로 대체하지 않는다. 실제 배포된 Edge Function을 호출한다.

검증 항목:

- `action: health`
- `action: template`
- `action: coach`
- `action: score`
- coach 2.5초 timeout
- upstream 503 처리
- 잘못된 body의 안전한 4xx
- coach 요청이 매번 DB row를 만들지 않는지
- score 또는 시도 종료 기록이 기존 스키마를 깨지 않는지
- Edge 응답의 upstream `build_sha`가 테스트 SHA와 일치하는지

운영 DB에 테스트 행을 남겨야 한다면 명확한 `test_run_id`를 사용하고, 기존 사용자 데이터를 수정·삭제하지 않는다.

### 6.8 브라우저 및 기기 E2E

최소 실제 데스크톱 Chrome으로 다음을 검증한다.

1. 앱 공개 URL 접속
2. `永` 템플릿 로드
3. 필기 중 로컬 신호
4. pointerup 즉시 로컬 cue
5. 서버 보정이 비동기로 도착
6. `/score` 실행 중에도 undo/clear 또는 다음 연습이 가능
7. 느린 응답 뒤 stale result가 현재 시도를 덮어쓰지 않음
8. Pod Stop 뒤 새로고침하고 핵심 문자 local-only 연습 가능

가능하면 iPad Safari + Apple Pencil에서도 같은 시나리오를 수행한다. 실제 기기가 없으면 `NOT RUN`으로 명시하며 데스크톱 테스트를 iPad 테스트로 가장하지 않는다.

### 6.9 Pod 종료와 오프라인 재검증

모든 테스트가 끝나면 반드시 Pod를 Stop한다.

확인할 사항:

- RunPod 상태가 Stop/Exited 계열로 전환됨
- public health endpoint가 더 이상 정상 GPU 응답을 주지 않음
- 공개 앱 URL은 여전히 열림
- 핵심 문자 local-only 피드백은 계속 동작
- 원격 보정과 최종채점은 명확한 offline 상태로 표시
- 자동 재시작이나 남은 watchdog 때문에 Pod가 다시 켜지지 않음

테스트가 중간에 실패해도 Pod 종료 확인까지 수행한다.

---

## 7. RunPod 실기 합격 기준

아래 항목을 모두 만족해야 Phase 2를 완료로 표시한다.

- [ ] 정확한 push된 SHA가 Pod에서 실행됨
- [ ] Pod 작업 트리가 깨끗함
- [ ] 실제 GPU와 체크포인트가 로드됨
- [ ] `/health.build_sha`가 대상 SHA와 일치함
- [ ] direct `/coach/stroke` 실제 요청 성공
- [ ] Edge 경유 `/coach/stroke` 실제 요청 성공
- [ ] geometry-only 실제 모드 성공
- [ ] geometry+stroke-model 실제 모드 성공
- [ ] 기존 `/score` 실제 Chandra/Hybrid 요청 성공
- [ ] legacy/rich point 호환성 성공
- [ ] 4xx 입력 검증과 5xx 누수 방지 성공
- [ ] direct coach warm p95 측정됨
- [ ] Edge coach p95 측정됨
- [ ] `/score` p50/p95 측정됨
- [ ] 서버 보정이 UI를 차단하지 않음
- [ ] stale response가 폐기됨
- [ ] Pod Stop 뒤 fresh-load local-only 흐름 성공
- [ ] 테스트 종료 후 Pod Stop 확인
- [ ] API key와 비밀정보가 Git·로그에 없음

하나라도 실패하거나 실행하지 못하면 전체 결과를 `부분 완료` 또는 `미완료`로 보고한다.

---

## 8. 검증 보고서

다음 파일을 생성한다.

```text
docs/validation/RUNPOD_PHASE2_<YYYYMMDD>.md
```

필수 목차:

1. 결론: PASS / PARTIAL / FAIL
2. 저장소, 브랜치, 정확한 SHA
3. main merge-base와 변경 범위
4. RunPod 환경
5. 체크포인트 SHA-256
6. 배포 및 cold-start 시간선
7. 실행한 로컬 테스트와 결과
8. direct API 테스트 결과
9. Edge Function E2E 결과
10. 브라우저/iPad 결과
11. 지연시간 p50/p95/max 표
12. geometry-only 폴백 결과
13. 기존 `/score` 호환성 결과
14. offline fresh-load 결과
15. 실패·미검증 항목
16. Pod Stop 확인
17. 남은 위험과 다음 PR 범위

요약 응답과 로그 발췌는 비밀·개인정보를 제거한 뒤 기록한다. “정상 작동함” 같은 서술만 남기지 말고 status, engine, build SHA, elapsed, 요청 수, 오류 수를 수치로 기록한다.

---

## 9. Git 작업 단위

권장 커밋 순서:

1. `test: freeze phase 0/1 status and remote contracts`
2. `feat: add geometry realtime coach engine`
3. `feat: expose lightweight coach API and health states`
4. `feat: route coach through edge and refine local feedback`
5. `test: add reproducible RunPod remote validator`
6. `docs: record actual RunPod phase 2 validation`

규칙:

- 구현 코드와 실제 검증 보고서를 같은 커밋으로 뭉치지 않는다.
- 실패한 검증도 숨기지 말고 보고서에 남긴다.
- 검증 중 발견한 별도 대규모 문제는 현재 PR을 확장하지 말고 후속 이슈 또는 PR로 분리한다.
- Phase 3 기능을 Phase 2 PR에 몰래 섞지 않는다.

---

## 10. Codex 최종 보고 형식

최종 응답에는 다음을 반드시 포함한다.

1. Phase 0/1 감사 결과
2. Phase 2에서 구현한 항목과 제외한 항목
3. 정확한 branch, HEAD, merge-base
4. 변경 파일과 각 책임
5. 로컬에서 실행한 명령과 실제 결과
6. 실제 RunPod Pod 정보와 GPU 정보
7. 실제 배포 SHA 일치 여부
8. direct/Edge coach 요청 수와 성공·오류·timeout 수
9. direct/Edge coach p50/p95/max
10. `/score` p50/p95와 UI 비차단 결과
11. geometry-only 실제 폴백 결과
12. 서버 offline fresh-load 결과
13. iPad/Apple Pencil 검증 여부
14. Pod Stop 확인
15. 검증하지 못한 사항과 남은 위험
16. 다음 단일 작업 단위

RunPod에 접속하지 못했거나 Pod를 실제로 시작하지 않았다면 첫 문단에서 명시하고, Phase 2 완료를 주장하지 않는다.
