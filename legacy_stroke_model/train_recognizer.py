# -*- coding: utf-8 -*-
"""1단계: 손글씨 문자 인식 모델 사전학습.

python -m scorer.train_recognizer --kanji-dir kanji --n-chars 300 --epochs 4
"""
import argparse
import os
import time

import torch
from torch.utils.data import DataLoader

from .data import RecognitionDataset, collate_recognition, load_charset
from .model import Recognizer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--n-chars', type=int, default=300)
    ap.add_argument('--epochs', type=int, default=4)
    ap.add_argument('--batch-size', type=int, default=32)
    ap.add_argument('--samples-per-char', type=int, default=24)
    ap.add_argument('--lr', type=float, default=3e-4)
    ap.add_argument('--out', default='checkpoints/recognizer.pt')
    ap.add_argument('--device', default='auto', help='auto|cuda|cpu')
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--max-strokes', type=int, default=16)
    args = ap.parse_args()

    dev = torch.device('cuda' if torch.cuda.is_available() else 'cpu') \
        if args.device == 'auto' else torch.device(args.device)
    print(f'device: {dev}', flush=True)

    torch.manual_seed(0)
    print(f'loading charset from {args.kanji_dir} ...', flush=True)
    charset = load_charset(args.kanji_dir, n_chars=args.n_chars,
                           max_strokes=args.max_strokes)
    chars = sorted(charset.keys())
    print(f'{len(chars)} chars loaded', flush=True)

    ds = RecognitionDataset(charset, samples_per_char=args.samples_per_char)
    dl = DataLoader(ds, batch_size=args.batch_size, shuffle=True,
                    collate_fn=collate_recognition, num_workers=args.workers,
                    pin_memory=(dev.type == 'cuda'),
                    persistent_workers=(args.workers > 0))

    # 획수 상한에 맞춰 시퀀스 길이 여유 확보 (P=12점/획)
    model = Recognizer(n_classes=len(chars),
                       max_len=max(512, args.max_strokes * 12 + 8),
                       max_strokes=max(40, args.max_strokes + 1)).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=args.epochs * len(dl))

    for epoch in range(args.epochs):
        ds.epoch = epoch
        model.train()
        t0 = time.time()
        tot, correct, seen = 0.0, 0, 0
        for step, (f, s, m, y) in enumerate(dl):
            f, s, m, y = f.to(dev), s.to(dev), m.to(dev), y.to(dev)
            logits = model(f, s, m)
            loss = torch.nn.functional.cross_entropy(logits, y)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot += loss.item() * len(y)
            correct += (logits.argmax(1) == y).sum().item()
            seen += len(y)
            if step % 50 == 0:
                print(f'  ep{epoch} step{step}/{len(dl)} '
                      f'loss={tot / seen:.4f} acc={correct / seen:.3f}', flush=True)
        print(f'epoch {epoch}: loss={tot / seen:.4f} acc={correct / seen:.3f} '
              f'({time.time() - t0:.0f}s)', flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save({'model': {k: v.cpu() for k, v in model.state_dict().items()},
                'chars': chars, 'n_classes': len(chars),
                'max_strokes': args.max_strokes}, args.out)
    print(f'saved -> {args.out}')


if __name__ == '__main__':
    main()
