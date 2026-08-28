# -*- coding: utf-8 -*-
"""채점 + 교정 피드백 생성.

세 가지 신호를 결합한다:
 1. 모델 예측    — 전체 점수, 획별 품질/방향/순서 확률
 2. 반사실 분석  — 각 획을 정답 획으로 바꿔 다시 채점 -> "이 획을 고치면 +X점"
 3. 그래디언트   — d(점수)/d(좌표) -> 각 점을 어느 방향으로 옮겨야 점수가 오르는지
"""
import numpy as np
import torch

from .data import featurize, POINTS_PER_STROKE
from .final_report import build_final_report
from .kanjivg import resample_stroke, normalize_strokes
from .synth import stroke_errors

UNMATCHED_PENALTY = 0.24


def _to_batch(strokes):
    f, s = featurize(strokes)
    return (torch.from_numpy(f).unsqueeze(0),
            torch.from_numpy(s).unsqueeze(0),
            torch.zeros(1, len(f), dtype=torch.bool))


def prepare_user_strokes(raw_strokes, points_per_stroke=POINTS_PER_STROKE):
    """태블릿 원시 입력 [(N_i,2)...] -> 정규화 + 재샘플링."""
    strokes = [resample_stroke(np.asarray(s, dtype=np.float64), points_per_stroke)
               for s in raw_strokes if len(s) >= 1]
    return normalize_strokes(strokes)


def _linear_sum_assignment(cost):
    """작은 직사각 비용 행렬용 Hungarian 알고리즘 (row <= column)."""
    n, m = cost.shape
    if n > m:
        cols, rows = _linear_sum_assignment(cost.T)
        return rows, cols
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        minv = np.full(m + 1, np.inf)
        used = np.zeros(m + 1, dtype=bool)
        j0 = 0
        while True:
            used[j0] = True
            i0 = p[j0]
            cur = cost[i0 - 1] - u[i0] - v[1:]
            candidates = np.where(~used[1:], cur, np.inf)
            better = candidates < minv[1:]
            minv[1:][better] = candidates[better]
            way[1:][better] = j0
            j1 = int(np.argmin(np.where(used[1:], np.inf, minv[1:]))) + 1
            delta = minv[j1]
            u[p[used]] += delta
            v[used] -= delta
            minv[~used] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    cols = np.flatnonzero(p[1:])
    rows = p[1:][cols] - 1
    return rows, cols


def match_strokes(user, template):
    """전역 최적 매칭: 사용자 획 i -> 템플릿 획 match[i].

    더 나쁜 억지 매칭보다 획 누락/추가로 처리할 수 있도록 dummy 항목을 둔다.
    반환: match (len=사용자 획수, 값=템플릿 인덱스 or -1), missing(빠진 템플릿 획)."""
    nu, nt = len(user), len(template)
    cost = np.zeros((nu, nt))
    for i in range(nu):
        for j in range(nt):
            pe, se, _ = stroke_errors(user[i], template[j])
            # Shape is the primary matching signal. A parallel translation is
            # still the same stroke and should not become extra+missing.
            cost[i, j] = 0.35 * pe + 1.4 * se + 0.03 * abs(i - j)
    # 실획끼리의 비용이 2*penalty보다 크면 각각 extra/missing이 더 싸다.
    size = nu + nt
    augmented = np.full((size, size), 1e3)
    augmented[:nu, :nt] = cost
    for i in range(nu):
        augmented[i, nt + i] = UNMATCHED_PENALTY
    for j in range(nt):
        augmented[nu + j, j] = UNMATCHED_PENALTY
    augmented[nu:, nt:] = 0.0
    rows, cols = _linear_sum_assignment(augmented)
    match = np.full(nu, -1, dtype=int)
    used = set()
    for i, j in zip(rows, cols):
        if i < nu and j < nt:
            match[i] = j
            used.add(j)
    missing = [j for j in range(nt) if j not in used]
    return match, missing


@torch.no_grad()
def _model_score(model, user, template):
    return model(_to_batch(user), _to_batch(template))


def gradient_directions(model, user, template):
    """각 사용자 획 점마다 점수를 올리는 이동 방향 (S,P,2)."""
    f, s, m = _to_batch(user)
    f = f.clone().requires_grad_(True)
    out = model((f, s, m), _to_batch(template))
    out['overall'].sum().backward()
    g = f.grad[0, :, :2].detach().numpy()  # x,y 채널만
    S = len(user)
    P = len(user[0])
    return g.reshape(S, P, 2)


def analyze(model, template, raw_user_strokes, top_k=3, mode="trace"):
    """종합 분석. 반환 dict:
      score        0~100 전체 점수
      strokes      획별 진단 리스트 (q, 플래그, 오차, 반사실 gain, 메시지)
      missing      빠진 템플릿 획 인덱스
      extra        대응 없는 사용자 획 인덱스
      grad         (S,P,2) 점 이동 방향
      corrections  점수 상승 기대치 순으로 정렬된 교정 제안
    """
    model.eval()
    user = prepare_user_strokes(raw_user_strokes)
    match, missing = match_strokes(user, template)
    out = _model_score(model, user, template)
    grad = gradient_directions(model, user, template)
    report = build_final_report(
        output=out,
        user=user,
        template=template,
        match=match,
        missing=missing,
        score_once=lambda fixed, target: _model_score(model, fixed, target),
        top_k=top_k,
        mode=mode,
    )
    report['grad'] = grad
    return report
