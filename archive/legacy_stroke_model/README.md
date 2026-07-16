# legacy_stroke_model — 경량 스트로크 Transformer (보관용)

Chandra 전이학습 방향으로 확정하면서 사용하지 않게 된 경량 스트로크 모델 관련
파일들을 보관하는 폴더. 현재 파이프라인에서는 사용되지 않는다.

- `train_recognizer.py` — 1단계: 문자 인식 사전학습
- `train_scorer.py` — 2단계: 채점 전이학습 (`scorer.pt` 생성)
- `score_api.py` — 경량 모델(`scorer.pt`) 추론 API (`KanjiScorer`)
- `server.py` — 경량 모델 웹 데모 서버
- `evaluate_demo.py` — 경량 모델 평가/시각화 데모
- `static/` — 웹 데모 프론트엔드

참고: 이 파일들은 원래 `scorer/` 패키지 안에 있어서 `from .model import ...`
같은 상대 임포트를 쓴다. 다시 쓰려면 `scorer/`로 되돌려 놓아야 동작한다.
(공용 모듈인 `scorer/model.py`, `scorer/data.py`, `scorer/feedback.py` 등은
Chandra 학습에서도 쓰이므로 `scorer/`에 그대로 남아 있다.)
