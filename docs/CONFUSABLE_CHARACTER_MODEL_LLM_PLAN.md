# 유사 문자 판별 강화 + 구조화 LLM 교사 피드백 실행 계획

> 상태: **모델 품질 개선의 canonical execution contract**  
> 대상 모델: `ChandraScorer` 비전 모델 + 좌표 기반 `Scorer` 스트로크 모델  
> 대표 실패 사례: 히라가나 `い`와 `り`처럼 전체 인상은 비슷하지만 일부 획의 시작점·길이·방향·곡률로 구분되는 문자  
> 선행 게이트: [`NEXT_PHASE_RUNPOD_VALIDATION.md`](NEXT_PHASE_RUNPOD_VALIDATION.md)의 Phase 2 및 실제 RunPod 검증  
> 핵심 원칙: **모델이 무엇이 틀렸는지 구조화된 근거로 확정하고, LLM은 그 근거를 교사식 표현으로 변환한다.**

---

## 0. 결론과 우선순위

현재 두 채점 모델은 같은 문자의 정답 템플릿을 왜곡한 합성 글씨를 주로 학습한다. 이 방식은 `정답 글자를 얼마나 반듯하게 썼는가`를 배우는 데는 유효하지만, `다른 글자를 잘 썼는데 목표 글자와 비슷해 보이는 경우`를 직접 학습하지 않는다.

예를 들어 목표가 `い`인데 사용자가 `り`에 가까운 모양을 쓴 경우, 현재 모델은 다음 질문을 명시적으로 풀지 않는다.

1. 이 글씨는 정말 `い`인가.
2. 아니면 `り`가 더 가까운 후보인가.
3. 두 후보를 구분하는 결정적 획과 구간은 어디인가.
4. 사용자가 다음 시도에서 무엇을 바꾸면 `い` 쪽으로 이동하는가.

따라서 모델 크기나 에폭만 늘리는 방식보다 다음 네 축을 우선한다.

1. **confusion graph**: 유사 문자 후보를 자동·수동으로 관리한다.
2. **hard-negative 학습**: 목표 문자와 유사한 다른 문자를 직접 경쟁시킨다.
3. **identity와 quality 분리**: `무슨 글자인가`와 `그 글자를 얼마나 잘 썼는가`를 별도 출력으로 학습한다.
4. **structured teacher evidence + LLM**: 모델의 판정을 바꾸지 않고, 근거에 충실한 교사식 설명만 생성한다.

이번 계획에서 가장 먼저 구현할 것은 LLM이 아니라 **유사 문자 전용 벤치마크와 hard-negative 데이터 파이프라인**이다. 벤치마크 없이 모델 개선이나 LLM 설명 품질을 주장하지 않는다.

---

## 1. 현재 코드 기준 진단

### 1.1 공통 데이터 병목

현재 `scorer/synth.py::make_scoring_sample()`은 한 문자 템플릿에 회전·크기·이동·지터·방향 반전·인접 획 순서 교환을 가한다. 그러나 다른 문자의 획이나 전체 템플릿을 사용한 negative pair가 없다.

따라서 다음 유형이 학습 분포에 충분히 포함되지 않는다.

- 목표 `い`, 실제 글씨 `り`
- 목표 `い`, 1획은 `い`에 가깝지만 2획만 `り`에 가까운 글씨
- 목표 `り`, 전체 배치는 비슷하지만 2획이 너무 짧고 오른쪽 아래로 기울어 `い`처럼 보이는 글씨
- 두 문자 사이 경계에 있는 애매한 글씨
- 다른 문자를 매우 반듯하게 쓴 경우

### 1.2 좌표 기반 스트로크 모델 병목

현재 좌표 모델은 다음 특성을 가진다.

- 점 특징: `[x, y, dx, dy, stroke_start]`
- 획당 기본 재샘플링: 12점
- 사용자 획과 목표 템플릿을 각각 Transformer로 인코딩
- 사용자 획이 목표 템플릿 획을 cross-attention으로 참조
- 출력: 전체 점수, 획별 품질, 방향 반전, 순서 오류
- 인식 사전학습: 전체 문자 flat softmax + cross entropy
- 채점 검증: 합성 글씨의 MSE, 방향·순서 정확도 중심

이 구조의 문제는 다음과 같다.

1. 12점 재샘플링은 짧은 꼬리, 완만한 굴곡, 획 후반 방향 변화 같은 미세 차이를 희석할 수 있다.
2. 시작·끝 위치, 획 길이, 접선 방향, 곡률, 획 사이 간격을 명시적 관계 특징으로 제공하지 않는다.
3. 채점 헤드는 목표 템플릿 하나만 보고 점수를 내므로 유사 후보와의 상대적 margin을 학습하지 않는다.
4. 문자 인식 사전학습은 쉬운 클래스와 어려운 클래스에 동일한 flat 분류 손실을 사용한다.
5. validation에서 `다른 글자를 목표 글자로 잘못 인정한 비율`을 측정하지 않는다.

### 1.3 Chandra 비전 모델 병목

현재 Chandra 경로는 다음 방식이다.

- 사용자 획을 448×448의 시간 인코딩 3채널 이미지로 렌더링
- 채널: 잉크, 획 내부 진행도, 획 순서
- 사용자 이미지와 목표 템플릿 이미지를 별도로 비전 타워에 통과
- 사용자 획별 pooling 후 템플릿 토큰과 cross-attention
- 기본값에서는 비전 타워 전체를 동결
- 같은 문자 템플릿을 왜곡한 데이터로 채점 헤드를 학습

이 구조의 문제는 다음과 같다.

1. 사용자와 템플릿의 차이 영상이나 국소적인 discriminative region을 직접 입력하지 않는다.
2. 전체 448 이미지의 patch 표현에 의존하므로, 글자 전체에 비해 작은 획 후반의 차이가 약해질 수 있다.
3. 목표 문자와 유사 후보를 동시에 경쟁시키는 identity/mismatch head가 없다.
4. 기본 동결 백본은 시간 인코딩 채널과 일본어 필기 미세 차이에 충분히 적응하지 못할 수 있다.
5. 평가는 합성 quality MSE 위주이며, 유사 문자 false acceptance를 직접 최적화하지 않는다.

### 1.4 하이브리드 결합 병목

현재 `scorer/calibrate_hybrid.py`는 전체 holdout 합성 샘플의 MSE를 최소화하는 전역 선형 가중치를 구한다.

이 방식은 다음을 반영하지 못한다.

- 문자 쌍별로 어느 모델이 강한지
- 비전 모델과 좌표 모델의 불확실성
- 방향·순서 문제에서는 좌표 모델이 더 신뢰할 만한 상황
- 국소 형태 문제에서는 비전 모델이 더 신뢰할 만한 상황
- 두 모델이 크게 불일치할 때 보류해야 하는 상황

### 1.5 문제를 잘못 정의하면 안 되는 이유

목표가 `い`인데 사용자가 `り`를 매우 잘 썼다면 다음 두 값은 다르다.

- `written_form_quality`: 작성된 글자 자체의 필기 품질
- `target_identity_match`: 작성된 글자가 과제의 목표 문자와 일치하는 정도

현재처럼 하나의 overall score로만 처리하면 `다른 글자를 반듯하게 쓴 경우`와 `목표 글자를 서툴게 쓴 경우`를 구분하기 어렵다.

새 모델은 최소한 다음을 분리해야 한다.

```text
identity: 이 글씨가 어느 문자에 가까운가
quality: 가장 가까운 문자 기준으로 필기 품질은 어떤가
task score: 목표 문자 일치도와 품질을 결합한 과제 점수
```

기존 API의 `score` 필드는 유지하되 새 구조화 필드를 선택적으로 추가한다.

---

## 2. 대표 사례 `い` ↔ `り`

저장소의 KanjiVG 템플릿을 기준으로 두 문자는 모두 2획이지만 2획의 기하가 크게 다르다.

### `い`의 주요 특징

- 2획 시작점이 `り`보다 상대적으로 낮다.
- 2획이 비교적 짧다.
- 2획의 주 방향이 오른쪽 아래 대각선에 가깝다.
- 글자 아래쪽까지 길게 떨어지는 세로 꼬리가 없다.

### `り`의 주요 특징

- 2획이 더 높은 위치에서 시작한다.
- 2획이 길고 세로 방향 성분이 강하다.
- 아래쪽으로 깊게 내려간 뒤 하단에서 왼쪽 방향 곡률이 나타난다.
- 전체 세로 점유 범위가 더 크다.

이 차이를 사람이 작성한 고정 문구로만 관리하지 않는다. 템플릿 쌍에서 자동 계산한 `pair delta profile`을 저장한다.

예시:

```json
{
  "pair_id": "hiragana_3044_308a",
  "target": "い",
  "competitor": "り",
  "critical_strokes": [1],
  "critical_regions": ["stroke_2_start", "stroke_2_lower_tail"],
  "feature_deltas": {
    "stroke_2_start_y": "target_lower",
    "stroke_2_length": "target_shorter",
    "stroke_2_primary_angle": "target_more_down_right",
    "stroke_2_vertical_extent": "target_smaller",
    "stroke_2_terminal_curvature": "competitor_leftward_tail"
  }
}
```

사용자가 목표 `い`에 대해 `り`에 가까운 글씨를 쓰면 모델은 다음과 같은 구조화 판정을 만들어야 한다.

```json
{
  "target_char": "い",
  "nearest_competitor": "り",
  "target_identity_probability": 0.18,
  "competitor_probability": 0.74,
  "confusion_margin": -0.56,
  "critical_stroke": 1,
  "evidence_codes": [
    "STROKE_TOO_VERTICAL",
    "STROKE_TOO_LONG",
    "START_TOO_HIGH",
    "END_TOO_LOW"
  ],
  "next_action": "RETRY_CRITICAL_STROKE"
}
```

그 뒤 LLM 또는 고정 폴백은 다음처럼 말할 수 있다.

> 2획이 길고 세로로 내려가 `り`처럼 보입니다. `い`의 2획은 오른쪽 중간에서 시작해 짧게 오른쪽 아래로 마무리하세요.

이 문장은 모델이 제공하지 않은 새로운 사실을 추가해서는 안 된다.

---

## 3. 제품 및 모델 불변조건

- LLM은 문자 판정, 점수, 오류 코드, critical stroke, 다음 행동을 변경하지 않는다.
- `pointermove` 및 즉시 `pointerup` hot path에서 LLM을 호출하지 않는다.
- 원시 필기 이미지와 전체 좌표를 LLM에 기본 전송하지 않는다.
- 기존 `/score` 요청·응답과 `[x, y]` 입력을 깨지 않는다.
- 기존 quality, direction, order 기능을 제거하지 않는다.
- 유사 문자 개선을 위해 정상 글자의 acceptance가 과도하게 떨어지지 않게 한다.
- 모호한 글씨는 억지로 한 문자로 확정하지 않고 `AMBIGUOUS_BETWEEN_CHARACTERS`를 허용한다.
- 하나의 합성 generator와 같은 seed family를 train/test에 공유하지 않는다.
- 모델 성능 향상은 실제 confusion benchmark와 실제 RunPod 결과 없이 주장하지 않는다.
- 외부 데이터와 사용자 데이터는 라이선스·동의·보존 정책을 확인한 뒤 사용한다.

---

## 4. Confusion benchmark를 먼저 만든다

### 4.1 confusion registry

다음 파일을 버전 관리한다.

```text
configs/confusions/
  kana_seed_v1.yaml
  schema.json
```

초기 seed에는 최소 `い↔り`를 반드시 포함한다. 추가 쌍은 자동 mining 결과와 일본어 필기 검토를 거쳐 넣는다. 단순히 사람이 기억나는 유사 문자를 대량 나열하지 않는다.

권장 스키마:

```yaml
version: 1
pairs:
  - id: hiragana_3044_308a
    chars: ["い", "り"]
    script: hiragana
    source: [manual_seed, template_mining]
    expected_stroke_counts: [2, 2]
    critical_strokes: [1]
    review_status: reviewed
    notes: "2획 시작 높이·길이·세로 범위·하단 곡률"
```

### 4.2 자동 confusion graph 생성

새 모듈의 권장 책임:

```text
scorer/confusions.py
scorer/build_confusion_graph.py
```

각 템플릿에 다음 descriptor를 계산한다.

#### 전체 글자 descriptor

- stroke count
- normalized bounding box와 aspect ratio
- occupancy grid
- signed distance transform 또는 skeleton distance
- 전체 중심과 주성분 방향
- 획 시작점·끝점 분포
- 획 간 상대 위치 그래프

#### 획별 descriptor

- arc length
- 시작점·끝점
- centroid와 bounding box
- 시작·중간·끝 접선의 `sin/cos`
- curvature profile
- 수평·수직 extent
- loop/intersection 여부
- 다른 획과의 최소 거리
- banded DTW 또는 Soft-DTW 계열 거리

후보 생성 절차:

1. 동일 script 안에서 우선 검색한다.
2. stroke count가 같거나 ±1인 후보를 우선한다.
3. 저비용 descriptor로 top-K를 뽑는다.
4. 획 정렬과 raster distance로 재순위화한다.
5. 현재 두 모델의 confusion matrix에서 반복 오인된 쌍을 추가한다.
6. 수동 seed와 합쳐 versioned graph를 만든다.

출력 예시:

```json
{
  "char": "い",
  "neighbors": [
    {
      "char": "り",
      "rank": 1,
      "template_distance": 0.12,
      "same_stroke_count": true,
      "critical_strokes": [1],
      "critical_region_mask": "artifact reference"
    }
  ]
}
```

대형 중간 산출물은 Git에 커밋하지 않는다. 작은 versioned registry, 생성 설정, 해시, 요약 보고서만 커밋한다.

### 4.3 benchmark 샘플 종류

각 confusion pair마다 최소 다음을 생성한다.

1. **clean target**: 목표 템플릿의 자연스러운 왜곡
2. **full competitor substitution**: 경쟁 문자 전체를 목표 문자에 제출
3. **critical-stroke substitution**: 목표 글자의 결정적 획만 경쟁 문자 획으로 교체
4. **boundary interpolation**: 목표와 경쟁 획 사이를 단계적으로 보간
5. **near miss target**: 큰 왜곡이지만 여전히 목표 문자로 인정해야 하는 샘플
6. **wrong order/direction**: identity와 필순 오류를 분리하기 위한 샘플
7. **ambiguous**: 두 문자 경계에 가까워 보류가 필요한 샘플

interpolation 샘플을 무조건 target 또는 competitor로 라벨링하지 않는다. 규칙과 사람 검토 없이 경계 샘플을 강한 라벨로 사용하면 모델이 잘못된 경계를 배울 수 있다.

### 4.4 데이터 분할

#### 합성 데이터

- generator seed family를 train/validation/test로 완전히 분리
- pair registry 버전을 기록
- 특정 augmentation parameter 조합이 여러 split에 복제되지 않게 한다.

#### 실제 이미지 데이터

- 가능하면 writer 단위로 분할
- 같은 사람이 쓴 샘플이 train/test에 동시에 들어가지 않게 한다.
- ETL 계열 데이터는 비전 identity 보조 학습과 독립 평가에 사용하고, 획순 라벨로 사용하지 않는다.

#### 실제 스트로크 데이터

- 동의한 사용자 데이터가 생기면 user/session 단위로 분할
- 동일 사용자의 반복 시도가 train/test에 섞이지 않게 한다.
- 동의 이전 데이터나 기존 운영 제출을 임의로 학습에 사용하지 않는다.

### 4.5 핵심 지표

기존 MSE와 방향·순서 정확도를 유지하되 다음을 추가한다.

- target true acceptance rate
- competitor false acceptance rate
- pairwise ROC-AUC / PR-AUC
- target score와 competitor score의 margin
- top-1 / top-K candidate accuracy
- macro average over confusion pairs
- worst-10 pair 성능
- `い→り`, `り→い` 방향별 오류율
- ambiguity detection coverage와 selective risk
- Expected Calibration Error와 Brier score
- critical-stroke localization accuracy
- evidence-code precision/recall

평균값만 보고하지 않는다. 반드시 pair별 표와 worst-case를 제공한다.

---

## 5. Hard-negative 데이터 파이프라인

### 5.1 데이터셋 인터페이스 확장

기존 `RecognitionDataset`, `ScoringDataset`, `ChandraScoringDataset`을 무작정 복제하지 않는다. 공통 sample contract를 추가한다.

```python
{
    "user_strokes": ...,
    "target_template": ...,
    "written_char": "り",
    "target_char": "い",
    "competitor_char": "り",
    "is_target": False,
    "quality_for_written_char": 0.93,
    "target_match": 0.04,
    "pair_id": "hiragana_3044_308a",
    "critical_strokes": [1],
    "evidence_labels": ["STROKE_TOO_VERTICAL", "STROKE_TOO_LONG"],
    "ambiguity": False
}
```

`written_char`와 `target_char`가 같은 positive뿐 아니라 다른 hard negative를 같은 batch에 넣는다.

### 5.2 negative 유형

#### A. Full-character hard negative

경쟁 문자의 자연스러운 합성 글씨를 목표 템플릿과 짝지어 준다.

```text
user=distort(り), target_template=い, is_target=false
```

#### B. Critical-stroke transplant

목표 글자의 대부분을 유지하되 결정적 획만 경쟁 문자에 가깝게 바꾼다. 모델이 전체 실루엣이 아니라 실제 구별 획을 보게 한다.

#### C. Morph ladder

목표 획과 경쟁 획 사이를 여러 단계로 보간하되, 경계 영역은 `ambiguous` 또는 soft label로 처리한다.

#### D. Model-mined hard negative

현재 체크포인트가 높은 target score를 준 잘못된 문자 샘플을 다음 epoch의 hard-negative queue에 추가한다.

#### E. User-error replay

동의 기반 실제 데이터가 생긴 뒤, 반복 오인된 패턴을 익명화·집계해 합성 parameter와 pair sampling 비율에 반영한다.

### 5.3 sampling curriculum

초기에는 easy negative와 hard negative를 섞고, 학습 후반에 hard pair 비율을 높인다.

권장 초기 실험 범위:

```text
positive                 45%
random different char    10%
confusion full negative  20%
critical-stroke negative 15%
ambiguous/morph          10%
```

비율은 고정 진리가 아니다. ablation으로 결정한다. 단, positive만 사용한 baseline과 비교를 유지한다.

### 5.4 외부 실제 필기 이미지

AIST ETL Character Database는 일본어 손글씨 이미지의 보조 소스로 검토한다.

- 라이선스와 이용 신청 조건을 먼저 확인한다.
- 원본 데이터를 Git에 커밋하지 않는다.
- writer split을 유지한다.
- contemporary handwriting과의 domain gap을 보고한다.
- 좌표·필압·필순 정보가 없으므로 Chandra identity branch와 평가에만 사용한다.
- ETL로 얻은 개선을 실시간 stroke quality 개선으로 과장하지 않는다.

---

## 6. 공통 출력 계약: identity, quality, evidence를 분리

두 모델이 가능한 한 다음 공통 의미를 출력하도록 한다.

```python
{
    "task_score": Tensor[B],
    "written_form_quality": Tensor[B],
    "target_match_logit": Tensor[B],
    "char_embedding": Tensor[B, D],
    "candidate_logits": Tensor[B, K],
    "candidate_ids": list[list[str]],
    "confusion_margin": Tensor[B],
    "q": Tensor[B, S],
    "rev_logit": Tensor[B, S],
    "ord_logit": Tensor[B, S],
    "critical_stroke_logit": Tensor[B, S],
    "evidence_logits": Tensor[B, S, E],
    "uncertainty": Tensor[B]
}
```

기존 `overall`, `q`, `rev_logit`, `ord_logit`는 checkpoint/API 호환을 위해 alias 또는 기존 필드로 유지한다.

### candidate prototype bank

전체 문자 flat classifier만 사용하는 대신 템플릿 기반 prototype bank를 추가한다.

- 각 문자 템플릿에서 좌표 prototype과 비전 prototype을 계산
- 사용자 embedding과 prototype cosine similarity 계산
- confusion graph의 top-K 후보를 우선 평가
- 정답 및 hard negative에 angular 또는 cosine margin 적용
- 새 checkpoint에 prototype schema version과 charset hash 저장

prototype bank는 다음 장점이 있다.

- 목표와 경쟁 후보의 상대적 margin을 직접 계산
- 유사 문자 top-K 설명 가능
- 전체 6천여 클래스 full forward 없이 후보 재순위화 가능
- 문자 identity와 quality를 분리하기 쉬움

---

## 7. 좌표 기반 `Scorer` v2

### 7.1 입력 표현 강화

현재 5차원 점 특징을 다음 후보로 확장한다.

```text
x, y
normalized dx, dy
arc-length increment
normalized cumulative arc length
sin(tangent), cos(tangent)
curvature or turning angle
stroke_start, stroke_end
stroke index / relative order
optional speed, pressure validity mask
```

시간·필압은 실제 데이터 보정 전까지 강한 점수 근거로 사용하지 않는다. 값과 validity mask를 보존하되 auxiliary feature로 시작한다.

### 7.2 multi-resolution stroke encoding

획당 12점 하나로 고정하지 않는다.

권장 실험:

- coarse: 12점
- medium: 24점
- fine: 48점 또는 원시 포인트의 제한된 subset

공유 encoder 또는 가벼운 parallel projection으로 결합한다. 미세 곡률을 위해 해상도를 늘리되 실시간 추론 latency를 측정한다.

### 7.3 local stroke encoder + global relation encoder

권장 구조:

```text
point features
  -> per-stroke local encoder
  -> stroke embeddings
  -> relation features between strokes
  -> global character encoder
  -> target/competitor prototype comparison
```

명시적 relation feature:

- centroid delta
- start/end delta
- bbox overlap와 gap
- relative length
- relative principal angle
- vertical/horizontal ordering
- minimum inter-stroke distance

`い↔り`처럼 같은 2획 문자에서 획 간 배치와 2획 궤적을 구분하는 데 사용한다.

### 7.4 loss 설계

모든 손실을 한 번에 넣지 말고 ablation으로 검증한다.

기본 후보:

```text
L_total =
  λ_score * L_existing_score
+ λ_match * BCE(target_match)
+ λ_rank * max(0, margin - s(user,target) + s(user,competitor))
+ λ_metric * L_supcon_or_angular_margin
+ λ_critical * CE(critical_stroke)
+ λ_evidence * multi_label_BCE(evidence_codes)
+ λ_align * L_soft_dtw_optional
```

#### 필수 손실

- target-match binary loss
- target-vs-competitor margin ranking loss
- 기존 quality/direction/order loss

#### 실험 손실

- Supervised Contrastive Loss
- ArcFace 계열 additive angular margin
- Soft-DTW 또는 Soft-DTW divergence 기반 궤적 정렬 보조 손실
- focal weighting for rare/hard confusion examples

SupCon, ArcFace, Soft-DTW를 모두 넣은 모델을 첫 후보로 만들지 않는다. 각각의 단독 기여와 상호작용을 실험한다.

### 7.5 인식 사전학습 개선

현재 flat cross entropy baseline을 유지하고 다음 후보와 비교한다.

1. cross entropy + confusion-balanced sampler
2. cross entropy + supervised contrastive embedding
3. angular-margin prototype classifier
4. hierarchical classifier: script/coarse family → confusion group fine classifier

primary product split은 known-character writer/style holdout이다. 기존 character-holdout 평가는 generalization 보조 지표로 유지한다.

### 7.6 후보 인식과 채점의 결합

추론 시 전체 문자와 매번 비교하지 않는다.

1. cheap embedding으로 top-K 후보 검색
2. 목표 문자와 confusion graph 이웃을 반드시 후보에 포함
3. 목표와 후보 각각의 pair score 계산
4. margin이 충분하면 목표 인정
5. margin이 작으면 ambiguity
6. 경쟁 후보가 명확하면 structured confusion evidence 생성

경량 코치 hot path에서는 K를 제한하고 cache된 template feature를 사용한다.

---

## 8. `ChandraScorer` v2

### 8.1 explicit difference representation

현재 user/template을 별도로 인코딩하는 경로에 다음 interaction을 실험한다.

- `|user_token - target_token|`
- elementwise product
- bilinear/local pair interaction
- cross-attention residual
- raster signed-distance difference map

fine-grained visual recognition에서 국소 상호작용이 중요한 점을 반영하되, 가장 단순한 elementwise interaction을 baseline으로 시작한다.

### 8.2 discriminative-region multi-crop

confusion pair의 target/competitor 차이 mask에서 critical region을 만든다.

```text
full user image
full target image
critical user crop
critical target crop
optional target-vs-competitor delta crop
```

`い↔り`에서는 2획 상단 시작 영역과 하단 꼬리 영역이 우선 ROI가 된다.

구현 선택지:

- ROI token pooling
- 작은 crop을 별도 비전 forward 후 fusion
- full image token 위에 region mask weighting

첫 실험은 VRAM과 latency가 가장 낮은 region-weighted pooling으로 시작한다.

### 8.3 렌더링 개선

- anti-aliasing
- pen width variation
- mild blur/noise
- global translation/scale
- stroke-specific deformation
- background/contrast variation for external images

시간 인코딩 채널의 의미는 유지한다. 일반 RGB 사전학습 분포와의 차이를 줄이기 위한 multi-view rendering도 실험한다.

예:

```text
view A: conventional black-ink glyph
view B: current time-encoded view
view C: target difference / ROI view
```

### 8.4 backbone adaptation

비전 타워 전체 동결 baseline과 다음을 비교한다.

1. 마지막 N개 block 부분 unfreeze
2. adapter 또는 LoRA 계열 저랭크 적응
3. head-only warmup 후 일부 block unfreeze
4. confusion curriculum 단계에서만 low-LR adaptation

RunPod에서 VRAM, step time, overfit, pairwise 성능을 기록한다. 백본을 많이 푼 모델이 합성 train 성능만 높이고 실제 ETL/writer holdout이 나빠지는지 확인한다.

### 8.5 auxiliary heads

- target-match head
- candidate prototype/identity head
- critical-region head
- critical-stroke head
- evidence-code head

Chandra가 방향·순서를 완전히 독립적으로 알아낸다고 가정하지 않는다. 시간 인코딩과 stroke model의 높은 신뢰도 label을 이용한 auxiliary distillation은 후속 ablation으로 둔다.

### 8.6 hard-negative pair training

각 sample에서 다음을 제공한다.

```text
user image
correct written-character template
task target template
hard competitor template
critical region mask
```

목표가 `い`이고 user가 `り`인 경우:

- user vs `り`는 identity positive
- user vs `い`는 task negative
- quality는 `り` 기준으로 높을 수 있음
- task score는 `い` match가 낮으므로 낮아야 함

이 분리 없이는 `잘 쓴 다른 글자`를 설명하기 어렵다.

---

## 9. 두 모델을 함께 개선하는 방법

### 9.1 static average를 learned gate로 교체

기존 전역 가중치 baseline을 유지하고 다음 입력을 받는 작은 fusion gate를 추가한다.

```text
vision target-match probability
stroke target-match probability
vision/stroke candidate margins
vision/stroke uncertainty
model disagreement
stroke count and input quality
pair id or pair embedding
presence of timing/pressure metadata
```

출력:

- task score fusion weight
- identity fusion weight
- direction/order fusion weight
- abstain probability

학습 데이터는 hard-negative validation과 실제 writer holdout을 사용한다.

### 9.2 역할별 신뢰도

- 형태·국소 시각 차이: Chandra 우선 후보
- 획 방향·순서·시간 정보: stroke model 우선 후보
- 두 모델 일치: 높은 confidence 가능
- 두 모델 불일치: learned gate 또는 ambiguity

규칙을 하드코딩하지 않고 baseline rule과 learned gate를 비교한다.

### 9.3 제한적 cross-model distillation

두 모델을 서로 무조건 따라 하게 하면 같은 오류가 복제될 수 있다.

허용하는 distillation:

- stroke model이 방향·순서에 매우 높은 confidence일 때 Chandra auxiliary head에 soft label 제공
- Chandra가 실제 이미지 identity에 높은 confidence일 때 stroke identity embedding에 soft target 제공
- clean positive에서 candidate distribution consistency

금지:

- hard confusion에서 한 모델의 낮은 confidence 출력을 다른 모델에 강제
- overall score 전체를 무조건 KL로 일치
- test pair 또는 사람 검토 결과를 teacher label로 누수

Deep Mutual Learning은 독립 ablation으로만 평가하고, 독립 모델 대비 diversity가 줄어 worst-pair가 악화되면 사용하지 않는다.

### 9.4 uncertainty와 abstention

다음 중 하나면 강한 문자 혼동 피드백을 내지 않는다.

- target/competitor margin이 calibration threshold 미만
- 두 모델 candidate top-1이 다르고 둘 다 confidence가 낮음
- 입력 획이 너무 짧거나 불완전
- candidate pool 밖 문자가 높은 가능성을 가짐

응답:

```json
{
  "code": "AMBIGUOUS_BETWEEN_CHARACTERS",
  "candidates": ["い", "り"],
  "next_action": "RETRY_CRITICAL_STROKE",
  "critical_stroke": 1
}
```

억지 오답 설명보다 보류와 재시도가 낫다.

---

## 10. 모델 근거를 피드백 근거로 변환

### 10.1 pair delta profile

`scorer/confusion_explain.py` 또는 동등한 모듈을 추가한다.

입력:

- target template
- competitor template
- user strokes
- target/competitor model scores
- stroke alignment

출력:

- critical stroke
- critical segment
- target-vs-user feature residual
- competitor-vs-user feature residual
- evidence code
- anchor/vector/overlay

### 10.2 evidence 선택 규칙

모델 attention만으로 설명하지 않는다. attention은 보조 신호일 뿐이다.

우선순위:

1. 템플릿 쌍의 실제 기하 차이
2. user가 어느 쪽에 가까운지 계산한 feature residual
3. 두 모델의 candidate margin
4. 모델의 critical-region/stroke head
5. attention visualization은 디버그 참고

### 10.3 안정적인 evidence code

최소 후보:

```text
START_TOO_HIGH
START_TOO_LOW
START_TOO_LEFT
START_TOO_RIGHT
END_TOO_HIGH
END_TOO_LOW
STROKE_TOO_LONG
STROKE_TOO_SHORT
STROKE_TOO_VERTICAL
STROKE_TOO_HORIZONTAL
STROKE_ANGLE_MISMATCH
CURVE_TOO_EARLY
CURVE_TOO_LATE
TERMINAL_HOOK_WRONG_DIRECTION
INTER_STROKE_GAP_TOO_SMALL
INTER_STROKE_GAP_TOO_LARGE
CHARACTER_RESEMBLES_COMPETITOR
AMBIGUOUS_BETWEEN_CHARACTERS
```

모델과 UI는 문구가 아니라 code를 기준으로 동작한다.

---

## 11. 구조화 LLM 교사 계층

### 11.1 역할 경계

LLM이 할 일:

- 정해진 오류와 근거를 초보자에게 이해하기 쉽게 표현
- 반복 오류일 때 설명 방식을 바꿈
- `왜?` 요청에 짧은 이유 설명
- 문자 완료 후 개선점과 다음 micro-drill 요약

LLM이 하지 않을 일:

- 이미지나 좌표를 보고 독자적으로 문자 판정
- target/competitor 변경
- 점수·confidence 생성 또는 수정
- critical stroke 변경
- 입력에 없는 필순·위치·형태 사실 추가
- accepted/retry 결정을 변경

### 11.2 API 분리

```text
POST /coach/stroke       # geometry + stroke model, hot path
POST /score              # Chandra/Hybrid 심층 채점
POST /coach/verbalize    # 구조화 evidence를 교사 문장으로 변환
POST /coach/summary      # 문자/세션 완료 요약
```

`/coach/verbalize`와 `/coach/summary` 장애는 필기·채점을 실패시키지 않는다.

### 11.3 LLM 입력 스키마

```json
{
  "schema_version": "teacher_feedback.v1",
  "locale": "ko",
  "learner": {
    "level": "beginner",
    "attempt_number": 3,
    "same_error_count": 2,
    "preferred_length": "short"
  },
  "task": {
    "target_char": "い",
    "nearest_competitor": "り",
    "mode": "recall",
    "critical_stroke": 1,
    "total_strokes": 2
  },
  "locked_decision": {
    "decision_id": "uuid",
    "error_code": "CHARACTER_RESEMBLES_COMPETITOR",
    "evidence_codes": [
      "STROKE_TOO_VERTICAL",
      "STROKE_TOO_LONG",
      "START_TOO_HIGH"
    ],
    "severity": "major",
    "confidence": 0.94,
    "accepted": false,
    "next_action": "RETRY_CRITICAL_STROKE"
  },
  "evidence": {
    "target_margin": -0.56,
    "critical_region": "stroke_2",
    "target_feature_profile": {
      "primary_direction": "down_right",
      "relative_length": "shorter",
      "start_height": "middle"
    },
    "observed_feature_profile": {
      "primary_direction": "mostly_down",
      "relative_length": "long",
      "start_height": "high"
    }
  },
  "teaching_policy": {
    "allowed_strategies": [
      "direct_correction",
      "brief_contrast",
      "micro_drill"
    ],
    "max_sentences": 2,
    "max_characters": 100,
    "must_preserve_locked_fields": true,
    "forbidden": [
      "change_diagnosis",
      "invent_score",
      "invent_evidence",
      "give_multiple_actions"
    ]
  }
}
```

### 11.4 LLM 출력 스키마

```json
{
  "schema_version": "teacher_feedback.v1",
  "decision_id": "uuid",
  "error_code": "CHARACTER_RESEMBLES_COMPETITOR",
  "next_action": "RETRY_CRITICAL_STROKE",
  "strategy": "brief_contrast",
  "primary_text": "2획이 길고 세로로 내려가 り처럼 보입니다.",
  "secondary_text": "い는 오른쪽 중간에서 시작해 짧게 오른쪽 아래로 마무리하세요.",
  "spoken_text": "두 번째 획을 조금 짧고 오른쪽 아래로 써 보세요.",
  "emphasis_target": "critical_stroke"
}
```

### 11.5 이중 검증

JSON Schema 일치만으로는 충분하지 않다. semantic validator를 둔다.

검증:

- `decision_id` 동일
- `error_code` 동일
- `next_action` 동일
- `strategy`가 allowed list 안에 있음
- 문자 수와 문장 수 제한 준수
- evidence에 없는 방향·위치·점수 언급 금지
- 다른 획 번호 추가 금지
- target과 competitor 뒤바꿈 금지
- accepted=false인데 다음 획으로 넘어가라는 문구 금지

실패하면 응답을 폐기하고 code 기반 고정 템플릿을 사용한다.

### 11.6 호출 정책

LLM을 모든 획마다 호출하지 않는다.

호출 후보:

- 같은 confusion error가 2회 이상 반복
- 사용자가 `왜?` 또는 `더 자세히` 선택
- hint level이 상승
- 문자 완료 요약
- 세션 완료 요약

호출하지 않는 경우:

- pointermove
- 첫 번째 단순 오류
- confidence가 낮아 ambiguity인 상태에서 근거 없는 장문 설명
- stale attempt
- offline/local-only 모드

### 11.7 cache와 fallback

cache key 후보:

```text
schema_version
locale
level
error_code
sorted evidence_codes
hint_level
strategy
```

문자별 실제 차이 설명이 필요하면 target/competitor/pair version을 key에 포함한다.

모든 evidence code에 deterministic fallback 문구를 둔다. LLM timeout, API 장애, refusal, schema failure, semantic failure가 학습 흐름을 막지 않게 한다.

### 11.8 개인정보와 데이터 최소화

기본 LLM 요청에는 다음을 보내지 않는다.

- 원시 좌표 배열
- 필기 이미지
- 필압 전체 시계열
- 사용자 식별 정보
- 기존 제출 기록 원문

보내는 것은 문자, 구조화 evidence, hint level, 익명화된 반복 횟수다.

---

## 12. API와 checkpoint 호환성

### 12.1 `/score` 선택적 확장

기존 필드는 유지하고 새 필드를 optional로 추가한다.

```json
{
  "score": 61.3,
  "base_model_score": 82.4,
  "confusion": {
    "target_char": "い",
    "nearest_competitor": "り",
    "target_probability": 0.18,
    "competitor_probability": 0.74,
    "margin": -0.56,
    "critical_stroke": 1,
    "evidence_codes": ["STROKE_TOO_LONG", "STROKE_TOO_VERTICAL"],
    "decision": "RETRY_CRITICAL_STROKE"
  }
}
```

구버전 클라이언트는 새 필드를 무시해도 기존 동작을 유지해야 한다.

### 12.2 checkpoint version

새 checkpoint에는 최소 다음을 저장한다.

```text
schema_version
model_version
charset hash
confusion registry version/hash
feature schema
points-per-stroke settings
prototype bank hash
training config hash
dataset split hash
validation metrics
```

구버전 checkpoint 로더는 명확한 legacy 모드로 동작하고, 필드가 없다고 조용히 잘못 해석하지 않는다.

---

## 13. 평가 설계와 합격 기준

### 13.1 baseline 고정

변경 전에 현재 두 checkpoint로 다음 보고서를 만든다.

```text
docs/validation/CONFUSION_BASELINE_<YYYYMMDD>.md
artifacts local-only: raw prediction JSON, plots, large tables
```

보고서에는 다음이 있어야 한다.

- 정확한 Git SHA
- checkpoint SHA-256
- confusion registry hash
- synthetic test seed hash
- `い→り`, `り→い` 방향별 결과
- pair별 false acceptance
- macro/worst metrics
- latency와 VRAM

### 13.2 provisional quality gate

아래 값은 baseline을 보기 전에 고정하는 초기 gate다. 변경이 필요하면 결과를 본 뒤 임의로 낮추지 말고 별도 rationale commit으로 수정한다.

#### mandatory `い↔り`

- competitor false acceptance 상대 감소: 최소 70%
- target true acceptance 감소: 2 percentage point 이내
- pairwise margin의 중앙값 개선
- critical stroke localization accuracy: 최소 90% on synthetic labeled fixtures
- 잘못된 강한 competitor 설명: 0건 on deterministic fixtures

#### confusion registry macro

- macro competitor false acceptance 상대 감소: 최소 50%
- worst-10 pair 중 8개 이상 개선
- macro target true acceptance 감소: 2 percentage point 이내
- ECE 0.05 이하 목표 또는 baseline 대비 명확한 개선
- ambiguity 사용 시 selective risk가 coverage 감소에 비례해 개선

절대 수치가 불가능하면 실패를 숨기지 않고 data/model bottleneck을 분리한다.

### 13.3 일반 채점 회귀 gate

- 기존 quality MSE가 통계적으로 의미 있게 악화되지 않음
- 방향·순서 정확도 회귀 없음
- 기존 `/score` 계약 테스트 통과
- `永`, `水`, `木`, `日`, `語` 기존 E2E 유지
- latency와 checkpoint 크기 증가 보고

### 13.4 LLM gate

최소 1,000개의 synthetic structured cases에서 측정한다.

- JSON schema adherence: 100% 또는 실패 시 정상 fallback
- locked decision 보존: 100%
- invented score: 0
- invented evidence: 0
- target/competitor 뒤바꿈: 0
- 한 번에 하나의 행동 준수: 100%
- timeout/5xx에서 fallback 성공: 100%
- 사람이 평가한 명확성·실행 가능성 비교
- p50/p95 latency와 호출당 비용 기록

LLM 문장이 더 자연스럽다는 이유만으로 merge하지 않는다. deterministic template 대비 학습자 이해도 또는 반복 오류 개선 신호가 있어야 한다.

---

## 14. 실험 및 ablation matrix

### 14.1 좌표 모델

```text
S0 current baseline
S1 + hard-negative sampling
S2 + target-match + ranking loss
S3 + multi-resolution points/features
S4 + relation encoder
S5 + supervised contrastive OR angular-margin
S6 + optional Soft-DTW auxiliary
S7 best combination from independent ablations
```

### 14.2 Chandra 모델

```text
V0 current baseline
V1 + hard-negative pair training
V2 + explicit token difference interaction
V3 + critical-region weighted pooling
V4 + target-match/prototype heads
V5 + partial unfreeze or adapter
V6 + ETL auxiliary identity training/eval
V7 best combination from independent ablations
```

### 14.3 fusion

```text
F0 current static weights
F1 task-specific static weights on confusion benchmark
F2 rule-based confidence gate
F3 learned gate
F4 learned gate + calibrated abstention
F5 optional confidence-gated cross-model distillation
```

각 단계에서 full metrics를 저장한다. best combination은 단일 점수 하나가 아니라 mandatory pair, macro, worst-pair, regression, latency를 함께 본다.

---

## 15. 실제 RunPod 학습·검증 계약

모델 성능 실험은 실제 RunPod GPU에서 수행한다. 로컬 CPU나 mock 결과는 최종 근거가 아니다.

### 15.1 정확한 소스 고정

- GitHub에 push된 정확한 SHA만 사용
- Pod 작업 트리 clean 확인
- `/health.build_sha` 또는 training report의 SHA 일치
- Pod 내부 임시 수정 금지

### 15.2 환경 기록

- Pod ID/name
- GPU와 VRAM
- driver/CUDA/PyTorch
- container image
- training 시작·종료 UTC
- dataset/config/registry hash
- base checkpoint SHA-256
- output checkpoint SHA-256
- seed
- peak VRAM과 step time

비밀키와 전체 env 값은 기록하지 않는다.

### 15.3 screening과 final run 분리

#### screening

- 작은 문자 subset 또는 제한 step
- seed 1개
- 명백히 실패하는 구조 제거
- 기능·VRAM·loss sanity 검증

#### final candidate

- 전체 대상 문자/registry
- 최소 3개 seed 권장
- writer/style holdout
- mandatory pair와 macro/worst report
- direct inference latency
- hybrid calibration
- 실제 `/score` E2E

### 15.4 보고서

```text
docs/validation/CONFUSION_MODEL_RUNPOD_<YYYYMMDD>.md
docs/validation/LLM_TEACHER_EVAL_<YYYYMMDD>.md
```

raw checkpoints, caches, ETL 원본, 전체 prediction dump는 Git에 넣지 않는다. 해시와 요약만 기록한다.

### 15.5 종료

실험 성공·실패와 관계없이 마지막에 Pod를 Stop하고 상태를 확인한다. 사용자 지시 없이 Terminate하지 않는다.

---

## 16. 구현 단계

현재 실시간 튜터 Phase 2와 RunPod gate를 먼저 완료한다. confusion benchmark 도구의 설계는 병행할 수 있지만, 생산 모델 변경을 Phase 2 코드와 한 PR에 섞지 않는다.

### C0 — 현재 모델 confusion baseline

- [ ] `い↔り` deterministic fixtures
- [ ] confusion registry schema와 seed v1
- [ ] template-neighbor mining baseline
- [ ] 기존 두 모델과 hybrid의 pairwise 평가
- [ ] 현재 false acceptance와 calibration 보고서

완료 조건: 문제를 수치로 재현하고 exact checkpoint/SHA로 고정한다.

### C1 — hard-negative data foundation

- [ ] 공통 sample contract
- [ ] full competitor substitution
- [ ] critical-stroke transplant
- [ ] ambiguity/morph sample
- [ ] confusion-balanced sampler
- [ ] split/seed leakage 테스트

완료 조건: 두 모델이 동일한 pair metadata를 사용하고, label sanity test가 통과한다.

### C2 — stroke model v2

- [ ] target-match/ranking head
- [ ] prototype candidate retrieval
- [ ] multi-resolution/feature ablation
- [ ] relation encoder ablation
- [ ] RunPod train/eval

완료 조건: `い↔り`와 macro gate를 충족하거나 실패 원인이 보고된다.

### C3 — Chandra model v2

- [ ] hard-negative pair training
- [ ] explicit difference interaction
- [ ] critical-region pooling/crop ablation
- [ ] backbone adaptation ablation
- [ ] ETL auxiliary evaluation if licensing/setup complete
- [ ] RunPod train/eval

완료 조건: independent Chandra gain과 실제 이미지 holdout 결과가 존재한다.

### C4 — calibrated hybrid

- [ ] confidence inputs
- [ ] static/rule/learned gate 비교
- [ ] ambiguity threshold calibration
- [ ] optional cross-model distillation ablation
- [ ] existing score regression gate

완료 조건: independent models보다 macro 또는 worst-pair가 개선되고 회귀가 통제된다.

### C5 — structured teacher evidence

- [ ] pair delta profile
- [ ] critical stroke/region
- [ ] stable evidence codes
- [ ] deterministic fallback text
- [ ] overlay anchors/vectors

완료 조건: LLM 없이도 정확하고 행동 가능한 피드백을 제공한다.

### C6 — LLM pedagogical renderer

- [ ] versioned input/output JSON Schema
- [ ] strict structured output
- [ ] semantic validator
- [ ] cache/fallback/timeout
- [ ] repeated-error and summary policies
- [ ] 1,000-case faithfulness eval

완료 조건: 모델 결정을 100% 보존하고 장애 시 deterministic feedback가 유지된다.

### C7 — product A/B and real user calibration

- [ ] opt-in data policy
- [ ] template vs LLM feedback A/B
- [ ] repeated error reduction
- [ ] over-intervention rate
- [ ] per-device calibration

실제 사용자 데이터 동의와 삭제 경로가 마련되기 전에는 시작하지 않는다.

---

## 17. 파일 단위 작업 지시

권장 추가·수정 위치:

```text
configs/confusions/schema.json
configs/confusions/kana_seed_v1.yaml
scorer/confusions.py
scorer/build_confusion_graph.py
scorer/confusion_dataset.py
scorer/confusion_losses.py
scorer/confusion_explain.py
scorer/evaluate_confusions.py
scorer/fusion_gate.py
scorer/teacher_schemas.py
scorer/teacher_renderer.py
scripts/run_confusion_ablation.py
scripts/validate_teacher_feedback.py
tests/test_confusion_graph.py
tests/test_confusion_dataset.py
tests/test_confusion_losses.py
tests/test_confusion_explain.py
tests/test_confusion_server_contract.py
tests/test_teacher_schema.py
tests/test_teacher_semantics.py
```

기존 파일 변경 후보:

```text
scorer/data.py
scorer/synth.py
scorer/model.py
scorer/train_recognizer.py
scorer/train_scorer.py
scorer/chandra_scorer.py
scorer/train_chandra.py
scorer/hybrid.py
scorer/calibrate_hybrid.py
scorer/server.py
web/edge-score.ts
web/index.html or modular successor
```

새 이름을 사용하더라도 책임 분리를 유지한다. 기존 로직을 복사한 평행 구현을 무분별하게 만들지 않는다.

---

## 18. Codex가 처음 수행할 작업

별도 지시가 없으면 다음 단일 작업 단위만 먼저 수행한다.

1. 현재 branch/HEAD/main merge-base와 checkpoint 정보를 기록한다.
2. 현재 Phase 2 상태를 확인하고, 미완료면 기존 RunPod 계획을 우선한다.
3. `C0 — confusion baseline` 전용 새 branch를 만든다.
4. `い↔り` fixture를 `kanji/03044.svg`, `kanji/0308a.svg`에서 생성한다.
5. 현재 stroke, Chandra, hybrid가 `い`와 `り`를 서로 제출했을 때 어떻게 점수화하는지 재현한다.
6. pairwise metric, false acceptance, candidate margin을 추가한다.
7. 작은 confusion registry schema와 자동 neighbor mining prototype을 추가한다.
8. 로컬 테스트 후 실제 RunPod에서 exact SHA와 checkpoint로 baseline을 측정한다.
9. `docs/validation/CONFUSION_BASELINE_<YYYYMMDD>.md`를 작성한다.
10. 이 baseline이 고정되기 전에는 model v2나 LLM 구현을 시작하지 않는다.

최종 보고에는 다음을 포함한다.

- 실행한 단계와 실행하지 않은 단계
- exact SHA와 checkpoint hash
- `い→り`, `り→い` 결과
- macro/worst pair metrics
- current synthetic generator의 한계
- RunPod 환경과 실제 latency
- Pod Stop 증거
- 다음 하나의 실험 제안

---

## 19. 명시적 비목표

- 모델 크기만 늘려 해결했다고 주장
- LLM vision에 원시 글씨를 넘겨 최종 판정을 위임
- 모든 획마다 LLM 호출
- hard negative 없이 loss만 복잡하게 추가
- test pair를 보고 수동 threshold를 계속 조정
- `い/り`만 하드코딩해 전체 시스템을 특수 처리
- confusion 개선을 위해 정상 글자 acceptance를 크게 희생
- attention map을 사실 근거로 단독 사용
- 여러 loss를 한 번에 넣고 ablation 없이 결론
- 실제 RunPod/실제 checkpoint 검증 없이 완료 처리
- ETL 또는 사용자 원본 데이터를 Git에 커밋
- 기존 PR #1을 통째로 병합

---

## 20. 연구 근거

아래 방법은 그대로 정답으로 채택하는 것이 아니라 ablation 후보의 근거다.

- Khosla et al., **Supervised Contrastive Learning**, NeurIPS 2020  
  https://proceedings.neurips.cc/paper/2020/hash/d89a66c7c80a29b1bdbab0f2a1a94af8-Abstract.html
- Deng et al., **ArcFace: Additive Angular Margin Loss for Deep Face Recognition**, CVPR 2019  
  https://openaccess.thecvf.com/content_CVPR_2019/html/Deng_ArcFace_Additive_Angular_Margin_Loss_for_Deep_Face_Recognition_CVPR_2019_paper.html
- Cuturi and Blondel, **Soft-DTW: a Differentiable Loss Function for Time-Series**, ICML 2017  
  https://proceedings.mlr.press/v70/cuturi17a.html
- Blondel et al., **Differentiable Divergences Between Time Series**, AISTATS 2021  
  https://proceedings.mlr.press/v130/blondel21a.html
- Lin et al., **Bilinear CNN Models for Fine-Grained Visual Recognition**, ICCV 2015  
  https://openaccess.thecvf.com/content_iccv_2015/html/Lin_Bilinear_CNN_Models_ICCV_2015_paper.html
- Yan et al., **HD-CNN: Hierarchical Deep Convolutional Neural Networks for Large Scale Visual Recognition**, ICCV 2015  
  https://openaccess.thecvf.com/content_iccv_2015/html/Yan_HD-CNN_Hierarchical_Deep_ICCV_2015_paper.html
- Zhang et al., **Deep Mutual Learning**, CVPR 2018  
  https://openaccess.thecvf.com/content_cvpr_2018/html/Zhang_Deep_Mutual_Learning_CVPR_2018_paper.html
- Guo et al., **On Calibration of Modern Neural Networks**, ICML 2017  
  https://proceedings.mlr.press/v70/guo17a.html
- AIST, **The ETL Character Database**  
  https://etlcdb.db.aist.go.jp/the-etl-character-database/
- OpenAI, **Structured Outputs in the API**  
  https://openai.com/index/introducing-structured-outputs-in-the-api/
