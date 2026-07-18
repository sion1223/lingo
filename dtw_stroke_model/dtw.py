"""
DTW (Dynamic Time Warping) 핵심 구현
- 기본 DTW
- Sakoe-Chiba 밴드를 적용한 빠른 DTW
- 역추적(backtracking)을 통한 정렬 경로 반환
"""
import numpy as np
from typing import Tuple, Optional


def dtw_distance(
    seq1: np.ndarray,
    seq2: np.ndarray,
    window: Optional[int] = None
) -> Tuple[float, np.ndarray]:
    """
    두 시퀀스 사이의 DTW 거리와 정렬 경로를 계산한다.

    Args:
        seq1: shape (T1, D) — 첫 번째 시퀀스 (T1 타임스텝, D 차원)
        seq2: shape (T2, D) — 두 번째 시퀀스
        window: Sakoe-Chiba 밴드 너비 (None이면 전체 탐색)

    Returns:
        (distance, path)
        distance: 정규화된 DTW 거리 (낮을수록 유사)
        path: [(i, j), ...] 최적 정렬 경로
    """
    seq1 = np.asarray(seq1, dtype=float)
    seq2 = np.asarray(seq2, dtype=float)
    n, m = len(seq1), len(seq2)

    if window is None:
        window = max(n, m)
    window = max(window, abs(n - m))  # 경로가 반드시 존재하도록

    # 비용 행렬 초기화
    INF = float("inf")
    D = np.full((n + 1, m + 1), INF)
    D[0, 0] = 0.0

    for i in range(1, n + 1):
        j_lo = max(1, i - window)
        j_hi = min(m, i + window)
        for j in range(j_lo, j_hi + 1):
            cost = float(np.linalg.norm(seq1[i - 1] - seq2[j - 1]))
            D[i, j] = cost + min(D[i - 1, j - 1], D[i - 1, j], D[i, j - 1])

    # 정렬 경로 역추적
    path = []
    i, j = n, m
    while i > 0 or j > 0:
        path.append((i - 1, j - 1))
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            move = np.argmin([D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]])
            if move == 0:
                i -= 1
                j -= 1
            elif move == 1:
                i -= 1
            else:
                j -= 1
    path.reverse()

    # 경로 길이로 정규화 (긴 시퀀스 불이익 방지)
    normalized_dist = D[n, m] / (n + m)
    return normalized_dist, np.array(path)


def fast_dtw(
    seq1: np.ndarray,
    seq2: np.ndarray,
    window: int = 10
) -> float:
    """
    빠른 DTW 거리 계산 (경로 없이 거리만 반환).
    대량 비교 시 사용.
    """
    seq1 = np.asarray(seq1, dtype=float)
    seq2 = np.asarray(seq2, dtype=float)
    n, m = len(seq1), len(seq2)
    window = max(window, abs(n - m))

    INF = float("inf")
    # 메모리 절약: 두 행만 유지
    prev = np.full(m + 1, INF)
    curr = np.full(m + 1, INF)
    prev[0] = 0.0

    for i in range(1, n + 1):
        curr[:] = INF
        j_lo = max(1, i - window)
        j_hi = min(m, i + window)
        for j in range(j_lo, j_hi + 1):
            cost = float(np.linalg.norm(seq1[i - 1] - seq2[j - 1]))
            curr[j] = cost + min(prev[j - 1], prev[j], curr[j - 1])
        prev, curr = curr, prev

    return prev[m] / (n + m)


def per_point_error(
    seq1: np.ndarray,
    seq2: np.ndarray,
    path: np.ndarray
) -> np.ndarray:
    """
    DTW 경로를 따라 각 포인트의 유클리드 오차를 계산한다.
    seq1의 각 포인트에 대해 정렬된 seq2 포인트와의 거리를 반환.

    Returns:
        errors: shape (len(seq1),) — 각 타임스텝별 오차
    """
    errors = np.zeros(len(seq1))
    counts = np.zeros(len(seq1))
    for i, j in path:
        if 0 <= i < len(seq1) and 0 <= j < len(seq2):
            errors[i] += np.linalg.norm(seq1[i] - seq2[j])
            counts[i] += 1
    counts = np.where(counts == 0, 1, counts)
    return errors / counts
