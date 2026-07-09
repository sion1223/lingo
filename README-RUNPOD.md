# RunPod에서 Chandra 전이학습 실행하기

## 1. Pod 생성

- 템플릿: **RunPod PyTorch 2.x** (CUDA 12.x)
- GPU: A40 / RTX A6000 / L40S 권장 (VRAM 24GB 이상 — Chandra 비전 타워 bf16 로드용)
- 볼륨: 40GB 이상 (Chandra 가중치 ~16GB 캐시 포함)

## 2. 코드 + 데이터 업로드

Pod 터미널에서:

```bash
cd /workspace
# 방법 A: 이 폴더(lingo)를 zip으로 올려서 풀기 (RunPod 웹 업로드 또는 scp)
unzip lingo.zip && cd lingo

# 방법 B: git 저장소를 쓰고 있다면
# git clone <repo> && cd <repo>/lingo
```

`kanji/` 폴더(SVG 11,661개)가 lingo 안에 함께 있어야 한다.

## 3. 학습 실행

```bash
# Chandra OCR (datalab-to/chandra) 비전 인코더 전이학습 — 기본
bash runpod_train.sh

# 규모/설정 조절 (환경변수)
N_CHARS=0 EPOCHS=4 BATCH=24 bash runpod_train.sh        # 전체 문자
UNFREEZE_LAST=4 bash runpod_train.sh                     # 백본 마지막 4블록도 미세조정
CHANDRA_ID=datalab-to/chandra-ocr-2 bash runpod_train.sh # Chandra 2 사용

# 경량 스트로크 Transformer 파이프라인(인식 사전학습 -> 채점 전이)도 지원
MODE=stroke N_CHARS=0 EPOCHS=10 bash runpod_train.sh
```

체크포인트는 `checkpoints/` 에 저장된다 (`chandra_scorer.pt` 는 헤드만 저장 —
백본은 추론 시 HF에서 다시 로드).

## 4. 결과 회수 및 추론

`checkpoints/chandra_scorer.pt` 를 다운로드한 뒤 (GPU 서버에서):

```python
from scorer.chandra_scorer import load_chandra_scorer, analyze_chandra
from scorer.kanjivg import load_char

model = load_chandra_scorer('checkpoints/chandra_scorer.pt')
report = analyze_chandra(model, load_char('kanji', '永'), user_strokes)
print(report['score'], report['corrections'])  # 점수 + "이 획 고치면 +X점"
```

경량 스트로크 모델(`scorer.pt`)은 CPU 로컬 추론 가능:
`python -m scorer.server` 로 웹 데모 실행.

## 메모

- Chandra 는 정지 이미지 모델이라 필순·방향 정보가 없으므로, 입력을
  **시간 인코딩 3채널**(잉크/획내 진행도/획 순서)로 래스터화해 전달한다
  (`scorer/render.py`).
- 획별 진단은 획별 마스크로 비전 패치 특징을 풀링해 만든다.
- 학습 데이터는 KanjiVG 정답 획에 제어된 왜곡을 가한 합성 데이터이며
  라벨은 기하학적으로 자동 생성된다 (`scorer/synth.py`).
