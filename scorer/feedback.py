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
            cost[i, j] = 1.2 * pe + se + 0.03 * abs(i - j)
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


def analyze(model, template, raw_user_strokes, top_k=3):
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
    extra = [i for i in range(len(user)) if match[i] < 0]

    out = _model_score(model, user, template)
    base = float(out['overall'][0])
    q = out['q'][0].numpy()
    rev_p = torch.sigmoid(out['rev_logit'][0]).numpy()
    ord_p = torch.sigmoid(out['ord_logit'][0]).numpy()

    # 획 누락 페널티 (모델은 존재하는 획만 보므로 규칙으로 반영)
    nt = len(template)
    coverage = (nt - len(missing)) / max(nt, 1)
    score = base * coverage

    grad = gradient_directions(model, user, template)

    strokes_report = []
    for i in range(len(user)):
        j = int(match[i])
        entry = dict(index=i, template_index=j, q=float(q[i]),
                     rev_prob=float(rev_p[i]), ord_prob=float(ord_p[i]))
        if j >= 0:
            pe, se, looks_rev = stroke_errors(user[i], template[j])
            entry.update(pos_err=pe, shape_err=se)
            # 반사실: 이 획을 정답 획으로 교체하면 점수가 얼마나 오르나
            fixed = list(user)
            fixed[i] = template[j].copy()
            cf = _model_score(model, fixed, template)
            entry['gain'] = (float(cf['overall'][0]) - base) * coverage * 100
            # 이동 방향 (정답 중심 - 사용자 중심)
            entry['move'] = (template[j].mean(0) - user[i].mean(0)).tolist()
            msgs = []
            if entry['rev_prob'] > 0.5 or looks_rev:
                msgs.append('필순 방향이 반대입니다 — 반대쪽 끝에서 시작하세요')
            if entry['ord_prob'] > 0.5:
                msgs.append(f'획 순서 오류 — 이 획은 {j + 1}번째로 써야 합니다')
            if pe > 0.06:
                dx, dy = entry['move']
                h = '오른쪽' if dx > 0.02 else ('왼쪽' if dx < -0.02 else '')
                v = '아래' if dy > 0.02 else ('위' if dy < -0.02 else '')
                msgs.append(f'위치를 {h}{"·" if h and v else ""}{v}로 옮기세요')
            if se > 0.05:
                msgs.append('모양을 정답 궤적에 가깝게 교정하세요')
            entry['messages'] = msgs or ['잘 썼습니다']
        else:
            entry.update(gain=0.0, messages=['불필요한(대응 없는) 획입니다 — 지우세요'])
        strokes_report.append(entry)

    corrections = sorted(
        [e for e in strokes_report if e.get('gain', 0) > 0.5 or e['template_index'] < 0],
        key=lambda e: -(e.get('gain') or 0))[:top_k]
    for j in missing:
        corrections.append(dict(index=-1, template_index=j, gain=None,
                                messages=[f'{j + 1}번째 획이 빠졌습니다 — 추가하세요']))

    return dict(score=round(score * 100, 1), base_model_score=round(base * 100, 1),
                strokes=strokes_report, missing=missing, extra=extra,
                match=match.tolist(), grad=grad, corrections=corrections,
                user=user)
