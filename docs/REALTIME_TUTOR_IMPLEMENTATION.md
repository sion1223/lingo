# 실시간 선생님형 필기 튜터 구현 지시서

> 상태: **구현 기준 문서(canonical implementation contract)**  
> 우선순위: **P0 — 현재 최우선 개선 과제**  
> 기준 코드: `main`의 `811258de21812993efce812b34e4ba53b859029e`  
> 대상: Codex 및 이 저장소에서 작업하는 모든 개발 에이전트

## 1. 목표

현재 제품은 사용자가 글자를 모두 쓴 뒤 `채점하기`를 누르면 점수와 몇 개의 교정 문장을 한 번에 보여 준다. 이 흐름은 결과는 설명하지만, 사용자가 **어느 순간 잘못 쓰기 시작했는지**, **지금 그 획을 다시 써야 하는지**, **다음 획을 어디서 시작해야 하는지**를 즉시 알려 주지 못한다.

최종 목표는 다음과 같은 경험이다.

1. 사용자가 획을 시작하기 전에 다음 시작점이 필요할 때만 작게 표시된다.
2. 획을 긋는 동안 경로를 크게 벗어난 상태가 일정 시간 지속될 때만 조용한 시각 신호를 준다.
3. 펜을 떼면 즉시 한 가지 핵심 교정만 말한다. 예: `시작점을 조금 왼쪽으로 옮겨 보세요.`
4. 오류가 작으면 획을 인정하고 다음 획으로 진행한다. 큰 오류면 해당 획만 다시 쓰도록 제안한다.
5. 같은 오류가 반복되면 힌트 강도를 단계적으로 높인다.
6. 글자를 완성하면 무거운 AI 최종채점은 백그라운드에서 실행하고, 사용자는 기다리지 않고 복습 또는 재시도를 계속할 수 있다.
7. RunPod 또는 모델 서버가 꺼져 있어도 로컬 기하 피드백은 계속 동작한다.

핵심은 채팅형 문장을 많이 생성하는 것이 아니라, **관찰 → 진단 → 개입 결정 → 시각적 교정 → 학습 이력 반영**의 폐루프(closed-loop)를 만드는 것이다.

---

## 2. 현재 구현 진단

| 위치 | 현재 동작 | 실시간 튜터 관점의 문제 | 개선 방향 |
|---|---|---|---|
| `web/index.html` | Pointer Events로 좌표를 모으고 `strokes` 배열에 저장 | `pointerup` 뒤에도 피드백이 없고, 사용자가 `채점하기`를 눌러야 서버 호출 | 필기 중 로컬 분석과 획 종료 분석을 별도 계층으로 추가 |
| `web/index.html` | `lastReport`가 있을 때 획 전체를 품질 색으로 다시 그림 | 사후 결과만 보이며 어떤 구간을 어떻게 고칠지 직접 표시하지 못함 | 교정 전용 오버레이 캔버스에 시작점·종점·문제 구간·이동 벡터 표시 |
| `scorer/server.py` | `/score` 하나가 전체 분석을 수행 | 짧은 획 하나의 즉시 판정에도 무거운 Chandra 경로를 타게 됨 | `/coach/stroke` 경량 API와 기존 `/score` 심층 API 분리 |
| `scorer/chandra_scorer.py` | 기본 1회 채점 뒤 사용자 획마다 반사실 재채점 | 획이 `N`개면 대략 `N+1`회 모델 호출이 발생해 실시간 경로에 부적합 | 심층 최종채점에만 유지하고, 즉시 피드백에서는 1회 이하의 경량 추론 사용 |
| `scorer/server.py::_sanitize` | `grad`를 응답에서 제거 | 기존 좌표 모델의 방향 정보가 UI로 전달되지 않음 | 실시간용 구조화 오버레이 계약을 별도로 정의 |
| `scorer/hybrid.py` | Chandra와 경량 좌표 `Scorer`를 결합 | 이미 빠른 좌표 모델이 있으나 독립적인 실시간 엔진으로 노출되지 않음 | 경량 모델을 `/coach/stroke`에서 단독 사용하고 없으면 기하 규칙으로 폴백 |
| `web/edge-score.ts` | 최종채점 요청 제한시간 150초, 성공 시 제출 전체 저장 | 실시간 요청에 너무 긴 제한시간이며 이벤트마다 저장하면 비용·지연 증가 | `coach`는 2.5초 이내 제한, 로깅은 시도 종료 시 배치 |
| PR #1 | 오래된 DTW 기반 채점 구현 | 현재 `main`보다 오래됐고 획 인덱스를 단순 대응시키는 구조 | 통째 병합 금지. DTW 아이디어만 현재 데이터 구조와 테스트 기준에 맞춰 재구현 |

### 반드시 유지할 호환성

- 기존 `POST /score` 요청·응답은 깨지지 않아야 한다.
- 기존 `[x, y]` 좌표 배열 입력은 계속 지원해야 한다.
- 기존 1단계 따라쓰기와 2단계 암기쓰기 기능은 제거하지 않는다.
- Chandra 체크포인트, 경량 스트로크 체크포인트, KanjiVG 템플릿 형식은 변경하지 않는다.
- 기존 최종 점수의 의미를 바꾸려면 별도 보정 실험과 마이그레이션이 필요하다. 이번 작업에서 임의로 점수 척도를 바꾸지 않는다.

---

## 3. 목표 아키텍처: 세 단계 피드백 경로

```text
Pointer move
  └─ A. 브라우저 로컬 코치
       - 부분 궤적 정렬
       - 경로 이탈 감지
       - 시각 신호
       - 네트워크 없음

Pointer up
  ├─ A. 로컬 획 종료 판정 즉시 표시
  └─ B. POST /coach/stroke
       - 기하 지표 + 경량 좌표 Scorer 1회
       - 구조화된 진단과 다음 행동 반환
       - 로컬 판정을 보정

문자 완성
  └─ C. POST /score
       - Chandra/Hybrid 심층 채점
       - 반사실 분석과 최종 리포트
       - UI를 막지 않는 비동기 작업
```

### 3.1 지연시간 목표

모든 수치는 실제 장비에서 계측해 PR에 보고한다. 측정 없이 달성했다고 쓰지 않는다.

| 구간 | 목표 |
|---|---:|
| 로컬 `pointermove` 1회 분석 p95 | 8ms 이하 |
| 로컬 `pointerup` 판정 p95 | 50ms 이하 |
| 사용자가 첫 교정 신호를 보는 시간 | 150ms 이하 |
| `/coach/stroke` warm p95 | 400ms 이하 |
| `/coach/stroke` 클라이언트 hard timeout | 2.5초 |
| 서버 보정 결과가 UI에 반영되는 시간 | 700ms 이하 목표 |
| `/score` | UI 비차단이 필수. p50/p95를 기록하고 별도 최적화 |

`/coach/stroke`가 시간 안에 도착하지 않으면 로컬 판정을 유지한다. 네트워크 실패는 학습 흐름을 중단시키지 않는다.

---

## 4. 학습 세션 상태 머신

프런트엔드 상태를 전역 변수의 암묵적 조합으로 관리하지 말고 명시적 상태 머신으로 관리한다.

```text
IDLE
  -> READY_TO_DRAW
  -> DRAWING
  -> LOCAL_REVIEW
  -> WAITING_SERVER_REFINEMENT
  -> READY_NEXT_STROKE
  -> RETRY_CURRENT_STROKE
  -> FINALIZING
  -> SUMMARY
```

### 상태 전이 규칙

- `READY_TO_DRAW -> DRAWING`: 유효한 `pointerdown`.
- `DRAWING -> LOCAL_REVIEW`: `pointerup` 또는 `pointercancel`로 획이 종료되고 최소 길이를 만족.
- `LOCAL_REVIEW -> READY_NEXT_STROKE`: 오류가 없거나 경미하며 획을 인정.
- `LOCAL_REVIEW -> RETRY_CURRENT_STROKE`: 방향 반전, 완전히 다른 획, 큰 시작점 오류처럼 신뢰도 높은 중대 오류.
- `WAITING_SERVER_REFINEMENT`: 로컬 결과를 먼저 보여 준 뒤 서버 결과를 기다리는 논리 상태. UI 입력을 막지 않는다.
- 서버 응답은 `attempt_revision`과 `request_id`가 현재 값과 일치할 때만 반영한다.
- 사용자가 `undo`, `clear`, 문자 변경, 새 시도를 하면 이전 요청을 `AbortController`로 취소하고 revision을 증가시킨다.
- 예상 획 수를 채웠다고 즉시 완료 처리하지 않는다. 경량 엔진의 누락·추가 판단 또는 짧은 idle timer를 함께 사용한다.
- 최종채점은 기본적으로 문자 완성 뒤 700ms idle에서 자동 시작하되 `채점하기` 버튼은 수동 폴백으로 유지한다.

---

## 5. 입력 데이터 계약

### 5.1 점 형식

기존 입력과 호환하면서 시간·필압을 보존한다.

```ts
type LegacyPoint = [number, number];

type RichPoint = {
  x: number;              // 0..1 canvas 좌표
  y: number;              // 0..1 canvas 좌표
  t?: number;             // performance.now() 기준 상대 ms
  pressure?: number;      // PointerEvent.pressure, 보통 0..1
  tiltX?: number;
  tiltY?: number;
  pointerType?: "pen" | "touch" | "mouse";
};

type InputPoint = LegacyPoint | RichPoint;
```

- 서버는 두 형식을 모두 받아야 한다.
- 현재 모델 입력은 우선 `x`, `y`만 사용한다.
- 시간·필압·기울기는 진단 및 향후 학습을 위해 손실 없이 보존한다.
- 필압이나 속도를 글씨 모양 점수에 즉시 강하게 반영하지 않는다. 기기별 편차가 크므로 별도 보정 데이터가 생기기 전에는 보조 설명에만 사용한다.

### 5.2 경량 획 코칭 요청

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "session_id": "anonymous-session-id",
  "attempt_id": "uuid",
  "attempt_revision": 7,
  "char": "永",
  "mode": "trace|recall",
  "accepted_strokes": [
    [[0.2, 0.1], [0.3, 0.2]]
  ],
  "current_stroke": [
    {"x": 0.42, "y": 0.16, "t": 0, "pressure": 0.31},
    {"x": 0.48, "y": 0.20, "t": 34, "pressure": 0.38}
  ],
  "expected_template_index": 1,
  "client_metrics": {
    "path_error": 0.041,
    "direction_cosine": 0.92
  }
}
```

### 5.3 경량 획 코칭 응답

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "attempt_revision": 7,
  "engine": "geometry+stroke-model",
  "matched_template_index": 1,
  "expected_template_index": 1,
  "match_confidence": 0.94,
  "accepted": true,
  "severity": "minor",
  "primary_cue": {
    "code": "START_OFFSET",
    "text": "시작점을 조금 왼쪽으로 옮겨 보세요.",
    "confidence": 0.91,
    "anchor": {"x": 0.42, "y": 0.16},
    "vector": {"dx": -0.055, "dy": 0.006}
  },
  "metrics": {
    "start_error": 0.055,
    "end_error": 0.018,
    "path_error": 0.032,
    "shape_error": 0.021,
    "direction_cosine": 0.97,
    "length_ratio": 1.04,
    "model_quality": 0.82,
    "reverse_probability": 0.03,
    "order_error_probability": 0.08
  },
  "overlay": {
    "problem_segment": [[0.42, 0.16], [0.45, 0.18]],
    "target_segment": [[0.365, 0.166], [0.40, 0.19]],
    "next_start": {"x": 0.57, "y": 0.23}
  },
  "next_action": {
    "type": "draw_next",
    "template_index": 2,
    "hint_level": 0
  },
  "latency_ms": 87
}
```

응답은 자연어보다 **오류 코드, 좌표, 벡터, 신뢰도, 다음 행동**이 우선이다. UI 문구는 구조화된 증거에서 결정적으로 생성하며, 모델이 근거 없는 설명을 만들게 하지 않는다.

---

## 6. 실시간 진단 알고리즘

### 6.1 전처리

1. NaN, Infinity, 캔버스 범위를 크게 벗어난 좌표를 거부한다.
2. 연속 중복점과 극단적으로 짧은 이동을 제거한다.
3. 원래 시간 정보를 유지한 채 호길이 기준으로 24~32점에 재샘플링한다.
4. 템플릿과 사용자는 동일한 캔버스 좌표계 `[0, 1]`에서 비교한다.
5. 각 획을 독립적으로 0~1 정규화하지 않는다. 독립 정규화는 획의 실제 위치·크기 오류를 없애 버린다.
6. 모양 오차와 위치 오차를 분리하기 위해 다음 두 표현을 모두 만든다.
   - 절대 캔버스 좌표: 시작점·종점·위치·크기 진단
   - 중심 이동을 제거한 좌표: 순수 모양·곡률 진단

### 6.2 인과적 획 매칭

기존 `match_strokes()`의 전역 Hungarian 매칭은 완성된 글자의 사후 분석에는 쓸 수 있지만, 아직 쓰지 않은 미래 획까지 고려해 과거 매칭이 바뀔 수 있으므로 실시간 개입에는 그대로 사용하지 않는다.

실시간 엔진은 다음 원칙을 따른다.

- 현재 인정된 템플릿 prefix를 상태로 유지한다.
- 기본 후보는 예상 획 `k`, 바로 다음 획 `k+1`, 명시적으로 허용된 이체자/대체 필순 후보만 둔다.
- 누락·추가 획 페널티를 포함한 단조(monotonic) 동적계획 또는 작은 beam search를 사용한다.
- 한 번 높은 신뢰도로 인정한 과거 획을 미래 입력 때문에 임의로 재배정하지 않는다.
- 신뢰도가 낮으면 단정하지 말고 `uncertain`으로 반환해 최종채점에 판단을 넘긴다.

### 6.3 궤적 정렬

- 완성 획: Sakoe–Chiba band가 있는 DTW 또는 동등한 제한 정렬을 사용한다.
- 필기 중 부분 획: 사용자 prefix를 템플릿 prefix 후보들과만 비교한다.
- 계산량은 점 수를 제한해 브라우저에서 예측 가능하게 유지한다.
- PR #1의 코드는 참고만 한다. 그대로 cherry-pick하지 않는다.
  - 현재 데이터 파이프라인과 중복된다.
  - 획을 같은 인덱스로 단순 비교한다.
  - 경계 입력과 역추적 경로에 대한 테스트가 부족하다.

### 6.4 필수 지표

| 지표 | 정의/의도 |
|---|---|
| `start_error` | 사용자 첫 점과 템플릿 첫 점의 거리 |
| `end_error` | 사용자 마지막 점과 템플릿 마지막 점의 거리 |
| `path_error` | 정렬 경로를 따른 절대 좌표 평균 거리 |
| `shape_error` | 중심 이동을 제거한 뒤 정렬 경로 평균 거리 |
| `direction_cosine` | 전체 시작→끝 방향 벡터의 코사인 유사도 |
| `length_ratio` | 사용자 호길이 / 템플릿 호길이 |
| `bbox_shift` | 사용자와 템플릿 bounding-box 중심 차이 |
| `scale_ratio` | bounding-box 대각선 또는 면적 비율 |
| `curvature_hotspot` | 곡률 차이가 가장 큰 정렬 구간 |
| `model_quality` | 경량 `Scorer.q` 예측 |
| `reverse_probability` | 경량 모델 방향 반전 확률 |
| `order_error_probability` | 경량 모델 순서 오류 확률 |

기하 지표는 해석 가능성을 제공하고 경량 모델은 자연스러운 필체 변형에 대한 보정을 제공한다. 둘이 충돌하면 신뢰도를 낮추고 강한 개입을 하지 않는다.

### 6.5 실시간 경고 히스테리시스

필기 도중 매 프레임 경고가 깜빡이지 않도록 다음 기본값으로 시작하고 실제 로그로 보정한다.

- 경고 시작: 동일 오류 신뢰도 `>= 0.85`가 150ms 이상 지속.
- 경고 해제: 신뢰도 `< 0.60`.
- 동일 경고 재표시 cooldown: 600ms.
- 한 시점에 활성화되는 경고는 최대 1개.
- 펜이 빠르게 움직이는 동안 텍스트 팝업을 띄우지 않는다. 캔버스의 작은 색·화살표 신호만 사용한다.
- 중간 경로를 잠깐 벗어났다가 회복한 경우 획 종료 뒤 최종 정렬 결과를 우선한다.

---

## 7. 선생님형 개입 정책

### 7.1 오류 코드

문구를 직접 비교하거나 저장하지 말고 안정적인 오류 코드를 사용한다.

- `START_OFFSET`
- `END_OFFSET`
- `PATH_DEVIATION`
- `CURVE_EARLY`
- `CURVE_LATE`
- `DIRECTION_REVERSED`
- `WRONG_ORDER`
- `EXTRA_STROKE`
- `MISSING_STROKE`
- `TOO_SHORT`
- `TOO_LONG`
- `POSITION_OFFSET`
- `SCALE_MISMATCH`
- `UNCERTAIN_MATCH`

### 7.2 한 번에 한 가지 피드백

획 하나에 오류가 여러 개 있어도 다음 우선순위 함수로 가장 중요한 하나만 선택한다.

```text
priority = expected_learning_gain
         × diagnosis_confidence
         × severity
         × recurrence_weight
         × actionability
```

권장 기본 우선순위:

1. 잘못된 획 또는 필순
2. 방향 반전
3. 시작점 오류
4. 큰 경로·모양 오류
5. 종점 오류
6. 길이·크기·전체 위치 오류
7. 미세한 곡률·매끄러움

`점수를 높이려면 더 잘 쓰세요` 같은 비행동적 문구는 금지한다. 문장은 반드시 위치, 방향, 구간, 다음 행동 중 하나를 포함한다.

### 7.3 개입 강도

- `silent`: 정상 범위. 짧은 확인 표시만 제공.
- `nudge`: 작은 화살표와 한 문장. 현재 획 인정.
- `pause_and_retry`: 신뢰도 높은 중대 오류. 해당 획만 다시 쓰도록 제안.
- `guided_trace`: 같은 오류가 반복될 때 문제 구간 또는 전체 획을 반투명으로 표시.

자동으로 사용자 획을 지우거나 정답 경로에 스냅하지 않는다. 학습자가 직접 수정해야 한다.

### 7.4 반복 오류에 따른 적응

시도 이력은 최소한 다음을 유지한다.

```json
{
  "char": "永",
  "attempt_count": 3,
  "error_counts": {
    "START_OFFSET:stroke_1": 2,
    "DIRECTION_REVERSED:stroke_4": 0
  },
  "last_hint_level": {
    "stroke_1": 1
  }
}
```

- 첫 오류: 짧은 문장과 벡터.
- 같은 오류 2회: 정답 시작점과 짧은 목표 구간 표시.
- 같은 오류 3회 이상: 해당 획만 분리 연습하도록 제안.
- 교정 성공 시 힌트 강도를 한 단계 낮춘다.
- 같은 시도 안에서 동일 문구를 반복하지 않는다.

초기 버전은 브라우저 메모리 또는 `localStorage`에 저장한다. 계정 기반 장기 학습자 모델은 후속 단계다.

---

## 8. 파일별 구현 지시

### 8.1 프런트엔드

현재 단일 `web/index.html`을 프레임워크로 전면 재작성하지 않는다. 네이티브 ES module로 먼저 분리한다.

권장 구조:

```text
web/
  index.html
  styles.css
  app.js
  api.js
  coach/
    controller.js
    local-matcher.js
    metrics.js
    policy.js
    overlay.js
    session-memory.js
  tests/
    local-matcher.test.mjs
    policy.test.mjs
    stale-response.test.mjs
```

구현 사항:

1. `index.html`은 DOM 구조와 module import 중심으로 축소한다.
2. 사용자 잉크와 교정 표시를 분리한다.
   - 기존 캔버스: 사용자 필기
   - 새 오버레이 캔버스 또는 SVG: 화살표, 문제 구간, 다음 시작점
3. `pointermove`는 `requestAnimationFrame` 또는 최대 30Hz로 샘플링한다.
4. 원시 이벤트에서 `t`, `pressure`, `tiltX`, `tiltY`, `pointerType`을 기록한다.
5. 로컬 코치는 Web Worker가 필요할 만큼 느려지기 전에는 메인 스레드의 작은 순수 함수로 유지한다. p95가 목표를 넘으면 Worker로 이동한다.
6. `pointerup` 즉시 로컬 결과를 렌더링한 뒤 `/coach/stroke`를 비동기로 호출한다.
7. 서버 결과가 로컬 결과와 다르면 갑작스럽게 메시지를 교체하지 말고, 신뢰도가 더 높을 때만 부드럽게 보정한다.
8. `request_id`, `attempt_revision`, `AbortController`로 오래된 응답을 버린다.
9. 색만으로 상태를 구분하지 않는다. 아이콘·선 모양·짧은 텍스트를 같이 사용한다.
10. 음성·진동은 옵션이며 기본값은 꺼짐으로 둔다.

### 8.2 백엔드 경량 코치

새 파일을 추가한다.

```text
scorer/
  realtime.py
  schemas.py
  benchmark_realtime.py
```

`scorer/realtime.py`의 권장 책임:

- rich point에서 `x`, `y`와 메타데이터 분리
- 호길이 재샘플링
- 인과적 획 후보 선택
- banded DTW/부분 정렬
- 필수 기하 지표 계산
- 경량 `Scorer` 1회 추론
- 기하+모델 증거 결합
- 오류 코드와 오버레이 생성
- 힌트 정책에 필요한 구조화 결과 반환

`FastCoachEngine`은 다음 모드를 지원한다.

1. `geometry-only`: 경량 체크포인트가 없거나 로드 실패.
2. `geometry+stroke-model`: 기본 운영 모드.

중요 제한:

- `/coach/stroke`에서 Chandra 비전 타워를 호출하지 않는다.
- 획별 반사실 `N+1` 추론을 하지 않는다.
- 한 요청당 경량 모델 forward는 최대 1회가 기본이다.
- 템플릿과 템플릿 tensor/feature를 문자별로 캐시한다.
- 서버 응답에 내부 예외 메시지, 파일 경로, 비밀 환경변수를 노출하지 않는다.

### 8.3 `scorer/server.py`

추가 엔드포인트:

```text
POST /coach/stroke
POST /attempt/events      # 선택: 시도 종료 배치 로그
```

모델 로더를 분리한다.

- `_vision_model`: 기존 `/score` 전용, 느린 lazy load.
- `_stroke_model`: `/coach/stroke` 전용, 빠른 lazy load.
- `_coach_engine`: 기하 엔진 + 선택적 경량 모델.

`/health` 응답도 분리 상태를 표시한다.

```json
{
  "ok": true,
  "coach_ready": true,
  "coach_engine": "geometry+stroke-model",
  "deep_score_ready": false,
  "deep_model_loading": true
}
```

경량 코치가 준비되어 있으면 Chandra가 아직 로딩 중이어도 웹앱은 연습 가능 상태로 표시한다.

### 8.4 `web/edge-score.ts`

- `action: "coach"`를 `/coach/stroke`로 전달한다.
- timeout은 2.5초로 둔다.
- `action: "score"`는 기존 동작을 유지한다.
- RunPod URL은 소스 상수 대신 환경변수에서 읽고, 누락 시 명확한 503을 반환한다.
- `coach` 요청마다 DB insert를 하지 않는다.
- 시도 종료 시 이벤트를 한 번에 저장한다.
- 저장 실패가 사용자 피드백 응답을 실패시키지 않게 한다.

### 8.5 심층 최종채점

- 기존 `/score` 응답 계약은 유지한다.
- 프런트엔드에서 비차단으로 호출한다.
- 서버의 반사실 분석은 후속 최적화에서 배치 추론을 검토한다.
- 최종 리포트는 획별 색칠만 하지 말고 경량 코치에서 사용한 오류 코드와 가능한 한 정렬한다.
- 로컬/경량/심층 결과가 다를 때는 엔진 이름과 신뢰도를 개발 로그에 남긴다.

### 8.6 데이터 로깅

필기 중 모든 `pointermove`를 개별 DB 행으로 저장하지 않는다. 시도 종료 시 압축된 하나의 이벤트 묶음으로 저장한다.

최소 필드:

- 익명 `session_id`, `attempt_id`
- 문자, 학습 모드, 시도 번호
- 원시 획 좌표와 선택적 시간·필압
- 각 획의 진단 코드·신뢰도·개입 강도
- 재시도 여부와 다음 시도에서 개선됐는지
- 로컬 및 서버 지연시간
- 최종 점수
- 클라이언트/프로토콜 버전

개인 식별 정보는 수집하지 않는다. 실제 사용자 데이터를 학습에 재사용하기 전에는 명시적 동의, 보존 기간, 삭제 경로를 설계한다.

---

## 9. 테스트와 평가

### 9.1 Python 단위 테스트

새 테스트 폴더를 만든다.

```text
tests/
  test_realtime_metrics.py
  test_realtime_matching.py
  test_realtime_policy.py
  test_server_contract.py
  test_legacy_score_contract.py
```

필수 케이스:

- 완벽한 획
- 일정한 평행 이동
- 시작점만 오류
- 종점만 오류
- 방향 반전
- 너무 짧음/김
- 곡률이 너무 이르거나 늦음
- 예상 획과 다음 획이 유사한 문자
- 잘못된 획 순서
- 추가 획, 누락 획
- 점 하나뿐인 획, 중복점, 0길이 획
- NaN/Infinity/과도한 점 수
- 이체자 또는 허용 대체 필순
- 경량 체크포인트 부재 시 geometry-only 폴백
- 기존 `/score` 요청과 응답의 회귀 방지

KanjiVG 템플릿에 제어된 변형을 가해 golden fixture를 생성한다. 임계값을 테스트 데이터에 맞춰 매번 느슨하게 바꾸지 않는다.

### 9.2 JavaScript 테스트

순수 함수는 브라우저 없이 `node --test`로 검증 가능하게 작성한다.

필수 케이스:

- 부분 DTW가 사용자의 prefix만 비교하는지
- 히스테리시스가 경고 깜빡임을 막는지
- 한 번에 하나의 cue만 선택하는지
- 반복 오류에 따라 hint level이 상승하는지
- undo/clear 후 오래된 서버 응답이 무시되는지
- 좌표 배열과 rich point가 동일한 기하 결과를 내는지

### 9.3 E2E 및 수동 검증

- iPad Safari + Apple Pencil
- 데스크톱 Chrome + 마우스
- 터치 기기에서 손바닥 입력 무시
- RunPod online/offline 전환
- 느린 네트워크와 요청 timeout
- 문자 변경 중 응답 도착
- 여러 번 빠르게 undo/clear

### 9.4 측정 지표

기술 지표:

- 로컬 분석 p50/p95
- `/coach/stroke` p50/p95와 timeout 비율
- stale response 폐기 횟수
- geometry-only 폴백 비율
- 최종채점 대기 중 UI block 시간

학습 효과 지표:

- 같은 오류가 다음 시도에서 감소한 비율
- 한 글자를 기준 이상으로 쓰기까지 필요한 시도 수
- 과도한 개입률: 정상 획에 경고한 비율
- 사용자가 피드백 직후 해당 획을 다시 쓴 비율
- 힌트를 끈 사용자 비율

초기 오프라인 평가에서는 합성 오류 라벨로 precision/recall을 측정한다. 실제 사용자 데이터가 쌓이면 사람 검토 표본으로 보정한다.

---

## 10. 단계별 구현 순서

한 번에 전체를 구현하지 않는다. 각 단계는 독립적으로 실행·검증 가능해야 한다.

### Phase 0 — 기준선과 테스트 기반

- [ ] 현재 `main`에서 재현 가능한 실행 명령 정리
- [ ] `requirements-dev.txt` 또는 동등한 개발 의존성 파일 추가
- [ ] 기존 `/score` 계약 테스트 추가
- [ ] 대표 문자 5~10개의 합성 오류 fixture 추가
- [ ] 현재 최종채점 지연시간 p50/p95 측정 스크립트 추가
- [ ] 비밀키·URL을 새 코드에 하드코딩하지 않는 환경설정 경로 마련

완료 조건: 동작 변경 없이 테스트와 기준선 보고서가 존재한다.

### Phase 1 — 브라우저 로컬 코치 MVP

- [ ] `web/index.html`을 네이티브 module로 분리
- [ ] rich point 수집
- [ ] 로컬 재샘플링, 부분/완성 획 정렬, 핵심 지표 계산
- [ ] 오버레이 캔버스
- [ ] 상태 머신, revision, stale response 방지
- [ ] 한 번에 한 cue 정책
- [ ] 서버가 완전히 꺼져 있어도 동작

완료 조건: `永`, `水`, `木`, `日`, `語`에서 시작점 오류·방향 반전·큰 경로 이탈을 펜을 뗀 직후 설명하며, 네트워크 없이도 연습 가능하다.

### Phase 2 — 경량 서버 코치

- [ ] `scorer/realtime.py`, `schemas.py` 추가
- [ ] geometry-only 엔진
- [ ] 기존 경량 `Scorer` 단독 로드와 1회 추론
- [ ] `POST /coach/stroke`
- [ ] edge function `coach` 라우팅과 2.5초 timeout
- [ ] 로컬 결과의 비동기 보정
- [ ] warm p95 벤치마크

완료 조건: Chandra가 꺼져 있거나 로딩 중이어도 경량 코치가 응답하고, 기존 `/score`는 회귀하지 않는다.

### Phase 3 — 적응형 선생님 정책

- [ ] 오류 코드와 문구 사전 확정
- [ ] 반복 오류 memory
- [ ] `silent/nudge/pause_and_retry/guided_trace` 정책
- [ ] 다음 시작점 및 문제 구간 표시
- [ ] 획 분리 연습 모드
- [ ] 과잉 개입률 측정

완료 조건: 같은 오류가 반복될수록 힌트가 강해지고, 성공하면 다시 약해진다.

### Phase 4 — 비동기 심층채점과 데이터 루프

- [ ] 문자 완성 자동 감지
- [ ] `/score` 비차단 호출
- [ ] 최종 리포트와 실시간 오류 코드 통합
- [ ] 시도 종료 배치 로깅
- [ ] latency/학습효과 대시보드용 데이터 계약
- [ ] 반사실 분석 batch 최적화 검토

완료 조건: 사용자는 심층채점을 기다리지 않고 다음 행동을 할 수 있고, 결과가 늦게 와도 현재 시도를 덮어쓰지 않는다.

### Phase 5 — 실제 데이터 기반 보정

- [ ] 사람 검토 데이터셋 작성
- [ ] 기기별 필압·속도 편차 분석
- [ ] 임계값 보정과 모델 calibration
- [ ] 이체자·대체 필순 지원
- [ ] 개인별 숙련도 모델과 복습 스케줄

Phase 5는 충분한 동의 기반 데이터가 생기기 전에는 시작하지 않는다.

---

## 11. 완료 정의

실시간 튜터 기능은 다음 조건을 모두 만족해야 완료다.

1. 사용자는 최종 `채점하기`를 누르지 않아도 획별 피드백을 받는다.
2. 필기 중 네트워크 요청을 매 포인트 보내지 않는다.
3. 첫 교정 신호가 로컬에서 150ms 안에 보인다.
4. 경량 서버가 실패하거나 꺼져도 학습 흐름이 유지된다.
5. 한 번에 한 가지 행동 가능한 교정만 표시한다.
6. 큰 오류와 작은 오류의 다음 행동이 다르다.
7. undo/clear/문자 변경 뒤 오래된 응답이 현재 화면을 오염시키지 않는다.
8. 기존 `/score`와 기존 좌표 배열 입력이 그대로 동작한다.
9. 자동화 테스트와 iPad 수동 검증 결과가 PR에 기록된다.
10. 지연시간 수치는 실제 측정값으로 보고된다.
11. 새 체크포인트·대형 데이터·로그 파일이 Git에 추가되지 않는다.
12. 비밀키와 배포 URL을 새 코드에 하드코딩하지 않는다.

---

## 12. 명시적 비목표

이번 개선에서 다음을 먼저 하지 않는다.

- React/Vue 등으로 프런트엔드 전체 재작성
- 필기 중 매 포인트를 서버 또는 LLM에 전송
- 범용 대화형 LLM을 실시간 hot path에 배치
- 사용자 획 자동 스냅 또는 자동 수정
- Chandra보다 더 큰 모델 추가
- PR #1 전체 병합
- 실제 데이터 없이 필압·속도를 점수에 강하게 반영
- 근거 없이 자연어 설명을 생성
- 기존 최종 점수 척도 변경

---

## 13. Codex가 처음 수행할 작업

별도 요청이 없으면 Codex는 **Phase 0과 Phase 1만** 수행한다. 다음 순서를 지킨다.

1. 현재 브랜치와 HEAD를 기록하고 관련 파일을 다시 읽는다.
2. 기존 동작을 재현하고 최소 회귀 테스트를 먼저 추가한다.
3. `web/index.html`을 기능 변경 없이 모듈로 분리하는 커밋을 만든다.
4. 로컬 코치의 순수 함수와 테스트를 추가한다.
5. 상태 머신과 오버레이를 연결한다.
6. 서버를 끈 상태와 켠 상태를 모두 검증한다.
7. 실제 측정한 지연시간과 남은 한계를 문서화한다.
8. 이후 Phase 2 구현은 별도 커밋 또는 별도 PR로 분리한다.

작업 결과 보고에는 반드시 다음을 포함한다.

- 변경 파일과 각 파일의 책임
- 실행한 테스트 명령과 결과
- 실제 측정한 p50/p95 지연시간
- 기존 `/score` 호환성 결과
- 검증하지 못한 기기·환경
- 다음 단계에서 해결할 위험
