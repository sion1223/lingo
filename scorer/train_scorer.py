# -*- coding: utf-8 -*-
"""좌표 기반 스트로크 채점기 학습. 문자 단위 홀드아웃으로 일반화를 검증한다."""
import argparse
import os
import time
from contextlib import nullcontext

import numpy as np
import torch
from torch.utils.data import DataLoader

from .data import ScoringDataset, collate_scoring, load_charset
from .model import Scorer, scorer_loss


def split_charset(charset, val_fraction, seed):
    chars = np.array(sorted(charset))
    rng = np.random.default_rng(seed)
    rng.shuffle(chars)
    n_val = max(1, round(len(chars) * val_fraction))
    val_chars = set(chars[:n_val].tolist())
    train = {c: charset[c] for c in charset if c not in val_chars}
    val = {c: charset[c] for c in charset if c in val_chars}
    return train, val


@torch.no_grad()
def evaluate(model, loader, device, amp=False):
    model.eval()
    sums = dict(overall=0.0, q=0.0, rev_correct=0.0, ord_correct=0.0)
    batches = points = 0
    for user, tmpl, labels in loader:
        user = tuple(x.to(device) for x in user)
        tmpl = tuple(x.to(device) for x in tmpl)
        labels = {k: v.to(device) for k, v in labels.items()}
        ctx = torch.autocast('cuda', dtype=torch.bfloat16) if amp else nullcontext()
        with ctx:
            out = model(user, tmpl)
        mask = labels['smask'] & out['smask']
        count = int(mask.sum())
        sums['overall'] += float(((out['overall'] - labels['overall']) ** 2).sum())
        sums['q'] += float((((out['q'] - labels['q']) ** 2) * mask).sum())
        sums['rev_correct'] += int(((out['rev_logit'] > 0) == labels['rev'].bool())[mask].sum())
        sums['ord_correct'] += int(((out['ord_logit'] > 0) == labels['ord'].bool())[mask].sum())
        batches += len(labels['overall'])
        points += count
    return dict(overall_mse=sums['overall'] / batches,
                q_mse=sums['q'] / points,
                rev_accuracy=sums['rev_correct'] / points,
                ord_accuracy=sums['ord_correct'] / points)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--n-chars', type=int, default=0, help='0 = 전체')
    ap.add_argument('--epochs', type=int, default=10)
    ap.add_argument('--batch-size', type=int, default=256)
    ap.add_argument('--samples-per-char', type=int, default=24)
    ap.add_argument('--val-samples-per-char', type=int, default=4)
    ap.add_argument('--val-fraction', type=float, default=0.10)
    ap.add_argument('--lr', type=float, default=2e-4)
    ap.add_argument('--encoder-lr', type=float, default=5e-5)
    ap.add_argument('--pretrained', default='checkpoints/stroke_recognizer.pt')
    ap.add_argument('--out', default='checkpoints/stroke_scorer.pt')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--max-strokes', type=int, default=24)
    ap.add_argument('--seed', type=int, default=23)
    ap.add_argument('--patience', type=int, default=3)
    ap.add_argument('--no-amp', action='store_true')
    args = ap.parse_args()
    if not 0 < args.val_fraction < 0.5:
        raise ValueError('--val-fraction must be between 0 and 0.5')

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu') \
        if args.device == 'auto' else torch.device(args.device)
    amp = device.type == 'cuda' and not args.no_amp
    torch.manual_seed(args.seed)
    if device.type == 'cuda':
        torch.set_float32_matmul_precision('high')

    charset = load_charset(args.kanji_dir, n_chars=args.n_chars or None,
                           max_strokes=args.max_strokes)
    train_chars, val_chars = split_charset(charset, args.val_fraction, args.seed)
    print(f'device={device} amp={amp} train_chars={len(train_chars)} '
          f'val_chars={len(val_chars)}', flush=True)
    train_ds = ScoringDataset(train_chars, args.samples_per_char, seed=args.seed)
    val_ds = ScoringDataset(val_chars, args.val_samples_per_char,
                            seed=args.seed + 10_000_019)
    loader_kw = dict(collate_fn=collate_scoring, num_workers=args.workers,
                     pin_memory=device.type == 'cuda')
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                          **loader_kw)
    val_dl = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                        **loader_kw)

    model = Scorer(max_len=max(512, args.max_strokes * 12 + 8),
                   max_strokes=max(40, args.max_strokes + 1)).to(device)
    if args.pretrained and os.path.exists(args.pretrained):
        ckpt = torch.load(args.pretrained, map_location='cpu', weights_only=False)
        model.load_pretrained_encoder(ckpt['model'])
        print(f'transferred encoder from {args.pretrained}', flush=True)
    else:
        print('WARNING: recognizer checkpoint missing; training from scratch', flush=True)

    enc_params = list(model.encoder.parameters())
    enc_ids = {id(p) for p in enc_params}
    heads = [p for p in model.parameters() if id(p) not in enc_ids]
    opt = torch.optim.AdamW([
        {'params': enc_params, 'lr': args.encoder_lr},
        {'params': heads, 'lr': args.lr}], weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=args.epochs * len(train_dl))
    best = float('inf')
    stale = 0

    for epoch in range(args.epochs):
        train_ds.epoch = epoch
        model.train(); t0 = time.time(); seen = 0; aggregate = {}
        for step, (user, tmpl, labels) in enumerate(train_dl):
            user = tuple(x.to(device) for x in user)
            tmpl = tuple(x.to(device) for x in tmpl)
            labels = {k: v.to(device) for k, v in labels.items()}
            ctx = torch.autocast('cuda', dtype=torch.bfloat16) if amp else nullcontext()
            with ctx:
                out = model(user, tmpl)
                loss, parts = scorer_loss(out, labels)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); seen += 1
            for key, value in parts.items():
                aggregate[key] = aggregate.get(key, 0.0) + value
            if step % 100 == 0:
                msg = ' '.join(f'{k}={v/seen:.4f}' for k, v in aggregate.items())
                print(f'  ep{epoch} {step}/{len(train_dl)} {msg}', flush=True)
        metrics = evaluate(model, val_dl, device, amp)
        print(f'epoch {epoch}: val={metrics} ({time.time()-t0:.0f}s)', flush=True)
        if metrics['overall_mse'] < best:
            best = metrics['overall_mse']; stale = 0
            os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
            torch.save({
                'model': {k: v.cpu() for k, v in model.state_dict().items()},
                'max_strokes': args.max_strokes, 'val_metrics': metrics,
                'train_chars': len(train_chars), 'val_chars': len(val_chars),
            }, args.out)
            print(f'best checkpoint -> {args.out}', flush=True)
        else:
            stale += 1
            if stale >= args.patience:
                print(f'early stopping after {stale} stale epochs', flush=True)
                break


if __name__ == '__main__':
    main()
