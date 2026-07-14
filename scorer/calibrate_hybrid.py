# -*- coding: utf-8 -*-
"""홀드아웃 합성 샘플에서 Chandra/스트로크 결합 가중치를 보정한다."""
import argparse
import json
import os

import numpy as np
import torch

from .chandra_scorer import _score_once, load_chandra_scorer
from .data import load_charset
from .feedback import _to_batch
from .hybrid import load_stroke_scorer
from .synth import make_scoring_sample
from .train_scorer import split_charset


def optimal_weight(vision, stroke, target):
    delta = stroke - vision
    denom = float(np.dot(delta, delta))
    if denom < 1e-12:
        return 0.0
    return float(np.clip(np.dot(delta, target - vision) / denom, 0.0, 1.0))


def mse(pred, target):
    return float(np.mean((pred - target) ** 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--chandra-checkpoint', default='checkpoints/chandra_scorer.pt')
    ap.add_argument('--stroke-checkpoint', default='checkpoints/stroke_scorer.pt')
    ap.add_argument('--samples', type=int, default=256)
    ap.add_argument('--max-strokes', type=int, default=24)
    ap.add_argument('--seed', type=int, default=20260714)
    ap.add_argument('--train-split-seed', type=int, default=23)
    ap.add_argument('--val-fraction', type=float, default=0.10)
    ap.add_argument('--out', default='checkpoints/hybrid_config.json')
    args = ap.parse_args()

    charset = load_charset(args.kanji_dir, max_strokes=args.max_strokes)
    _, held_out = split_charset(charset, args.val_fraction, args.train_split_seed)
    chars = sorted(held_out)
    rng = np.random.default_rng(args.seed)
    selected = rng.choice(chars, size=min(args.samples, len(chars)), replace=False)
    vision_model = load_chandra_scorer(args.chandra_checkpoint).eval()
    stroke_model = load_stroke_scorer(args.stroke_checkpoint, device='cpu')

    collected = {name: [[], [], []] for name in ('overall', 'q', 'rev', 'ord')}
    for index, char in enumerate(selected):
        user, labels = make_scoring_sample(charset[char], rng)
        vision = _score_once(vision_model, user, charset[char])
        with torch.no_grad():
            stroke = stroke_model(_to_batch(user), _to_batch(charset[char]))
        collected['overall'][0].append(float(vision['overall'][0]))
        collected['overall'][1].append(float(stroke['overall'][0]))
        collected['overall'][2].append(float(labels['overall']))
        for name, output_name, label_name in (
                ('q', 'q', 'q'), ('rev', 'rev_logit', 'rev'),
                ('ord', 'ord_logit', 'ord')):
            vision_values = vision[output_name][0].float().cpu().numpy()
            stroke_values = stroke[output_name][0].float().cpu().numpy()
            if name != 'q':
                vision_values = 1.0 / (1.0 + np.exp(-vision_values))
                stroke_values = 1.0 / (1.0 + np.exp(-stroke_values))
            collected[name][0].extend(vision_values.tolist())
            collected[name][1].extend(stroke_values.tolist())
            collected[name][2].extend(labels[label_name].tolist())
        if index % 25 == 0:
            print(f'calibration {index}/{len(selected)}', flush=True)

    weights = {}
    metrics = {}
    for name, (vision_values, stroke_values, targets) in collected.items():
        v = np.asarray(vision_values); s = np.asarray(stroke_values); y = np.asarray(targets)
        weight = optimal_weight(v, s, y)
        weights[name] = weight
        metrics[name] = {
            'chandra': mse(v, y), 'stroke': mse(s, y),
            'hybrid': mse(v * (1.0 - weight) + s * weight, y),
        }
    result = {
        'weights': weights,
        'samples': len(selected),
        'seed': args.seed,
        'train_split_seed': args.train_split_seed,
        'metrics': metrics,
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
