# -*- coding: utf-8 -*-
"""스트로크 -> 시간 인코딩 래스터화.

Chandra(비전 모델) 전이학습용. 정지 이미지에는 필순/방향 정보가 없으므로
3채널에 시간 정보를 인코딩한다:
  ch0: 잉크 마스크 (획이 지나간 자리)
  ch1: 획 내 진행도 0->1 (필기 방향)
  ch2: 획 순서 (전체에서 몇 번째 획인지, 0->1)
추가로 획별 소프트 마스크 (S,H,W) 를 만들어 패치 특징의 획별 풀링에 쓴다.
"""
import numpy as np


def _draw_polyline(canvas, weight_canvas, pts, values, radius=2):
    """pts(N,2, 0~1 좌표)를 따라 원반을 찍으며 values(N,)를 기록."""
    H, W = canvas.shape
    # 촘촘하게 보간
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    n_dense = max(int(seg.sum() * max(H, W) * 1.5), len(pts))
    t = np.linspace(0, 1, n_dense)
    src_t = np.linspace(0, 1, len(pts))
    dense = np.stack([np.interp(t, src_t, pts[:, 0]),
                      np.interp(t, src_t, pts[:, 1])], axis=1)
    vals = np.interp(t, src_t, values)
    xs = np.clip((dense[:, 0] * (W - 1)).round().astype(int), 0, W - 1)
    ys = np.clip((dense[:, 1] * (H - 1)).round().astype(int), 0, H - 1)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            yy = np.clip(ys + dy, 0, H - 1)
            xx = np.clip(xs + dx, 0, W - 1)
            canvas[yy, xx] = np.maximum(canvas[yy, xx], vals)
            weight_canvas[yy, xx] = 1.0


def render_time_encoded(strokes, size=448, radius=None):
    """[(P,2)...] -> (3,H,W) float32 [0,1] + 획별 마스크 (S,H,W)."""
    H = W = size
    if radius is None:
        radius = max(size // 112, 2)
    S = len(strokes)
    ink = np.zeros((H, W), np.float32)
    prog = np.zeros((H, W), np.float32)
    order = np.zeros((H, W), np.float32)
    masks = np.zeros((S, H, W), np.float32)
    for i, st in enumerate(strokes):
        pts = np.asarray(st, np.float64)
        P = len(pts)
        progress_vals = np.linspace(0.15, 1.0, P)  # 시작점도 0이 아니게
        order_val = (i + 1) / S
        _draw_polyline(ink, masks[i], pts, np.ones(P), radius)
        _draw_polyline(prog, masks[i], pts, progress_vals, radius)
        _draw_polyline(order, masks[i], pts, np.full(P, order_val), radius)
    img = np.stack([ink, prog, order])
    return img, masks


def masks_to_grid(masks, grid_h, grid_w):
    """획별 마스크 (S,H,W) -> 패치 그리드 (S, grid_h*grid_w) 풀링 가중치."""
    S, H, W = masks.shape
    bh, bw = H // grid_h, W // grid_w
    m = masks[:, :grid_h * bh, :grid_w * bw]
    m = m.reshape(S, grid_h, bh, grid_w, bw).mean(axis=(2, 4))
    flat = m.reshape(S, -1)
    norm = flat.sum(axis=1, keepdims=True)
    return (flat / np.maximum(norm, 1e-6)).astype(np.float32)
