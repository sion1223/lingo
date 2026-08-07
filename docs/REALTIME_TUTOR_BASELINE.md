# 실시간 필기 튜터 Phase 0·1 기준선

기준일: 2026-08-07  
구현 시작 HEAD: `f8d3522663f45e693caae7167eb106c1303a5123` (`main`, `origin/main`보다 1커밋 앞섬)  
범위: GitHub PR #2의 지시 중 Phase 0과 Phase 1만 수행

## 재현 명령

```bash
python -m pip install -r requirements-dev.txt
python scripts/generate_realtime_fixtures.py
python -m pytest -q
node --test web/tests/*.test.mjs
node web/benchmark-local.mjs
```

기존 심층 최종채점의 HTTP 지연시간은 서버가 준비된 환경에서 다음처럼 측정한다.
배포 URL·인증키는 환경변수 또는 CLI 인자로만 전달한다.

```bash
python -m scorer.benchmark_score --url http://127.0.0.1:8000/score --samples 10
```

Supabase Edge Function을 측정할 때는 `--mode edge`를 추가하고 필요한 경우
`LINGO_API_KEY` 환경변수를 사용한다.

## 자동화 결과

- `python -m pytest -q`: 4 passed. 기존 `[x,y]` 입력과 `/score` 응답 키, 비유한 좌표 거부,
  모듈·스타일 정적 제공을 검증했다.
- `node --test web/tests/*.test.mjs`: 17 passed. `永`, `水`, `木`, `日`, `語`의 완벽한 획,
  시작점 이동, 경로 이탈, 방향 반전과 잘못된 순서·추가 획·중복점·NaN/Infinity,
  legacy/rich point 동등성, 히스테리시스, stale response 폐기를 검증했다.
- fixture는 KanjiVG 템플릿에 결정적인 제어 변형을 적용해
  `tests/fixtures/realtime-strokes.json`으로 생성한다.

## 로컬 지연시간

측정 환경: Windows x64, Node.js v22.18.0, CPU 실행. 워밍업 100회 뒤 순수 기하 함수만 측정했다.
DOM 렌더링·네트워크·브라우저 이벤트 전달 시간은 포함하지 않는다.

| 경로 | 표본 | p50 | p95 | 최대 |
|---|---:|---:|---:|---:|
| `pointermove` 부분 획 분석 | 5,000 | 0.038ms | 0.062ms | 1.864ms |
| `pointerup` 완성 획 판정 | 2,000 | 0.347ms | 0.695ms | 2.096ms |

목표인 `pointermove` p95 8ms와 `pointerup` p95 50ms 안에 들어왔다.
수치는 `node web/benchmark-local.mjs` 한 번의 실제 실행 결과이며 기기별로 다시 측정해야 한다.

## 브라우저·오프라인 검증

로컬 정적 서버만 켜고 API 경로는 모두 404가 되는 상태에서 데스크톱 인앱 Chromium과 마우스로 확인했다.

- 서버 상태가 `최종 채점 서버 오프라인 · 로컬 코치는 사용 가능`으로 표시됐다.
- 내장 `永` 템플릿으로 정상 획을 쓰면 다음 획 안내가 즉시 표시됐다.
- 시작점을 옮긴 획은 `START_OFFSET` 한 가지 cue만 표시됐다.
- 역방향 획은 `DIRECTION_REVERSED`와 `RETRY_CURRENT_STROKE`로 판정됐다.
- 브라우저 콘솔 오류·경고는 없었다.

현재 RunPod 프록시의 `/health`는 HTTP 404였고 서버를 켜지 않았다. 따라서 실제 Chandra `/score`
p50/p95와 온라인 최종채점은 이번 로컬 측정에 포함하지 않는다. 기존 `/score` 계약은 mock 기반 HTTP
회귀 테스트로 검증했으며, GPU 서버가 켜진 운영 검증에서 `scorer.benchmark_score`를 별도로 실행해야 한다.

## 런타임 설정과 호환성

- 브라우저 내부 획은 `{x, y, t, pressure, tiltX, tiltY, pointerType}`를 보존한다.
- 기존 `/score`에는 호출 직전에 계속 `[x,y]` 배열로 변환해 보낸다.
- 배포 URL과 키는 빈 meta 또는 `window.LINGO_CONFIG` 런타임 주입으로 설정한다.
- 기본 설정은 동일 출처 API이며, 새 모듈에 개인 URL·키를 넣지 않았다.
- 서버 결과가 늦게 도착하면 `requestId`, `attemptRevision`, `AbortController`로 폐기한다.

## 미검증 환경과 남은 위험

- iPad Safari + Apple Pencil, 실제 터치 기기의 손바닥 무시, 화면 회전은 검증하지 못했다.
- RunPod 온라인 상태, Chandra 최종채점의 실제 p50/p95, Supabase Edge 배포는 검증하지 못했다.
- 재시도가 필요한 잘못된 획은 자동 삭제하지 않으므로 사용자가 지우지 않고 다시 쓰면 최종 `/score`에서
  추가 획으로 보일 수 있다. 학습자 의도를 보존하기 위한 현재 선택이며 후속 UX 검증이 필요하다.
- Phase 2의 `/coach/stroke`, 경량 모델 보정, Edge Function coach 라우팅은 구현하지 않았다.
