# -*- coding: utf-8 -*-
"""학습된 채점 모델을 HuggingFace Hub(비공개)에 업로드.

사전 준비(1회):  hf auth login   (또는 huggingface-cli login)
실행:            python upload_hf.py
"""
from huggingface_hub import HfApi, whoami

REPO = f"{whoami()['name']}/lingo-kanji-scorer"

CARD = """---
license: cc-by-sa-3.0
language: [ja]
tags: [handwriting, kanji, scoring, transfer-learning, qwen3-vl, chandra]
base_model: datalab-to/chandra
---

# lingo-kanji-scorer

일본어 손글씨(온라인 스트로크) 채점 + 교정 피드백 모델.
[Chandra OCR](https://huggingface.co/datalab-to/chandra) (Qwen3-VL 기반)의
비전 인코더를 전이학습했다.

- 입력: 시간 인코딩 3채널 래스터 이미지 (잉크 / 획내 진행도 / 획 순서)
- 출력: 전체 점수(0~1), 획별 품질 q, 필순 방향 반전 확률, 획 순서 오류 확률
- 학습: KanjiVG 6,562자 × 6에폭, 합성 왜곡 + 기하학적 자동 라벨,
  2×RTX 5090 DDP, 백본 마지막 4블록 미세조정 (학습 파라미터 63.2M)
- 성능: 학습 overall MSE 0.0044, fresh-load 전체분포 평가 MSE 0.0033

체크포인트는 채점 헤드 + 미세조정된 비전 블록만 포함(131MB).
추론 시 Chandra 백본을 HF에서 자동 로드해 병합한다 (GPU 필요).

```python
from scorer.chandra_scorer import load_chandra_scorer, analyze_chandra
from scorer.kanjivg import load_char

model = load_chandra_scorer('chandra_scorer.pt')
report = analyze_chandra(model, load_char('kanji', '永'), user_strokes)
print(report['score'], report['corrections'])  # "N번 획 고치면 +X점"
```

데이터: [KanjiVG](http://kanjivg.tagaini.net) (CC-BY-SA 3.0, Ulrich Apel).
전체 파이프라인 코드: `scorer/` 폴더 동봉.
"""

api = HfApi()
api.create_repo(REPO, repo_type='model', private=True, exist_ok=True)
api.upload_file(path_or_fileobj=CARD.encode('utf-8'),
                path_in_repo='README.md', repo_id=REPO)
api.upload_file(path_or_fileobj='checkpoints/chandra_scorer.pt',
                path_in_repo='chandra_scorer.pt', repo_id=REPO)
api.upload_file(path_or_fileobj='checkpoints/chandra_train_full.log',
                path_in_repo='train.log', repo_id=REPO)
api.upload_folder(folder_path='scorer', path_in_repo='scorer', repo_id=REPO,
                  allow_patterns=['*.py'])
print(f'uploaded -> https://huggingface.co/{REPO} (private)')
