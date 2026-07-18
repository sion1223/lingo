"""
DTW 획 평가 모델 학습 스크립트

[모드 1] DTW 전용 (기본값, PyTorch 불필요)
  - kanji_dataset.json에서 참조 템플릿 DB를 구축한다.
  - 별도 학습 없이 즉시 평가 가능.
  - DTW 거리를 사용하여 획 정확도를 계산한다.

[모드 2] Siamese CNN + DTW (--nn 플래그)
  - 1D CNN Siamese 네트워크를 kanji 데이터로 학습한다.
  - 획의 의미론적 임베딩을 학습한다.
  - 학습 후 stroke_encoder.pt로 저장한다.

사용법:
  python train_model.py                        # DTW 전용 (평가 검증)
  python train_model.py --nn                   # CNN 학습
  python train_model.py --nn --epochs 50       # 에폭 지정
  python train_model.py --dataset my_data.json # 데이터 경로 지정
"""
import os
import sys
import json
import argparse
import time
import numpy as np
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="DTW 획 평가 모델 학습")
    parser.add_argument("--dataset",  default="kanji_dataset.json",
                        help="학습 데이터 JSON 경로 (기본값: kanji_dataset.json)")
    parser.add_argument("--nn",       action="store_true",
                        help="신경망(Siamese CNN) 학습 활성화")
    parser.add_argument("--epochs",   type=int, default=30,
                        help="학습 에폭 수 (기본값: 30)")
    parser.add_argument("--lr",       type=float, default=1e-3,
                        help="학습률 (기본값: 0.001)")
    parser.add_argument("--embed",    type=int, default=64,
                        help="임베딩 차원 (기본값: 64)")
    parser.add_argument("--resample", type=int, default=50,
                        help="획 리샘플링 포인트 수 (기본값: 50)")
    parser.add_argument("--features", action="store_true",
                        help="5차원 피처 사용 (좌표+방향+곡률)")
    parser.add_argument("--validate", action="store_true",
                        help="학습 후 검증 평가 실행")
    parser.add_argument("--save",     default="stroke_encoder.pt",
                        help="모델 저장 경로 (기본값: stroke_encoder.pt)")
    return parser.parse_args()


# ────────────────────────────────────────────────────────────────────────────
# 모드 1: DTW 전용 검증
# ────────────────────────────────────────────────────────────────────────────

def run_dtw_validation(dataset_path: str, resample_n: int, use_features: bool):
    """
    데이터셋 내에서 자기 대조(self-comparison) 및 타 문자 교차 비교로
    DTW 시스템의 판별력을 검증한다.
    """
    print("\n" + "="*60)
    print("  [DTW 전용 검증]")
    print("="*60)

    from dtw_stroke_model.dtw import fast_dtw
    from dtw_stroke_model.features import preprocess_stroke

    with open(dataset_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # 처음 200개 문자만 사용
    records = [r for r in records if r.get("strokes") and len(r["strokes"]) >= 1][:200]

    print(f"  검증 문자: {len(records)}개")

    # 각 문자의 첫 번째 획을 대표 획으로 사용
    samples = []
    for rec in records:
        char = rec.get("character", "")
        pts = rec["strokes"][0].get("points", [])
        if len(pts) < 2:
            continue
        seq = preprocess_stroke(pts, resample_n, use_features)
        samples.append({"char": char, "seq": seq})

    # 랜덤 샘플 100쌍으로 검증
    import random
    rng = random.Random(123)
    chars = list({s["char"] for s in samples})
    char_to_seqs = {}
    for s in samples:
        char_to_seqs.setdefault(s["char"], []).append(s["seq"])

    same_dists = []
    diff_dists = []

    n_pairs = min(500, len(samples) * 2)
    for _ in range(n_pairs):
        if rng.random() < 0.5 and any(len(v) > 1 for v in char_to_seqs.values()):
            # 같은 문자 (variant 있을 경우)
            eligible = [c for c, v in char_to_seqs.items() if len(v) > 1]
            if not eligible:
                continue
            c = rng.choice(eligible)
            a, b = rng.sample(char_to_seqs[c], 2)
            same_dists.append(fast_dtw(a, b))
        else:
            # 다른 문자
            c1, c2 = rng.sample(chars, 2)
            a = rng.choice(char_to_seqs[c1])
            b = rng.choice(char_to_seqs[c2])
            diff_dists.append(fast_dtw(a, b))

    # 결과 출력
    if same_dists:
        print(f"\n  같은 문자 획 거리:  평균 {np.mean(same_dists):.4f}  ±  {np.std(same_dists):.4f}")
    if diff_dists:
        print(f"  다른 문자 획 거리:  평균 {np.mean(diff_dists):.4f}  ±  {np.std(diff_dists):.4f}")
    if same_dists and diff_dists:
        separation = np.mean(diff_dists) - np.mean(same_dists)
        print(f"\n  판별력(분리도):     {separation:.4f}  (높을수록 좋음)")
        if separation > 0.01:
            print("  ✓ DTW가 문자 간 획 차이를 명확히 구분합니다.")
        else:
            print("  △ 판별력이 낮습니다. --resample 값을 늘려보세요.")

    print()


# ────────────────────────────────────────────────────────────────────────────
# 모드 2: Siamese CNN 학습
# ────────────────────────────────────────────────────────────────────────────

def run_nn_training(args, dataset_path: str):
    """Siamese CNN을 kanji 데이터로 학습한다."""
    try:
        import torch
        import torch.nn as nn
        import torch.optim as optim
    except ImportError:
        print("❌ PyTorch가 필요합니다: pip install torch")
        sys.exit(1)

    from dtw_stroke_model.nn_model import (
        SiameseStrokeNet, ContrastiveLoss, StrokeDataset, build_torch_dataset
    )

    in_channels = 5 if args.features else 2

    print("\n" + "="*60)
    print("  [Siamese CNN 학습]")
    print("="*60)
    print(f"  데이터:    {dataset_path}")
    print(f"  에폭:      {args.epochs}")
    print(f"  학습률:    {args.lr}")
    print(f"  임베딩:    {args.embed}차원")
    print(f"  리샘플:    {args.resample}포인트")
    print(f"  피처:      {'5D(x,y,dx,dy,curv)' if args.features else '2D(x,y)'}")
    print()

    # 데이터 준비
    with open(dataset_path, "r", encoding="utf-8") as f:
        records = json.load(f)
    print(f"  데이터 로드: {len(records)}개 문자")

    t0 = time.time()
    dataset = StrokeDataset(records, args.resample, args.features)
    print(f"  총 획 샘플: {len(dataset)}개  ({time.time()-t0:.1f}s)")

    pairs = dataset.get_pairs(n_pairs=20000)
    print(f"  학습 쌍:    {len(pairs)}개")

    loader = build_torch_dataset(pairs)

    # 모델 초기화
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  디바이스:  {device}\n")

    model = SiameseStrokeNet(in_channels=in_channels, embed_dim=args.embed).to(device)
    criterion = ContrastiveLoss(margin=1.0)
    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    best_loss = float("inf")
    history = []

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_loss = 0.0
        n_batches = 0

        for seq1, seq2, labels in loader:
            seq1   = seq1.to(device)
            seq2   = seq2.to(device)
            labels = labels.to(device)

            e1 = model.embed(seq1)
            e2 = model.embed(seq2)

            loss = criterion(e1, e2, labels)
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            epoch_loss += loss.item()
            n_batches  += 1

        scheduler.step()
        avg_loss = epoch_loss / max(n_batches, 1)
        history.append(avg_loss)

        if avg_loss < best_loss:
            best_loss = avg_loss
            torch.save(model.state_dict(), args.save)

        if epoch % 5 == 0 or epoch == 1 or epoch == args.epochs:
            bar = "█" * int(20 * (1 - avg_loss / max(history))) + "░" * int(20 * avg_loss / max(history))
            print(f"  Epoch {epoch:3d}/{args.epochs}  loss={avg_loss:.4f}  best={best_loss:.4f}")

    print(f"\n  ✓ 학습 완료. 최적 모델 저장: {args.save}")
    print(f"    최적 손실: {best_loss:.4f}\n")

    return model


# ────────────────────────────────────────────────────────────────────────────
# 학습 후 검증 평가
# ────────────────────────────────────────────────────────────────────────────

def run_evaluation_sample(dataset_path: str, resample_n: int, use_features: bool):
    """
    실제 평가기를 사용하여 샘플 평가를 실행한다.
    """
    print("\n" + "="*60)
    print("  [샘플 평가 데모]")
    print("="*60)

    from dtw_stroke_model.evaluator import StrokeEvaluator

    evaluator = StrokeEvaluator(
        dataset_path,
        resample_n=resample_n,
        use_features=use_features,
    )

    chars = evaluator.available_characters()
    test_chars = []
    # 히라가나 우선
    for c in chars:
        cp = ord(c)
        if 0x3040 <= cp <= 0x309F:
            test_chars.append(c)
            if len(test_chars) >= 3:
                break
    # 부족하면 한자 추가
    if len(test_chars) < 3:
        for c in chars:
            cp = ord(c)
            if 0x4E00 <= cp <= 0x9FFF and c not in test_chars:
                test_chars.append(c)
                if len(test_chars) >= 3:
                    break

    print(f"\n  테스트 문자: {test_chars}\n")

    for char in test_chars:
        ref = evaluator.get_reference(char)
        if not ref:
            continue

        # 시뮬레이션: 참조 획에 노이즈를 더해 사용자 입력으로 가정
        user_strokes = []
        for tmpl in ref:
            pts = np.array(tmpl["raw"], dtype=float)
            noise = np.random.normal(0, 3.0, pts.shape)  # ±3px 노이즈
            noisy_pts = (pts + noise).tolist()
            user_strokes.append(noisy_pts)

        evaluator.evaluate(char, user_strokes, verbose=True)


# ────────────────────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_path = os.path.join(script_dir, args.dataset)

    if not os.path.exists(dataset_path):
        print(f"❌ 데이터셋 파일을 찾을 수 없습니다: {dataset_path}")
        print("   먼저 run_parser.py를 실행하여 kanji_dataset.json을 생성하세요.")
        sys.exit(1)

    print(f"\n🚀 DTW 획 평가 모델")
    print(f"   데이터셋: {dataset_path}")

    # 신경망 학습
    if args.nn:
        run_nn_training(args, dataset_path)

    # DTW 전용 검증
    run_dtw_validation(dataset_path, args.resample, args.features)

    # 샘플 평가
    if args.validate or not args.nn:
        run_evaluation_sample(dataset_path, args.resample, args.features)

    print("✅ 완료!\n")
    print("  다음 단계:")
    print("  1) evaluate.py 를 실행하여 특정 문자 평가")
    print("  2) dtw_stroke_model.StrokeEvaluator 를 앱에 통합")
    if not args.nn:
        print("  3) python train_model.py --nn --epochs 50  (신경망 학습)")


if __name__ == "__main__":
    main()
