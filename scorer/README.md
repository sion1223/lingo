# 일본어 손글씨 채점 + 교정 피드백 모델

`kanji/` 폴더의 KanjiVG 획 데이터(11,661자)를 이용해, 사용자가 애플펜슬/손가락으로
입력한 글씨(스트로크 좌표 시퀀스)를 채점하고 **어디를 어떻게 고쳐야 점수가 오르는지**
제시하는 딥러닝 파이프라인.

## 구조

```
scorer/
  kanjivg.py          SVG 베지어 -> 획별 좌표 시퀀스 파싱/정규화
  synth.py            정답 획에 제어된 왜곡 -> 학습자 글씨 + 점수 라벨 합성
  data.py             torch Dataset / 배치 변환 (점 특징: x,y,dx,dy,획시작)
  model.py            PointEncoder(Transformer) / Recognizer / Scorer
  train_recognizer.py 1단계: 문자 인식 모델 사전학습
  train_scorer.py     2단계: 인코더 전이 + 채점 헤드 파인튜닝
  feedback.py         채점 + 교정 제안 (모델·반사실·그래디언트 결합)
  score_api.py        KanjiScorer 고수준 API
  evaluate_demo.py    E2E 데모 (합성 학습자 글씨 채점 + PNG 시각화)
  server.py           로컬 웹 데모 서버 (표준 라이브러리)
  static/index.html   캔버스 입력 UI (펜/터치/마우스, Pointer Events)
```

## 학습 방법 (2단계 전이학습)

1. **사전학습(인식)** — 왜곡된 글씨가 어떤 글자인지 분류하는 Transformer 인코더 학습.
   손글씨의 획 기하 구조를 이해하는 백본이 된다.

   ```
   python -m scorer.train_recognizer --kanji-dir kanji --n-chars 300 --epochs 4
   ```

2. **전이학습(채점)** — 인코더 가중치를 가져와(낮은 학습률로 미세조정) 사용자↔템플릿
   교차어텐션 채점 헤드를 학습. 출력: 전체 점수, 획별 품질 q, 방향(필순) 반전 확률,
   획 순서 오류 확률.

   ```
   python -m scorer.train_scorer --kanji-dir kanji --n-chars 300 --epochs 4
   ```

   외부 사전학습 인식 모델이 있으면 `--pretrained <ckpt>` 로 대체 가능
   (`Scorer.load_pretrained_encoder` 형식: `encoder.*` state_dict).

   학습 라벨은 합성 왜곡(지터·어파인·필순 반전·획 순서 스왑)을 기하학적으로
   재측정해 자동 생성한다 — 사람 채점 데이터 불필요.

## 교정 피드백 ("점수 극대화" 제시)

`feedback.analyze()` 는 세 신호를 결합한다:

| 신호 | 내용 |
|------|------|
| 모델 예측 | 획별 품질 q, 필순 방향 반전/획 순서 오류 확률 |
| **반사실 분석** | 각 획을 정답 획으로 교체해 재채점 → "이 획을 고치면 **+X점**" |
| 그래디언트 | ∂(점수)/∂(좌표) → 각 점을 어느 방향으로 옮겨야 점수가 오르는지 벡터 |

획 누락/불필요 획은 탐욕 매칭으로 규칙 기반 검출해 점수에 반영한다.
교정 제안은 기대 점수 상승 순으로 정렬되므로, 사용자는 가장 효과 큰 획부터 고치면 된다.

## 사용

```python
from scorer.score_api import KanjiScorer
ks = KanjiScorer('checkpoints/scorer.pt', 'kanji')
report = ks.score('永', user_strokes)  # [(N,2) 좌표배열...] 픽셀 단위 OK
print(report['score'], report['corrections'])
```

웹 데모 (아이패드에서 같은 네트워크로 접속 가능):

```
python -m scorer.server --port 8765
```

## 확장 포인트

- `--n-chars` 를 늘리면 전체 11k자까지 학습 가능 (GPU 권장)
- 실제 사용자 필기 로그가 쌓이면 합성 라벨 대신/병행해 파인튜닝
- 필압·속도 특징을 `featurize` 의 점 특징에 추가 가능 (현재 5차원)
