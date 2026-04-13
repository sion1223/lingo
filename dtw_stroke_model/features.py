"""
획 피처 추출 및 정규화
- 좌표 정규화 (스케일 불변)
- 균일 리샘플링
- 방향/곡률 특징 추출
"""
import numpy as np
from typing import List, Tuple


# KanjiVG 기준 캔버스 크기
CANVAS_SIZE = 109.0

# 리샘플링 기본 포인트 수
DEFAULT_RESAMPLE_N = 50


def normalize_stroke(points: List[List[float]]) -> np.ndarray:
    """
    단일 획 좌표를 정규화한다.
    1) 캔버스 크기 기준 [0, 1] 스케일링
    2) 시작점 기준 이동 (평행이동 불변)
    3) 바운딩박스 스케일 정규화 (크기 불변)

    Returns:
        shape (N, 2) numpy array
    """
    pts = np.array(points, dtype=float)
    if len(pts) == 0:
        return pts

    # 1) [0, 1] 스케일
    pts = pts / CANVAS_SIZE

    # 2) 시작점 기준 이동
    pts = pts - pts[0]

    # 3) 바운딩박스 정규화 (획의 절대 크기 무시, 형태만 비교)
    bbox = pts.max(axis=0) - pts.min(axis=0)
    scale = bbox.max()
    if scale > 1e-6:
        pts = pts / scale

    return pts


def resample_stroke(points: np.ndarray, n: int = DEFAULT_RESAMPLE_N) -> np.ndarray:
    """
    획을 n개 포인트로 균일하게 리샘플링한다.
    (DTW 비교를 위해 시퀀스 길이를 맞춤)

    Args:
        points: shape (T, 2)
        n: 목표 포인트 수

    Returns:
        shape (n, 2)
    """
    if len(points) < 2:
        return np.tile(points[0] if len(points) == 1 else np.zeros(2), (n, 1))

    # 누적 거리 계산
    diffs = np.diff(points, axis=0)
    seg_lengths = np.linalg.norm(diffs, axis=1)
    cum_lengths = np.concatenate([[0], np.cumsum(seg_lengths)])
    total_length = cum_lengths[-1]

    if total_length < 1e-8:
        return np.tile(points[0], (n, 1))

    # 균일 간격 샘플링
    target = np.linspace(0, total_length, n)
    resampled = np.zeros((n, points.shape[1]))
    for dim in range(points.shape[1]):
        resampled[:, dim] = np.interp(target, cum_lengths, points[:, dim])

    return resampled


def extract_features(points: np.ndarray) -> np.ndarray:
    """
    획에서 풍부한 피처를 추출한다:
    - (x, y): 정규화된 좌표
    - (dx, dy): 방향 벡터 (속도)
    - curvature: 곡률 (방향 변화율)

    Returns:
        shape (N, 5) — [x, y, dx, dy, curvature]
    """
    n = len(points)
    if n < 2:
        return np.zeros((n, 5))

    # 좌표
    xy = points  # (N, 2)

    # 방향 벡터 (중심차분)
    dx = np.gradient(points[:, 0])
    dy = np.gradient(points[:, 1])
    direction = np.stack([dx, dy], axis=1)  # (N, 2)

    # 크기로 정규화
    mag = np.linalg.norm(direction, axis=1, keepdims=True) + 1e-8
    direction_norm = direction / mag

    # 곡률 (방향 각도의 변화율)
    angles = np.arctan2(dy, dx)
    curvature = np.gradient(angles).reshape(-1, 1)

    return np.concatenate([xy, direction_norm, curvature], axis=1)


def preprocess_stroke(
    raw_points: List[List[float]],
    n_points: int = DEFAULT_RESAMPLE_N,
    use_features: bool = False
) -> np.ndarray:
    """
    원시 획 포인트를 DTW 비교용 시퀀스로 변환한다.

    Args:
        raw_points: [[x, y], ...] 원시 좌표 목록
        n_points: 리샘플링 포인트 수
        use_features: True면 (x,y,dx,dy,curvature) 5차원, False면 (x,y) 2차원

    Returns:
        shape (n_points, D)
    """
    pts = normalize_stroke(raw_points)
    pts = resample_stroke(pts, n_points)
    if use_features:
        pts = extract_features(pts)
    return pts


def compute_stroke_stats(points: np.ndarray) -> dict:
    """
    획의 통계적 특성을 계산한다 (피드백 생성용).

    Returns:
        dict with: length, direction_angle, bbox_ratio, smoothness
    """
    if len(points) < 2:
        return {"length": 0.0, "direction_angle": 0.0, "bbox_ratio": 1.0, "smoothness": 1.0}

    # 총 길이
    diffs = np.diff(points, axis=0)
    length = float(np.sum(np.linalg.norm(diffs, axis=1)))

    # 주 방향각 (시작→끝 벡터)
    overall_dir = points[-1] - points[0]
    direction_angle = float(np.degrees(np.arctan2(overall_dir[1], overall_dir[0])))

    # 가로:세로 비율
    bbox = points.max(axis=0) - points.min(axis=0)
    bbox_ratio = float(bbox[0] / (bbox[1] + 1e-8))

    # 부드러움 (방향 변화의 표준편차, 낮을수록 부드러움)
    dx = np.gradient(points[:, 0])
    dy = np.gradient(points[:, 1])
    angles = np.arctan2(dy, dx)
    angle_changes = np.abs(np.diff(angles))
    smoothness = float(1.0 / (1.0 + np.std(angle_changes)))

    return {
        "length": length,
        "direction_angle": direction_angle,
        "bbox_ratio": bbox_ratio,
        "smoothness": smoothness,
    }
