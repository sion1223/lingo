# -*- coding: utf-8 -*-
"""Chandra OCR 비전 인코더 전이학습 (RunPod 등 GPU 환경 전용).

python -m scorer.train_chandra --kanji-dir kanji --n-chars 1000 --epochs 3 \
    --batch-size 16 --workers 8

백본은 동결(기본), --unfreeze-last N 으로 마지막 N개 비전 블록 미세조정 가능.
"""
import argparse
import os
import time

import numpy as np
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, Dataset
from torch.utils.data.distributed import DistributedSampler

from .chandra_scorer import ChandraScorer, strokes_to_inputs
from .data import load_charset
from .model import scorer_loss
from .synth import make_scoring_sample


class ChandraScoringDataset(Dataset):
    def __init__(self, charset, size=448, samples_per_char=12, seed=0):
        self.chars = sorted(charset.keys())
        self.templates = [charset[c] for c in self.chars]
        self.size = size
        self.spc = samples_per_char
        self.seed = seed
        self.epoch = 0
        # 템플릿 렌더는 캐시 (문자당 1회)
        self._tmpl_cache = {}

    def __len__(self):
        return len(self.chars) * self.spc

    def _tmpl_img(self, ci):
        if ci not in self._tmpl_cache:
            img, _ = strokes_to_inputs(self.templates[ci], self.size)
            self._tmpl_cache[ci] = img
        return self._tmpl_cache[ci]

    def __getitem__(self, idx):
        ci = idx % len(self.chars)
        rng = np.random.default_rng(self.seed + idx * 6151 + self.epoch * 104729)
        user, labels = make_scoring_sample(self.templates[ci], rng)
        uimg, ugw = strokes_to_inputs(user, self.size)
        return uimg, ugw, self._tmpl_img(ci), labels


def collate(batch):
    uimgs, ugws, timgs, labels = zip(*batch)
    B = len(batch)
    Smax = max(g.shape[0] for g in ugws)
    Ntok = ugws[0].shape[1]
    gw = torch.zeros(B, Smax, Ntok)
    smask = torch.zeros(B, Smax, dtype=torch.bool)
    q = torch.zeros(B, Smax)
    rev = torch.zeros(B, Smax)
    ordw = torch.zeros(B, Smax)
    overall = torch.zeros(B)
    for i, (g, l) in enumerate(zip(ugws, labels)):
        S = g.shape[0]
        gw[i, :S] = torch.from_numpy(g)
        smask[i, :S] = True
        q[i, :S] = torch.from_numpy(l['q'])
        rev[i, :S] = torch.from_numpy(l['rev'])
        ordw[i, :S] = torch.from_numpy(l['ord'])
        overall[i] = float(l['overall'])
    lab = dict(q=q, rev=rev, ord=ordw, smask=smask, overall=overall)
    return list(uimgs), gw, list(timgs), smask, lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kanji-dir', default='kanji')
    ap.add_argument('--model-id', default='datalab-to/chandra')
    ap.add_argument('--n-chars', type=int, default=1000, help='0 = 전체')
    ap.add_argument('--max-strokes', type=int, default=24)
    ap.add_argument('--epochs', type=int, default=3)
    ap.add_argument('--batch-size', type=int, default=16)
    ap.add_argument('--samples-per-char', type=int, default=12)
    ap.add_argument('--lr', type=float, default=3e-4)
    ap.add_argument('--backbone-lr', type=float, default=1e-5)
    ap.add_argument('--unfreeze-last', type=int, default=0)
    ap.add_argument('--size', type=int, default=448)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out', default='checkpoints/chandra_scorer.pt')
    args = ap.parse_args()

    assert torch.cuda.is_available(), 'GPU 필요 (RunPod 등에서 실행하세요)'
    # torchrun 실행 시 멀티GPU DDP
    ddp = 'RANK' in os.environ
    if ddp:
        dist.init_process_group('nccl')
        local_rank = int(os.environ['LOCAL_RANK'])
        torch.cuda.set_device(local_rank)
        dev = torch.device('cuda', local_rank)
    else:
        dev = torch.device('cuda')
    rank0 = (not ddp) or dist.get_rank() == 0
    torch.manual_seed(0)

    charset = load_charset(args.kanji_dir, n_chars=args.n_chars or None,
                           max_strokes=args.max_strokes)
    if rank0:
        print(f'{len(charset)} chars loaded (ddp={ddp}, '
              f'world={dist.get_world_size() if ddp else 1})', flush=True)

    ds = ChandraScoringDataset(charset, size=args.size,
                               samples_per_char=args.samples_per_char)
    sampler = DistributedSampler(ds, shuffle=True) if ddp else None
    dl = DataLoader(ds, batch_size=args.batch_size, shuffle=(sampler is None),
                    sampler=sampler, collate_fn=collate,
                    num_workers=args.workers,
                    persistent_workers=(args.workers > 0))

    if rank0:
        print(f'loading Chandra vision tower: {args.model_id}', flush=True)
    model = ChandraScorer(model_id=args.model_id, size=args.size,
                          unfreeze_last=args.unfreeze_last).to(dev)
    # LazyLinear(투영층) 초기화용 더미 forward — 옵티마이저 생성 전에 필요
    dummy = np.zeros((args.size, args.size, 3), np.uint8)
    gw0 = torch.zeros(1, 1, (args.size // 16) ** 2)
    with torch.no_grad():
        model([dummy], gw0, [dummy], torch.ones(1, 1, dtype=torch.bool))
    core = model  # 저장/파라미터 그룹용 원본 참조
    if ddp:
        model = DDP(model, device_ids=[local_rank])
    n_train = sum(p.numel() for p in core.parameters() if p.requires_grad)
    if rank0:
        print(f'trainable params: {n_train / 1e6:.2f}M', flush=True)

    bb_params = [p for p in core.visual.parameters() if p.requires_grad]
    head_params = [p for n, p in core.named_parameters()
                   if not n.startswith('visual.')]
    groups = [{'params': head_params, 'lr': args.lr}]
    if bb_params:
        groups.append({'params': bb_params, 'lr': args.backbone_lr})
    opt = torch.optim.AdamW(groups, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=args.epochs * len(dl))

    for epoch in range(args.epochs):
        ds.epoch = epoch
        if sampler is not None:
            sampler.set_epoch(epoch)
        model.train()
        t0 = time.time()
        agg, seen = {}, 0
        for step, (uimgs, gw, timgs, smask, lab) in enumerate(dl):
            lab = {k: v.to(dev) for k, v in lab.items()}
            out = model(uimgs, gw, timgs, smask)
            loss, parts = scorer_loss(out, lab)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad], 1.0)
            opt.step()
            sched.step()
            seen += 1
            for k, v in parts.items():
                agg[k] = agg.get(k, 0.0) + v
            if rank0 and step % 20 == 0:
                msg = ' '.join(f'{k}={v / seen:.4f}' for k, v in agg.items())
                print(f'  ep{epoch} step{step}/{len(dl)} {msg}', flush=True)
        if rank0:
            msg = ' '.join(f'{k}={v / seen:.4f}' for k, v in agg.items())
            print(f'epoch {epoch}: {msg} ({time.time() - t0:.0f}s)', flush=True)
            os.makedirs(os.path.dirname(args.out), exist_ok=True)
            core.save_heads(args.out)
            print(f'checkpoint -> {args.out}', flush=True)
        if ddp:
            dist.barrier()
    if ddp:
        dist.destroy_process_group()


if __name__ == '__main__':
    main()
