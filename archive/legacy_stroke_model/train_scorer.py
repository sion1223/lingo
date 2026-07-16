# -*- coding: utf-8 -*-
"""2단계: 인식 모델 인코더를 전이해 채점 모델 파인튜닝.

python -m scorer.train_scorer --kanji-dir kanji --n-chars 300 --epochs 4
"""
import argparse
import os
import time

import torch
from torch.utils.data import DataLoader

from .data import ScoringDataset, collate_scoring, load_charset
from .model import Scorer, scorer_loss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--n-chars', type=int, default=300)
    ap.add_argument('--epochs', type=int, default=4)
    ap.add_argument('--batch-size', type=int, default=24)
    ap.add_argument('--samples-per-char', type=int, default=20)
    ap.add_argument('--lr', type=float, default=2e-4)
    ap.add_argument('--encoder-lr', type=float, default=5e-5,
                    help='전이된 인코더는 낮은 학습률로 미세조정')
    ap.add_argument('--pretrained', default='checkpoints/recognizer.pt')
    ap.add_argument('--out', default='checkpoints/scorer.pt')
    ap.add_argument('--device', default='auto', help='auto|cuda|cpu')
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--max-strokes', type=int, default=16)
    args = ap.parse_args()

    dev = torch.device('cuda' if torch.cuda.is_available() else 'cpu') \
        if args.device == 'auto' else torch.device(args.device)
    print(f'device: {dev}', flush=True)

    torch.manual_seed(0)
    charset = load_charset(args.kanji_dir, n_chars=args.n_chars,
                           max_strokes=args.max_strokes)
    print(f'{len(charset)} chars loaded', flush=True)

    ds = ScoringDataset(charset, samples_per_char=args.samples_per_char)
    dl = DataLoader(ds, batch_size=args.batch_size, shuffle=True,
                    collate_fn=collate_scoring, num_workers=args.workers,
                    pin_memory=(dev.type == 'cuda'),
                    persistent_workers=(args.workers > 0))

    model = Scorer(max_len=max(512, args.max_strokes * 12 + 8),
                   max_strokes=max(40, args.max_strokes + 1)).to(dev)
    if os.path.exists(args.pretrained):
        ckpt = torch.load(args.pretrained, map_location='cpu', weights_only=False)
        model.load_pretrained_encoder(ckpt['model'])
        print(f'transferred encoder from {args.pretrained}', flush=True)
    else:
        print(f'WARNING: {args.pretrained} 없음 — 인코더를 처음부터 학습', flush=True)

    enc_params = list(model.encoder.parameters())
    enc_ids = {id(p) for p in enc_params}
    head_params = [p for p in model.parameters() if id(p) not in enc_ids]
    opt = torch.optim.AdamW([
        {'params': enc_params, 'lr': args.encoder_lr},
        {'params': head_params, 'lr': args.lr},
    ], weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=args.epochs * len(dl))

    for epoch in range(args.epochs):
        ds.epoch = epoch
        model.train()
        t0 = time.time()
        agg = {}
        seen = 0
        for step, (user, tmpl, labels) in enumerate(dl):
            user = tuple(t.to(dev) for t in user)
            tmpl = tuple(t.to(dev) for t in tmpl)
            labels = {k: v.to(dev) for k, v in labels.items()}
            out = model(user, tmpl)
            loss, parts = scorer_loss(out, labels)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            seen += 1
            for k, v in parts.items():
                agg[k] = agg.get(k, 0.0) + v
            if step % 50 == 0:
                msg = ' '.join(f'{k}={v / seen:.4f}' for k, v in agg.items())
                print(f'  ep{epoch} step{step}/{len(dl)} {msg}', flush=True)
        msg = ' '.join(f'{k}={v / seen:.4f}' for k, v in agg.items())
        print(f'epoch {epoch}: {msg} ({time.time() - t0:.0f}s)', flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save({'model': {k: v.cpu() for k, v in model.state_dict().items()},
                'max_strokes': args.max_strokes}, args.out)
    print(f'saved -> {args.out}')


if __name__ == '__main__':
    main()
