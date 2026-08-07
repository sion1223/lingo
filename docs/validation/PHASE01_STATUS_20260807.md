# Phase 0·1 진행 감사 — 2026-08-07

## 결론

로컬 Gate A 기준으로 Phase 0과 Phase 1은 **PASS**다. 자동화 테스트, 결정적 fixture,
CPU 지연시간 기준선, API가 없는 상태의 데스크톱 브라우저 검증 근거가 있다. 실제 RunPod,
iPad Safari, Apple Pencil은 이 감사 범위에서 실행하지 않았으며 아래에 `NOT RUN`으로 남긴다.

## 저장소 상태

- branch: `main`
- 감사 시작 HEAD: `f8d3522663f45e693caae7167eb106c1303a5123`
- 당시 `origin/main`: `811258de21812993efce812b34e4ba53b859029e`
- main merge-base: `811258de21812993efce812b34e4ba53b859029e`
- Phase 0·1 구현 커밋: `6f51b33e1c111b8592fe389e26bb9aaf15ce057b`
- 사용자 선행 커밋 `f8d3522`는 보존했고 관련 없는 변경을 되돌리지 않았다.

## 완료 조건 감사

| 항목 | 판정 | 근거 |
|---|---|---|
| Phase 0 재현 명령·개발 의존성 | PASS | `requirements-dev.txt`, `README.md` |
| 결정적 대표 문자 fixture | PASS | `scripts/generate_realtime_fixtures.py`, `tests/fixtures/realtime-strokes.json` |
| 기존 `POST /score` 계약 고정 | PASS | `tests/test_legacy_score_contract.py` |
| 네이티브 ES module 분리 | PASS | `web/index.html`, `web/app.js`, `web/api.js`, `web/coach/*` |
| 따라쓰기·암기쓰기 유지 | PASS | `web/app.js`의 stage 1/2 전환과 기존 최종 채점 흐름 |
| legacy/rich point 호환 | PASS | `web/tests/local-matcher.test.mjs`, `/score` 직전 legacy 변환 |
| 네트워크 없는 로컬 코치 | PASS | 내장 5문자 템플릿, API 404 상태의 데스크톱 브라우저 수동 검증 |
| 인과적 prefix와 한 번에 한 cue | PASS | `local-matcher.test.mjs`, `policy.test.mjs` |
| stale response 폐기 | PASS | `requestId`, `attemptRevision`, `AbortController`, `stale-response.test.mjs` |
| 로컬 지연시간 실측 | PASS | `docs/REALTIME_TUTOR_BASELINE.md`, `web/benchmark-local.mjs` |
| 새 모듈의 비밀·배포 URL 하드코딩 방지 | PASS | 런타임 `LINGO_CONFIG`와 빈 meta 설정 사용 |
| 데스크톱 Chromium + 마우스 | PASS | API 404 상태에서 정상/시작점 이동/방향 반전 수동 확인 |
| iPad Safari + Apple Pencil | NOT RUN | 실제 기기 없음 |
| 실제 RunPod `/score` p50/p95 | NOT RUN | Phase 0·1 당시 Pod를 시작하지 않음 |
| 실제 Supabase Edge E2E | NOT RUN | Phase 0·1 당시 배포하지 않음 |

## 실행 결과

```text
python -m pytest -q
4 passed

node --test web/tests/*.test.mjs
17 passed
```

`node web/benchmark-local.mjs`를 Windows x64, Node.js v22.18.0에서 워밍업 100회 후
실행했다.

| 경로 | 표본 | p50 | p95 | 최대 |
|---|---:|---:|---:|---:|
| pointermove 부분 분석 | 5,000 | 0.038ms | 0.062ms | 1.864ms |
| pointerup 완성 획 | 2,000 | 0.347ms | 0.695ms | 2.096ms |

## 남은 위험

- 실제 펜의 필압·기울기·손바닥 무시와 화면 회전은 검증되지 않았다.
- 최종 Chandra 채점의 실제 GPU 지연시간과 Supabase 프록시 계약은 Phase 2 원격 Gate에서
  검증해야 한다.
- 잘못된 획을 자동 삭제하지 않으므로 사용자가 직접 undo하지 않으면 최종 채점에서 추가 획으로
  해석될 수 있다. 이는 학습자 입력을 보존하기 위한 의도적 현재 정책이다.
