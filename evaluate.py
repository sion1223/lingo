"""
획 정확도 평가 실행 스크립트

사용법:
  python evaluate.py --char 一
  python evaluate.py --char あ --strokes strokes.json
  python evaluate.py --char 日 --noise 5.0   # 노이즈 시뮬레이션
  python evaluate.py --char 一 --plot          # 시각화

strokes.json 형식:
  [
    [[x1, y1], [x2, y2], ...],   // 1번 획
    [[x1, y1], [x2, y2], ...],   // 2번 획
    ...
  ]
"""
import os
import sys
import json
import argparse
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(description="획 정확도 평가")
    parser.add_argument("--char",    required=True, help="평가할 문자 (예: 一, あ, 日)")
    parser.add_argument("--strokes", default=None,  help="사용자 획 JSON 파일 경로")
    parser.add_argument("--dataset", default="kanji_dataset.json",
                        help="참조 데이터 JSON (기본값: kanji_dataset.json)")
    parser.add_argument("--noise",   type=float, default=3.0,
                        help="시뮬레이션 노이즈 크기(px). --strokes 없을 때 사용 (기본값: 3.0)")
    parser.add_argument("--resample",type=int, default=50,
                        help="리샘플링 포인트 수 (기본값: 50)")
    parser.add_argument("--plot",    action="store_true",
                        help="matplotlib으로 결과 시각화")
    return parser.parse_args()


def simulate_user_strokes(ref_strokes: list, noise_std: float) -> list:
    """참조 획에 노이즈를 더해 사용자 입력을 시뮬레이션한다."""
    user = []
    for tmpl in ref_strokes:
        pts = np.array(tmpl["raw"], dtype=float)
        noise = np.random.normal(0, noise_std, pts.shape)
        # 잘못된 방향 시뮬레이션 (20% 확률로 특정 획 심하게 왜곡)
        if np.random.random() < 0.2:
            noise *= 5.0
        noisy = (pts + noise).tolist()
        user.append(noisy)
    return user


def plot_evaluation(
    char: str,
    user_strokes: list,
    ref_strokes: list,
    stroke_results: list,
):
    """DTW 평가 결과를 시각화한다."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
    except ImportError:
        print("  ⚠ matplotlib이 없습니다: pip install matplotlib")
        return

    from dtw_stroke_model.features import normalize_stroke, resample_stroke
    from dtw_stroke_model.dtw import dtw_distance

    n_strokes = len(stroke_results)
    cols = min(n_strokes, 4)
    rows = (n_strokes + cols - 1) // cols + 1

    fig = plt.figure(figsize=(cols * 4, rows * 3))
    fig.suptitle(f"문자 '{char}' 획 평가", fontsize=14, fontweight="bold")

    # 전체 문자 비교
    ax_main = fig.add_subplot(rows, 2, 1)
    ax_main.set_title("전체 비교", fontsize=10)
    ax_main.set_aspect("equal")
    ax_main.invert_yaxis()

    colors = plt.cm.Set1(np.linspace(0, 1, max(n_strokes, 1)))

    for i, (u_pts, r_tmpl) in enumerate(zip(user_strokes, ref_strokes)):
        c = colors[i % len(colors)]
        u = np.array(u_pts)
        r = np.array(r_tmpl["raw"])
        ax_main.plot(r[:, 0], r[:, 1], "-", color=c, alpha=0.4, linewidth=2, label=f"참조{i+1}")
        ax_main.plot(u[:, 0], u[:, 1], "--", color=c, linewidth=1.5, label=f"입력{i+1}")

    ax_main.legend(fontsize=6, ncol=2)

    # 획별 오차 바
    ax_bar = fig.add_subplot(rows, 2, 2)
    ax_bar.set_title("획별 점수", fontsize=10)
    scores = [r["score"] for r in stroke_results]
    stroke_labels = [f"{r['stroke_index']}획" for r in stroke_results]
    bar_colors = ["#2ecc71" if s >= 70 else "#e74c3c" for s in scores]
    bars = ax_bar.barh(stroke_labels, scores, color=bar_colors)
    ax_bar.set_xlim(0, 100)
    ax_bar.set_xlabel("점수")
    for bar, s in zip(bars, scores):
        ax_bar.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2,
                    f"{s:.1f}", va="center", fontsize=8)

    # 획별 DTW 정렬 시각화
    resample_n = 50
    for idx, result in enumerate(stroke_results):
        row = 1 + (idx // cols)
        col = idx % cols
        ax = fig.add_subplot(rows, cols, cols * row + col + 1)

        score = result["score"]
        grade = result["grade"]
        ax.set_title(f"{idx+1}획  {score:.0f}점 ({grade})", fontsize=9,
                     color="#2ecc71" if score >= 70 else "#e74c3c")
        ax.set_aspect("equal")
        ax.invert_yaxis()
        ax.axis("off")

        if idx < len(user_strokes) and idx < len(ref_strokes):
            u_raw = np.array(user_strokes[idx])
            r_raw = np.array(ref_strokes[idx]["raw"])

            # 정규화
            u_n = normalize_stroke(u_raw.tolist())
            r_n = normalize_stroke(r_raw.tolist())
            u_rs = resample_stroke(u_n, resample_n)
            r_rs = resample_stroke(r_n, resample_n)

            _, path = dtw_distance(u_rs, r_rs)

            # 참조(파랑), 사용자(빨강)
            ax.plot(r_rs[:, 0], r_rs[:, 1], "b-", linewidth=2, alpha=0.5, label="참조")
            ax.plot(u_rs[:, 0], u_rs[:, 1], "r-", linewidth=2, alpha=0.7, label="입력")

            # DTW 정렬선 (오차 큰 지점만)
            errors = np.array(result.get("per_point_errors", []))
            threshold = errors.mean() * 1.5 if len(errors) > 0 else 0
            for i, j in path[::3]:  # 3개마다 하나씩
                if i < len(u_rs) and j < len(r_rs):
                    err = errors[i] if i < len(errors) else 0
                    if err > threshold:
                        ax.plot([u_rs[i, 0], r_rs[j, 0]],
                                [u_rs[i, 1], r_rs[j, 1]],
                                "y-", alpha=0.3, linewidth=0.5)

    plt.tight_layout()
    out_path = f"eval_{char}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\n  📊 시각화 저장: {out_path}")
    plt.show()


def main():
    args = parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_path = os.path.join(script_dir, args.dataset)

    if not os.path.exists(dataset_path):
        print(f"❌ 데이터셋 파일을 찾을 수 없습니다: {dataset_path}")
        print("   python run_parser.py 를 먼저 실행하세요.")
        sys.exit(1)

    from dtw_stroke_model.evaluator import StrokeEvaluator

    evaluator = StrokeEvaluator(
        dataset_path,
        resample_n=args.resample,
    )

    # 사용자 획 준비
    if args.strokes:
        with open(args.strokes, "r", encoding="utf-8") as f:
            user_strokes = json.load(f)
        print(f"\n  입력 파일: {args.strokes}  ({len(user_strokes)}획)")
    else:
        # 참조 데이터로 시뮬레이션
        ref = evaluator.get_reference(args.char)
        if ref is None:
            print(f"❌ '{args.char}' 문자를 데이터셋에서 찾을 수 없습니다.")
            avail = evaluator.available_characters()[:20]
            print(f"   사용 가능한 문자 예시: {avail}")
            sys.exit(1)
        user_strokes = simulate_user_strokes(ref, args.noise)
        print(f"\n  ※ 노이즈 {args.noise}px 시뮬레이션 (실제 사용 시 --strokes 파일 제공)")

    # 평가 실행
    result = evaluator.evaluate(args.char, user_strokes, verbose=True)

    # JSON 결과 저장
    out_json = f"eval_{args.char}_result.json"
    save_result = {k: v for k, v in result.items() if k != "stroke_results"}
    save_result["stroke_results"] = [
        {k: v for k, v in r.items() if k != "per_point_errors"}
        for r in result["stroke_results"]
    ]
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(save_result, f, ensure_ascii=False, indent=2)
    print(f"  결과 저장: {out_json}")

    # 시각화
    if args.plot:
        ref = evaluator.get_reference(args.char)
        if ref:
            plot_evaluation(args.char, user_strokes, ref, result["stroke_results"])


if __name__ == "__main__":
    main()
