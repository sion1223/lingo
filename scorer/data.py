# -*- coding: utf-8 -*-
"""torch Dataset / 텐서 변환. 시퀀스 표현: 획들을 이어붙인 점 시퀀스.

점 특징 5차원: [x, y, dx, dy, sos(획 시작=1)]
stroke_ids: 각 점이 속한 획 번호 (획별 풀링용), 패딩은 -1.
"""
import glob
import os
import numpy as np
import torch
from torch.utils.data import Dataset

from .kanjivg import parse_svg, normalize_strokes
from .synth import distort, make_scoring_sample

POINTS_PER_STROKE = 12
MAX_STROKES = 16


def featurize(strokes):
    """[(P,2)...] -> (feats (L,5) float32, stroke_ids (L,) int64)"""
    feats, sids = [], []
    for si, st in enumerate(strokes):
        d = np.diff(st, axis=0, prepend=st[:1])
        sos = np.zeros((len(st), 1))
        sos[0, 0] = 1.0
        feats.append(np.concatenate([st, d, sos], axis=1))
        sids.append(np.full(len(st), si))
    return (np.concatenate(feats).astype(np.float32),
            np.concatenate(sids).astype(np.int64))


def load_charset(kanji_dir, n_chars=None, min_strokes=1, max_strokes=MAX_STROKES,
                 points_per_stroke=POINTS_PER_STROKE):
    """kanji 폴더 스캔 -> {char: [(P,2)...]} (획수 조건 통과분, 코드포인트 순)."""
    files = sorted(glob.glob(os.path.join(kanji_dir, '*.svg')))
    charset = {}
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        try:
            cp = int(name, 16)
        except ValueError:
            continue
        # 가나(3040-30FF)와 CJK 통합 한자(4E00~)만 사용 — 라틴/기호/부수 변형 제외
        if not (0x3040 <= cp <= 0x30FF or cp >= 0x4E00):
            continue
        try:
            strokes = parse_svg(f, points_per_stroke)
        except Exception:
            continue
        if not (min_strokes <= len(strokes) <= max_strokes):
            continue
        charset[chr(cp)] = normalize_strokes(strokes)
        if n_chars and len(charset) >= n_chars:
            break
    return charset


class RecognitionDataset(Dataset):
    """1단계 사전학습용: 왜곡된 글씨 -> 어떤 글자인지 분류."""

    def __init__(self, charset, samples_per_char=24, seed=0):
        self.chars = sorted(charset.keys())
        self.templates = [charset[c] for c in self.chars]
        self.spc = samples_per_char
        self.seed = seed
        self.epoch = 0

    def __len__(self):
        return len(self.chars) * self.spc

    def __getitem__(self, idx):
        ci = idx % len(self.chars)
        rng = np.random.default_rng(self.seed + idx * 7919 + self.epoch * 104729)
        user, _, _ = distort(self.templates[ci], rng, severity=float(rng.uniform(0, 0.7)))
        feats, sids = featurize(user)
        return feats, sids, ci


class ScoringDataset(Dataset):
    """2단계 전이학습용: (사용자 글씨, 정답 템플릿) -> 점수/획별 라벨."""

    def __init__(self, charset, samples_per_char=24, seed=0):
        self.chars = sorted(charset.keys())
        self.templates = [charset[c] for c in self.chars]
        self.spc = samples_per_char
        self.seed = seed
        self.epoch = 0

    def __len__(self):
        return len(self.chars) * self.spc

    def __getitem__(self, idx):
        ci = idx % len(self.chars)
        rng = np.random.default_rng(self.seed + idx * 6151 + self.epoch * 104729)
        tmpl = self.templates[ci]
        user, labels = make_scoring_sample(tmpl, rng)
        uf, us = featurize(user)
        tf, ts = featurize(tmpl)
        return uf, us, tf, ts, labels


def _pad_batch(feat_list, sid_list):
    B = len(feat_list)
    L = max(len(f) for f in feat_list)
    feats = torch.zeros(B, L, 5)
    sids = torch.full((B, L), -1, dtype=torch.long)
    mask = torch.ones(B, L, dtype=torch.bool)  # True = 패딩
    for i, (f, s) in enumerate(zip(feat_list, sid_list)):
        n = len(f)
        feats[i, :n] = torch.from_numpy(f)
        sids[i, :n] = torch.from_numpy(s)
        mask[i, :n] = False
    return feats, sids, mask


def collate_recognition(batch):
    feats, sids, labels = zip(*batch)
    f, s, m = _pad_batch(feats, sids)
    return f, s, m, torch.tensor(labels, dtype=torch.long)


def collate_scoring(batch):
    uf, us, tf, ts, labels = zip(*batch)
    fu, su, mu = _pad_batch(uf, us)
    ft, st_, mt = _pad_batch(tf, ts)
    B = len(batch)
    Smax = max(l['q'].shape[0] for l in labels)
    q = torch.zeros(B, Smax)
    rev = torch.zeros(B, Smax)
    ordw = torch.zeros(B, Smax)
    smask = torch.zeros(B, Smax, dtype=torch.bool)  # True = 실제 획
    overall = torch.zeros(B)
    for i, l in enumerate(labels):
        S = l['q'].shape[0]
        q[i, :S] = torch.from_numpy(l['q'])
        rev[i, :S] = torch.from_numpy(l['rev'])
        ordw[i, :S] = torch.from_numpy(l['ord'])
        smask[i, :S] = True
        overall[i] = float(l['overall'])
    return (fu, su, mu), (ft, st_, mt), dict(q=q, rev=rev, ord=ordw,
                                             smask=smask, overall=overall)
