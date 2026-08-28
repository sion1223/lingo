# -*- coding: utf-8 -*-
"""합성 학습 데이터: 정답 획에 제어된 왜곡을 가해 '학습자 글씨'와 점수 라벨 생성.

라벨은 왜곡 결과를 기하학적으로 재측정해서 만들기 때문에
여러 왜곡이 결합돼도 일관된 점수가 나온다.
"""
import numpy as np

# 획별 품질 q = exp(-(W_SHAPE*shape_err + W_POS*pos_err))
W_SHAPE = 7.0
W_POS = 2.0
REV_FACTOR = 0.6    # 필순(방향) 반전 시 곱해지는 감점 계수
ORDER_FACTOR = 0.75  # 획 순서 오류 시 감점 계수


def _rot(theta):
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s], [s, c]])


def _smooth_noise(n, rng, sigma):
    """저주파 노이즈 (n,2): 무작위 제어점 4개를 선형 보간."""
    k = 4
    ctrl = rng.normal(0, sigma, (k, 2))
    t = np.linspace(0, k - 1, n)
    out = np.empty((n, 2))
    for d in range(2):
        out[:, d] = np.interp(t, np.arange(k), ctrl[:, d])
    return out


def distort(template, rng, severity=None):
    """template: [(P,2)...] 정규화된 정답 획.
    반환: (user_strokes, perm, reversed_flags)
      user_strokes[i] 는 template[perm[i]] 를 왜곡한 것."""
    S = len(template)
    sev = float(rng.uniform(0.0, 1.0)) if severity is None else severity

    # 전역 어파인 (약간의 기울어짐/크기 변화 — 사람 글씨의 자연 변동)
    g_theta = rng.normal(0, 0.05 + 0.10 * sev)
    g_scale = 1 + rng.normal(0, 0.04 + 0.08 * sev)
    g_shift = rng.normal(0, 0.01 + 0.04 * sev, 2)
    G = _rot(g_theta) * g_scale

    user = []
    reversed_flags = np.zeros(S, dtype=bool)
    for i, st in enumerate(template):
        p = st.copy()
        c = p.mean(0)
        # 획별 어파인
        theta = rng.normal(0, 0.04 + 0.22 * sev)
        scale = 1 + rng.normal(0, 0.03 + 0.20 * sev)
        shift = rng.normal(0, 0.005 + 0.045 * sev, 2)
        p = (p - c) @ _rot(theta).T * scale + c + shift
        # 모양 왜곡 (저주파 지터)
        p = p + _smooth_noise(len(p), rng, 0.004 + 0.035 * sev)
        # 전역 어파인
        p = (p - 0.5) @ G.T + 0.5 + g_shift
        # 방향 반전 (필순 오류)
        if rng.random() < 0.10 + 0.12 * sev:
            p = p[::-1].copy()
            reversed_flags[i] = True
        user.append(np.clip(p, 0.0, 1.0))

    # 획 순서 오류: 인접 쌍 스왑
    perm = np.arange(S)
    n_swap = rng.binomial(1, min(0.08 + 0.30 * sev, 0.5)) if S >= 2 else 0
    for _ in range(n_swap):
        j = rng.integers(0, S - 1)
        perm[[j, j + 1]] = perm[[j + 1, j]]
    user = [user[k] for k in perm]
    reversed_flags = reversed_flags[perm]
    return user, perm, reversed_flags


def stroke_errors(u, t):
    """사용자 획 u vs 정답 획 t (둘 다 (P,2)). 위치/모양 오차와 방향추정 반환."""
    cu, ct = u.mean(0), t.mean(0)
    pos_err = float(np.linalg.norm(cu - ct))
    du, dt = u - cu, t - ct
    fwd = float(np.linalg.norm(du - dt, axis=1).mean())
    rev = float(np.linalg.norm(du[::-1] - dt, axis=1).mean())
    shape_err = min(fwd, rev)
    looks_reversed = rev < fwd - 1e-9
    return pos_err, shape_err, looks_reversed


def compute_labels(user, template, perm, reversed_flags):
    """획별 품질 q(0~1), 방향/순서 플래그, 전체 점수(0~1) 라벨."""
    S = len(user)
    q = np.zeros(S)
    order_wrong = (perm != np.arange(S)).astype(np.float32)
    for i in range(S):
        t = template[perm[i]]
        pos_err, shape_err, _ = stroke_errors(user[i], t)
        qi = np.exp(-(W_SHAPE * shape_err + W_POS * pos_err))
        if reversed_flags[i]:
            qi *= REV_FACTOR
        if order_wrong[i]:
            qi *= ORDER_FACTOR
        q[i] = qi
    overall = float(q.mean())
    return {
        'q': q.astype(np.float32),
        'rev': reversed_flags.astype(np.float32),
        'ord': order_wrong,
        'overall': np.float32(overall),
        'perm': perm,
    }


def make_scoring_sample(template, rng, severity=None):
    user, perm, rev = distort(template, rng, severity)
    labels = compute_labels(user, template, perm, rev)
    return user, labels
