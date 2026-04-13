"""
획 평가 피드백 생성기
DTW 분석 결과를 바탕으로 구체적인 개선 피드백을 생성한다.
"""
import numpy as np
from typing import List, Dict, Any
from .features import compute_stroke_stats


# 획별 점수 → 등급
GRADE_THRESHOLDS = [
    (95, "완벽"),
    (85, "우수"),
    (70, "양호"),
    (55, "보통"),
    (0,  "미흡"),
]


def score_to_grade(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "미흡"


def dtw_dist_to_score(dist: float, max_dist: float = 0.5) -> float:
    """
    DTW 거리를 0~100 점수로 변환한다.
    dist=0 → 100점, dist>=max_dist → 0점
    """
    score = max(0.0, 100.0 * (1.0 - dist / max_dist))
    return round(score, 1)


def analyze_stroke_diff(
    user_pts: np.ndarray,
    ref_pts: np.ndarray,
    path: np.ndarray,
    stroke_idx: int
) -> Dict[str, Any]:
    """
    사용자 획과 참조 획을 비교하여 세부 피드백을 생성한다.

    Args:
        user_pts: 정규화된 사용자 획 포인트 (N, 2)
        ref_pts:  정규화된 참조 획 포인트 (M, 2)
        path:     DTW 정렬 경로 [(i, j), ...]
        stroke_idx: 획 번호 (1-based)

    Returns:
        dict with issues, suggestions, per_point_errors
    """
    user_stats = compute_stroke_stats(user_pts)
    ref_stats  = compute_stroke_stats(ref_pts)

    issues = []
    suggestions = []

    # 1) 방향 오류 분석
    angle_diff = abs(user_stats["direction_angle"] - ref_stats["direction_angle"])
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    if angle_diff > 30:
        direction = _angle_to_direction(ref_stats["direction_angle"])
        issues.append(f"방향 오류 ({angle_diff:.0f}° 차이)")
        suggestions.append(f"{stroke_idx}번 획을 더 {direction} 방향으로 그으세요.")

    # 2) 길이/크기 오류 분석
    length_ratio = user_stats["length"] / (ref_stats["length"] + 1e-8)
    if length_ratio < 0.6:
        issues.append(f"획이 너무 짧음 (참조 대비 {length_ratio*100:.0f}%)")
        suggestions.append(f"{stroke_idx}번 획을 더 길게 그으세요.")
    elif length_ratio > 1.6:
        issues.append(f"획이 너무 김 (참조 대비 {length_ratio*100:.0f}%)")
        suggestions.append(f"{stroke_idx}번 획을 더 짧게 그으세요.")

    # 3) 곡률/부드러움 분석
    smoothness_diff = ref_stats["smoothness"] - user_stats["smoothness"]
    if smoothness_diff > 0.2:
        issues.append("획이 불규칙하거나 떨림 있음")
        suggestions.append(f"{stroke_idx}번 획을 더 부드럽고 일정하게 그으세요.")

    # 4) 가로세로 비율 (형태)
    ratio_diff = abs(user_stats["bbox_ratio"] - ref_stats["bbox_ratio"])
    if ratio_diff > 0.5:
        if user_stats["bbox_ratio"] < ref_stats["bbox_ratio"]:
            issues.append("획이 너무 세로로 치우침")
            suggestions.append(f"{stroke_idx}번 획을 가로로 더 넓게 그으세요.")
        else:
            issues.append("획이 너무 가로로 치우침")
            suggestions.append(f"{stroke_idx}번 획을 세로로 더 길게 그으세요.")

    # 5) 구간별 오차 (DTW 경로 기반)
    per_point_errs = _compute_per_point_errors(user_pts, ref_pts, path)
    high_error_regions = _find_high_error_regions(per_point_errs)
    for region in high_error_regions:
        pct_start = int(region["start"] * 100 / len(user_pts))
        pct_end   = int(region["end"]   * 100 / len(user_pts))
        issues.append(f"획의 {pct_start}%~{pct_end}% 구간에서 형태 이탈")
        suggestions.append(
            f"{stroke_idx}번 획의 {_region_name(pct_start, pct_end)} 부분을 수정하세요."
        )

    return {
        "stroke_index": stroke_idx,
        "issues": issues,
        "suggestions": suggestions,
        "per_point_errors": per_point_errs.tolist(),
        "user_stats": user_stats,
        "ref_stats": ref_stats,
    }


def _angle_to_direction(angle_deg: float) -> str:
    """각도를 직관적인 방향 명칭으로 변환한다."""
    angle_deg = angle_deg % 360
    if   angle_deg < 22.5  or angle_deg >= 337.5: return "오른쪽"
    elif angle_deg < 67.5:  return "오른쪽 아래"
    elif angle_deg < 112.5: return "아래"
    elif angle_deg < 157.5: return "왼쪽 아래"
    elif angle_deg < 202.5: return "왼쪽"
    elif angle_deg < 247.5: return "왼쪽 위"
    elif angle_deg < 292.5: return "위"
    else:                   return "오른쪽 위"


def _compute_per_point_errors(
    user_pts: np.ndarray,
    ref_pts:  np.ndarray,
    path:     np.ndarray
) -> np.ndarray:
    """DTW 경로를 따라 각 포인트의 오차를 계산한다."""
    errors = np.zeros(len(user_pts))
    counts = np.zeros(len(user_pts))
    for i, j in path:
        if 0 <= i < len(user_pts) and 0 <= j < len(ref_pts):
            errors[i] += np.linalg.norm(user_pts[i] - ref_pts[j])
            counts[i] += 1
    counts = np.where(counts == 0, 1, counts)
    return errors / counts


def _find_high_error_regions(
    errors: np.ndarray,
    threshold_ratio: float = 1.8,
    min_length: int = 5
) -> List[Dict]:
    """
    오차가 평균의 threshold_ratio배를 넘는 연속 구간을 찾는다.
    """
    if len(errors) == 0:
        return []
    threshold = errors.mean() * threshold_ratio
    regions = []
    in_region = False
    start = 0
    for i, e in enumerate(errors):
        if e > threshold and not in_region:
            in_region = True
            start = i
        elif e <= threshold and in_region:
            in_region = False
            if i - start >= min_length:
                regions.append({"start": start, "end": i, "max_error": float(errors[start:i].max())})
    if in_region and len(errors) - start >= min_length:
        regions.append({"start": start, "end": len(errors), "max_error": float(errors[start:].max())})
    return regions


def _region_name(pct_start: int, pct_end: int) -> str:
    """퍼센트 구간을 직관적인 명칭으로 변환한다."""
    mid = (pct_start + pct_end) / 2
    if mid < 25:  return "시작"
    if mid < 50:  return "초반"
    if mid < 75:  return "중간"
    if mid < 90:  return "후반"
    return "끝"


def generate_overall_feedback(
    stroke_results: List[Dict],
    overall_score: float
) -> Dict[str, Any]:
    """
    전체 평가 결과를 종합하여 최종 피드백을 생성한다.
    """
    grade = score_to_grade(overall_score)
    all_issues = []
    all_suggestions = []
    worst_strokes = []

    for r in stroke_results:
        score = r.get("score", 0)
        if score < 70:
            worst_strokes.append(r["stroke_index"])
        all_issues.extend(r.get("issues", []))
        all_suggestions.extend(r.get("suggestions", []))

    # 중복 제거
    unique_suggestions = list(dict.fromkeys(all_suggestions))

    summary = f"전체 점수: {overall_score:.1f}점 ({grade})"
    if overall_score >= 95:
        summary += " — 매우 정확합니다!"
    elif overall_score >= 70:
        summary += " — 잘 쓰고 있지만 조금 더 연습하면 좋겠습니다."
    else:
        summary += " — 기본 획 형태부터 다시 연습해보세요."

    return {
        "overall_score": overall_score,
        "grade": grade,
        "summary": summary,
        "worst_strokes": worst_strokes,
        "total_issues": len(all_issues),
        "top_suggestions": unique_suggestions[:5],
        "stroke_results": stroke_results,
    }
