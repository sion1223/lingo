# -*- coding: utf-8 -*-
"""좌표 기반 문자 인식 사전학습. 채점기의 PointEncoder 초기값을 만든다."""
import argparse
import os
import time
from contextlib import nullcontext

import torch
from torch.utils.data import DataLoader

from .data import RecognitionDataset, collate_recognition, load_charset
from .model import Recognizer


@torch.no_grad()
def evaluate(model, loader, device, amp=False):
    model.eval()
    total_loss = correct = seen = 0
    for feats, stroke_ids, pad_mask, labels in loader:
        feats, stroke_ids = feats.to(device), stroke_ids.to(device)
        pad_mask, labels = pad_mask.to(device), labels.to(device)
        ctx = torch.autocast('cuda', dtype=torch.bfloat16) if amp else nullcontext()
        with ctx:
            logits = model(feats, stroke_ids, pad_mask)
            loss = torch.nn.functional.cross_entropy(logits, labels)
        total_loss += loss.detach().item() * len(labels)
        correct += int((logits.argmax(1) == labels).sum())
        seen += len(labels)
    return {'loss': total_loss / seen, 'accuracy': correct / seen}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--n-chars', type=int, default=0, help='0 = 전체')
    ap.add_argument('--epochs', type=int, default=6)
    ap.add_argument('--batch-size', type=int, default=256)
    ap.add_argument('--samples-per-char', type=int, default=20)
    ap.add_argument('--val-samples-per-char', type=int, default=2)
    ap.add_argument('--lr', type=float, default=3e-4)
    ap.add_argument('--out', default='checkpoints/stroke_recognizer.pt')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--max-strokes', type=int, default=24)
    ap.add_argument('--seed', type=int, default=17)
    ap.add_argument('--no-amp', action='store_true')
    args = ap.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu') \
        if args.device == 'auto' else torch.device(args.device)
    amp = device.type == 'cuda' and not args.no_amp
    torch.manual_seed(args.seed)
    if device.type == 'cuda':
        torch.set_float32_matmul_precision('high')

    charset = load_charset(args.kanji_dir, n_chars=args.n_chars or None,
                           max_strokes=args.max_strokes)
    chars = sorted(charset)
    print(f'device={device} amp={amp} chars={len(chars)}', flush=True)
    train_ds = RecognitionDataset(charset, args.samples_per_char, seed=args.seed)
    val_ds = RecognitionDataset(charset, args.val_samples_per_char,
                                seed=args.seed + 10_000_019)
    loader_kw = dict(collate_fn=collate_recognition, num_workers=args.workers,
                     pin_memory=device.type == 'cuda')
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                          **loader_kw)
    val_dl = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                        **loader_kw)

    model = Recognizer(
        n_classes=len(chars), max_len=max(512, args.max_strokes * 12 + 8),
        max_strokes=max(40, args.max_strokes + 1)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=args.epochs * len(train_dl))
    best = -1.0

    for epoch in range(args.epochs):
        train_ds.epoch = epoch
        model.train()
        t0 = time.time()
        loss_sum = correct = seen = 0
        for step, (feats, stroke_ids, pad_mask, labels) in enumerate(train_dl):
            feats, stroke_ids = feats.to(device), stroke_ids.to(device)
            pad_mask, labels = pad_mask.to(device), labels.to(device)
            ctx = torch.autocast('cuda', dtype=torch.bfloat16) if amp else nullcontext()
            with ctx:
                logits = model(feats, stroke_ids, pad_mask)
                loss = torch.nn.functional.cross_entropy(logits, labels)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step()
            loss_sum += loss.detach().item() * len(labels)
            correct += int((logits.argmax(1) == labels).sum())
            seen += len(labels)
            if step % 100 == 0:
                print(f'  ep{epoch} {step}/{len(train_dl)} '
                      f'loss={loss_sum/seen:.4f} acc={correct/seen:.4f}', flush=True)
        metrics = evaluate(model, val_dl, device, amp)
        print(f'epoch {epoch}: train_acc={correct/seen:.4f} '
              f'val_loss={metrics["loss"]:.4f} val_acc={metrics["accuracy"]:.4f} '
              f'({time.time()-t0:.0f}s)', flush=True)
        if metrics['accuracy'] > best:
            best = metrics['accuracy']
            os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
            torch.save({
                'model': {k: v.cpu() for k, v in model.state_dict().items()},
                'chars': chars, 'n_classes': len(chars),
                'max_strokes': args.max_strokes, 'val_metrics': metrics,
            }, args.out)
            print(f'best checkpoint -> {args.out}', flush=True)


if __name__ == '__main__':
    main()
