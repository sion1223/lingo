# RunPod Phase 2 검증 — 2026-08-08

## 1. 결론: PARTIAL

Phase 2 구현과 로컬 Gate B는 통과했다. 그러나 **실제 RunPod Pod를 시작하거나 접속하지 않았고,
GitHub push·Supabase 배포·실제 Edge Function·공개 브라우저 E2E를 실행하지 않았다.** 따라서
GitHub PR #2의 원격 합격 조건에 따른 Phase 2 전체 완료를 주장하지 않는다.

차단 사유는 이 환경에 RunPod 저장 자격증명과 `RUNPOD_API_KEY`, `LINGO_API_KEY`, Supabase CLI가
모두 없고, 공개 Supabase/RunPod 호스트의 DNS 조회도 실패했기 때문이다. 외부 상태를 변경하지
않았으며 Pod Stop 상태 역시 조회하지 못했다.

## 2. 저장소, 브랜치, 정확한 SHA

- 저장소: `sion1223/lingo`
- branch: `main`
- Phase 2 구현 커밋: `0ff99e396b2367422fe978a637c091483a48afcb`
- `origin/main`: `811258de21812993efce812b34e4ba53b859029e`
- main merge-base: `811258de21812993efce812b34e4ba53b859029e`
- 구현 커밋 시 `origin/main...HEAD`: behind 0, ahead 4
- GitHub push: **NOT RUN**
- 배포 대상 SHA와 `/health.build_sha` 원격 일치: **NOT RUN**

## 3. 변경 범위

- `scorer/schemas.py`: legacy/rich point, 요청·응답, 안정적 cue/API 오류 코드 계약
- `scorer/realtime.py`: 인과적 매칭, banded DTW, 기하 지표, 경량 모델 최대 1회 추론과 폴백
- `scorer/server.py`: `/coach/stroke`, coach/deep 독립 로딩, 안전한 오류, 분리 health와 build SHA
- `scorer/benchmark_realtime.py`: geometry-only/경량 모델 warm p50·p95 측정
- `web/coach/server-refinement.js`, `web/app.js`: 로컬 우선 렌더, 2.5초 비동기 보정, stale 폐기
- `web/edge-score.ts`: 환경설정 upstream, coach 2.5초 timeout, coach 무기록 라우팅
- `web/edge-app.ts`: 중복 HTML·내장 토큰 제거, 항상 켜진 정적 `web/` 진입점으로 분리
- `scripts/validate_runpod.py`: direct/Edge 계약·SHA·status·timeout·p50/p95 집계 도구
- 관련 Python/JavaScript 계약·회귀 테스트와 운영 문서

Phase 3의 반복 오류 memory·guided trace 정책은 구현하지 않았다.

## 4. RunPod 환경

| 항목 | 결과 |
|---|---|
| 실제 Pod ID/상태 | NOT RUN — 자격증명 없음 |
| GPU/VRAM/리전/이미지 | NOT RUN |
| Pod Python/Torch/CUDA | NOT RUN |
| 실제 작업 트리와 HEAD | NOT RUN |
| 실제 체크포인트 존재·로드 | NOT RUN |
| 배포 시작·종료 UTC | NOT RUN |

로컬 측정 환경은 Windows x64, Python 3.13.14, Torch 2.11.0+cpu,
`torch.cuda.is_available() == False`, Node.js v22.18.0이다. 이는 RunPod GPU 증거가 아니다.

## 5. 체크포인트 SHA-256

아래 값은 **로컬 파일**의 해시이며 Pod 파일과의 일치는 검증하지 않았다.

| 파일 | 로컬 SHA-256 |
|---|---|
| `checkpoints/chandra_scorer.pt` | `af6d1f01497b51220752e414656cf2cd26eaf8234b586d6669dfdb78143be7f3` |
| `checkpoints/stroke_scorer.pt` | `77a0440829459e68d567df30d423723884ec3ce7df82d2473b8297f830e0305b` |
| `checkpoints/hybrid_config.json` | `2213dd9b0322029295ea312c74f062d5400fef41d4cd7731233cda0b40bd72c1` |

## 6. 배포 및 cold-start 시간선

**NOT RUN.** 실제 Pod Start 요청, proxy 응답 시작, coach ready, deep ready, 첫 coach/score
성공 시각을 측정하지 않았다. 로컬 프로세스 스모크에서는 deep model이 로딩 중인 상태에서
두 coach 모드 모두 health 200과 coach 200을 반환했지만 cold-start 대체 증거로 사용하지 않는다.

## 7. 로컬 Gate B

2026-08-07T15:06Z 전후에 최종 구현 상태로 실행했다.

```text
python -m pytest -q
35 passed, 1 Starlette TestClient deprecation warning

node --test web/tests/*.test.mjs
26 passed

python -m ruff check ...
All checks passed

python -m compileall -q scorer scripts tests
node --check web/app.js web/api.js web/coach/controller.js web/coach/server-refinement.js
git diff --check
성공

python scripts/validate_runpod.py --help
성공
```

로컬 `TestClient` 실제 체크포인트 스모크:

| 설정 | health | build_sha | deep 상태 | coach | 응답 engine |
|---|---:|---|---|---:|---|
| `COACH_ENGINE=geometry-only` | 200 | 주입값 일치 | loading | 200 | geometry-only |
| `COACH_ENGINE=auto` | 200 | 주입값 일치 | loading | 200 | geometry+stroke-model |

## 8. direct API 결과

**실제 RunPod: NOT RUN.** direct 요청 수 0, 성공 0, 오류 0, timeout 0이다. 로컬 HTTP 계약
테스트만 통과했으며 원격 검증으로 간주하지 않는다.

## 9. Edge Function E2E 결과

**NOT RUN.** 실제 배포·호출 수 0, 성공 0, 오류 0, timeout 0이다. `RUNPOD_BASE_URL` 누락 503,
coach 2.5초 timeout, coach 무기록, `test_run_id` 표시는 정적 계약 테스트로만 검증했다.

## 10. 브라우저/iPad 결과

- 데스크톱 공개 URL Phase 2 E2E: NOT RUN — 공개 호스트 DNS 조회 실패
- iPad Safari + Apple Pencil: NOT RUN — 실제 기기 없음
- 터치 손바닥 무시/화면 회전: NOT RUN
- 로컬 Phase 1 데스크톱 마우스 검증은 별도
  `PHASE01_STATUS_20260807.md`에 기록되어 있다.

## 11. 로컬 지연시간

아래는 단일 Windows CPU 프로세스의 warm 측정값이며 direct/Edge RunPod 수치가 아니다.

| 경로 | 워밍업 | 표본 | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| pointermove 로컬 부분 분석 | 100 | 5,000 | 0.016ms | 0.025ms | 0.278ms |
| pointerup 로컬 완성 분석 | 100 | 2,000 | 0.146ms | 0.263ms | 0.936ms |
| 서버 엔진 geometry-only | 10 | 50 | 14.082ms | 14.697ms | 14.807ms |
| 서버 엔진 geometry+stroke-model | 10 | 50 | 31.988ms | 35.340ms | 38.551ms |
| direct RunPod coach | — | 0 | NOT RUN | NOT RUN | NOT RUN |
| Edge coach | — | 0 | NOT RUN | NOT RUN | NOT RUN |
| 실제 `/score` | — | 0 | NOT RUN | NOT RUN | NOT RUN |

## 12. geometry-only 폴백

로컬에서는 명시적 `COACH_ENGINE=geometry-only`, 체크포인트 없는 엔진, 모델 forward 실패를
자동화 테스트로 확인했다. 기존 체크포인트를 삭제하거나 변경하지 않았다. 실제 Pod에서 모드 전환과
기본 모드 복귀는 **NOT RUN**이다.

## 13. 기존 `/score` 호환성과 UI 비차단

- legacy `[x,y]` 요청과 기존 응답 키: 로컬 HTTP 회귀 테스트 PASS
- 비유한 legacy 좌표 거부: PASS
- 최종 score 요청 중 새 필기가 score 요청을 취소할 수 있고 상태 머신을 막지 않음: JavaScript 테스트 PASS
- 실제 Chandra/Hybrid GPU 요청과 p50/p95: NOT RUN

## 14. offline fresh-load

내장 `永`, `水`, `木`, `日`, `語` 템플릿과 로컬 geometry 코드는 번들에 존재하며 로컬 API 404
상태의 Phase 1 브라우저 검증은 PASS다. `edge-app.ts`의 중복 HTML/토큰을 제거하고 Pod 독립 정적
배포 주소를 사용하도록 바꿨다. 그러나 실제 정적 호스팅과 공개 앱 배포, Pod Stop 상태에서의 새 창
테스트는 **NOT RUN**이다.

## 15. 실패·미검증 항목

- 정확한 SHA의 GitHub push 및 Pod checkout
- 실제 GPU·VRAM·드라이버와 Pod 체크포인트 해시
- RunPod cold start와 direct 50회 coach/10회 score
- 실제 Supabase Edge 50회 coach/10회 score와 DB 행 확인
- direct p95 400ms, Edge p95 700ms 원격 목표
- 실제 공개 데스크톱 Chrome과 offline fresh-load
- iPad Safari, Apple Pencil, 손바닥 입력

## 16. Pod Stop 확인

이 작업에서 Pod를 Start/Stop/Terminate하지 않았다. 자격증명이 없어 현재 원격 상태도 조회하지
못했으므로 Pod Stop을 확인했다고 주장하지 않는다. 사용자 데이터나 외부 배포 상태를 변경하지 않았다.

## 17. 남은 위험과 다음 작업

다음 단일 작업 단위는 배포 권한이 있는 환경에서 구현 커밋을 push한 뒤, 실제 Supabase 정적 앱과
두 Edge Function에 환경변수를 설정·배포하고 `scripts/validate_runpod.py`를 두 coach 모드로 실행하는
것이다. 성공·실패와 관계없이 마지막에 Pod Stop 상태를 확인하고 이 보고서를 실제 수치로 대체해야 한다.
