# -*- coding: utf-8 -*-
"""E2E 데모: 글자 하나를 왜곡해 '학습자 글씨'를 만들고 채점+교정 피드백 시각화.

python -m scorer.evaluate_demo --char 永 --severity 0.6
"""
import argparse

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import torch

from .feedback import analyze
from .kanjivg import load_char
from .model import Scorer
from .synth import distort


def draw(ax, strokes, q=None, lw=2.5, alpha=1.0, color=None):
    for i, s in enumerate(strokes):
        c = color
        if c is None:
            if q is not None:
                c = plt.cm.RdYlGn(float(q[i]))
            else:
                c = plt.cm.tab20(i % 20)
        ax.plot(s[:, 0], s[:, 1], color=c, lw=lw, alpha=alpha,
                solid_capstyle='round')
        ax.annotate(str(i + 1), s[0], fontsize=8, color=c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--char', default='永')
    ap.add_argument('--severity', type=float, default=0.6)
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--checkpoint', default='checkpoints/scorer.pt')
    ap.add_argument('--out', default='demo_feedback.png')
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()

    template = load_char(args.kanji_dir, args.char)
    rng = np.random.default_rng(args.seed)
    fake_user, _, _ = distort(template, rng, severity=args.severity)

    ckpt = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
    sd = ckpt['model']
    model = Scorer(max_len=sd['encoder.pos_emb.weight'].shape[0],
                   max_strokes=sd['encoder.stroke_emb.weight'].shape[0])
    model.load_state_dict(sd)

    rep = analyze(model, template, fake_user)
    user = rep['user']

    print(f"글자: {args.char}   점수: {rep['score']}/100")
    for e in rep['strokes']:
        gain = e.get('gain')
        gs = f" (+{gain:.1f}점 기대)" if gain and gain > 0.5 else ''
        print(f"  획{e['index'] + 1}: q={e['q']:.2f}{gs}  " + ' / '.join(e['messages']))
    print('우선 교정 제안:')
    for c in rep['corrections']:
        tag = f"획{c['index'] + 1}" if c['index'] >= 0 else '누락'
        g = f" (+{c['gain']:.1f}점)" if c.get('gain') else ''
        print(f"  - {tag}{g}: " + ' / '.join(c['messages']))

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6))
    for ax, title in zip(axes, ['template', 'user (per-stroke q)', 'corrections']):
        ax.set_xlim(0, 1); ax.set_ylim(1, 0)  # SVG 좌표계: y 아래로
        ax.set_aspect('equal'); ax.set_title(title, fontsize=11)
        ax.axis('off')
    draw(axes[0], template, color='0.3')
    qv = [e['q'] for e in rep['strokes']]
    draw(axes[1], user, q=qv)
    draw(axes[2], template, color='0.85')
    draw(axes[2], user, q=qv, alpha=0.9)
    grad = rep['grad']
    gmax = np.abs(grad).max() or 1.0
    for i, s in enumerate(user):
        v = grad[i] / gmax * 0.05
        axes[2].quiver(s[::3, 0], s[::3, 1], v[::3, 0], v[::3, 1],
                       angles='xy', scale_units='xy', scale=1, width=0.004,
                       color='crimson', alpha=0.8)
    fig.suptitle(f"'{args.char}'  score {rep['score']}/100", fontsize=13)
    fig.tight_layout()
    fig.savefig(args.out, dpi=140)
    print(f'saved -> {args.out}')


if __name__ == '__main__':
    main()
