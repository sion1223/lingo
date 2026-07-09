# -*- coding: utf-8 -*-
"""KanjiVG SVG 파싱: 베지어 경로 -> 획별 좌표 시퀀스."""
import os
import re
import numpy as np

_PATH_RE = re.compile(r'<path[^>]*\bid="[^"]*-s(\d+)"[^>]*\bd="([^"]+)"', re.S)
_TOKEN_RE = re.compile(r'([MmCcSsLl])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)')


def _parse_path_d(d):
    """SVG path d 속성 -> 3차 베지어 세그먼트 리스트 [(p0,p1,p2,p3), ...]."""
    tokens = _TOKEN_RE.findall(d)
    seq = []  # (cmd, args) 순서 보존
    cur_cmd = None
    args = []
    for letter, num in tokens:
        if letter:
            if cur_cmd and args:
                seq.append((cur_cmd, args))
            cur_cmd, args = letter, []
        else:
            args.append(float(num))
    if cur_cmd and args:
        seq.append((cur_cmd, args))

    segs = []
    cur = np.zeros(2)
    prev_ctrl = None
    for cmd, a in seq:
        if cmd in 'Mm':
            pt = np.array(a[:2])
            cur = pt if cmd == 'M' else cur + pt
            rest = a[2:]  # 후속 좌표쌍은 lineto 취급 (KanjiVG에는 거의 없음)
            for i in range(0, len(rest) - 1, 2):
                nxt = np.array(rest[i:i + 2])
                nxt = nxt if cmd == 'M' else cur + nxt
                segs.append((cur, cur, nxt, nxt))
                cur = nxt
            prev_ctrl = None
        elif cmd in 'Ll':
            for i in range(0, len(a) - 1, 2):
                nxt = np.array(a[i:i + 2])
                nxt = nxt if cmd == 'L' else cur + nxt
                segs.append((cur, cur, nxt, nxt))
                cur = nxt
            prev_ctrl = None
        elif cmd in 'Cc':
            for i in range(0, len(a) - 5, 6):
                p = [np.array(a[i + j:i + j + 2]) for j in (0, 2, 4)]
                if cmd == 'c':
                    p = [cur + q for q in p]
                segs.append((cur, p[0], p[1], p[2]))
                prev_ctrl = p[1]
                cur = p[2]
        elif cmd in 'Ss':
            for i in range(0, len(a) - 3, 4):
                p = [np.array(a[i + j:i + j + 2]) for j in (0, 2)]
                if cmd == 's':
                    p = [cur + q for q in p]
                c1 = 2 * cur - prev_ctrl if prev_ctrl is not None else cur
                segs.append((cur, c1, p[0], p[1]))
                prev_ctrl = p[0]
                cur = p[1]
    return segs


def _sample_bezier(seg, n=14):
    p0, p1, p2, p3 = seg
    t = np.linspace(0.0, 1.0, n)[:, None]
    return ((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1
            + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3)


def resample_stroke(pts, n):
    """호 길이 기준 균등 재샘플링 -> (n, 2)."""
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) < 2:
        return np.repeat(pts[:1], n, axis=0)
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(d)])
    total = s[-1]
    if total < 1e-9:
        return np.repeat(pts[:1], n, axis=0)
    u = np.linspace(0.0, total, n)
    out = np.empty((n, 2))
    out[:, 0] = np.interp(u, s, pts[:, 0])
    out[:, 1] = np.interp(u, s, pts[:, 1])
    return out


def normalize_strokes(strokes, margin=0.05):
    """전체 글자를 [margin, 1-margin] 정사각 박스에 중앙 배치 (종횡비 유지)."""
    allp = np.concatenate(strokes)
    lo, hi = allp.min(0), allp.max(0)
    scale = (1 - 2 * margin) / max((hi - lo).max(), 1e-6)
    center = (lo + hi) / 2
    return [(s - center) * scale + 0.5 for s in strokes]


def parse_svg(path, points_per_stroke=12):
    """SVG 파일 -> [ (P,2) ndarray, ... ] 획 순서대로."""
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    matches = _PATH_RE.findall(text)
    matches.sort(key=lambda m: int(m[0]))
    strokes = []
    for _, d in matches:
        segs = _parse_path_d(d)
        if not segs:
            continue
        pts = np.concatenate([_sample_bezier(s) for s in segs])
        strokes.append(resample_stroke(pts, points_per_stroke))
    return strokes


def char_to_file(kanji_dir, ch):
    return os.path.join(kanji_dir, f'{ord(ch):05x}.svg')


def load_char(kanji_dir, ch, points_per_stroke=12):
    strokes = parse_svg(char_to_file(kanji_dir, ch), points_per_stroke)
    if not strokes:
        raise ValueError(f'no strokes parsed for {ch!r}')
    return normalize_strokes(strokes)
