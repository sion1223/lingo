# -*- coding: utf-8 -*-
"""torch Dataset / 텐서 변환. 시퀀스 표현: 획들을 이어붙인 점 시퀀스.

점 특징 5차원: [x, y, dx, dy, sos(획 시작=1)]
stroke_ids: 각 점이 속한 획 번호 (획별 풀링용), 패딩은 -1.
"""
from __future__ import annotations

import glob
import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

import numpy as np
import torch
from torch.utils.data import Dataset

from .kanjivg import parse_svg, normalize_strokes
from .synth import distort, make_scoring_sample

if TYPE_CHECKING:
    from .confusion_dataset import ConfusionSample

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


class CoordinateConfusionDataset(Dataset):
    """Common confusion samples encoded for the coordinate scorer."""

    def __init__(self, samples: Sequence[ConfusionSample]):
        self.samples = tuple(samples)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        uf, us = featurize(sample.user_strokes)
        tf, ts = featurize(sample.target_template)
        return uf, us, tf, ts, sample


class RenderedConfusionDataset(Dataset):
    """Common confusion samples encoded for an image/grid scorer.

    ``renderer`` follows ``strokes_to_inputs(strokes, size)`` and is injected
    so this shared data module does not import or initialize a vision model.
    """

    def __init__(
        self,
        samples: Sequence[ConfusionSample],
        renderer: Callable,
        *,
        size: int = 448,
    ):
        if not callable(renderer):
            raise TypeError("renderer must be callable")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError("size must be a positive integer")
        self.samples = tuple(samples)
        self.renderer = renderer
        self.size = size
        self._tmpl_cache = {}

    def __len__(self):
        return len(self.samples)

    def _template_image(self, sample: ConfusionSample):
        if sample.target_char not in self._tmpl_cache:
            image, _ = self.renderer(sample.target_template, self.size)
            self._tmpl_cache[sample.target_char] = image
        return self._tmpl_cache[sample.target_char]

    def __getitem__(self, idx):
        sample = self.samples[idx]
        user_image, user_grid = self.renderer(sample.user_strokes, self.size)
        return user_image, user_grid, self._template_image(sample), sample


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


def collate_confusion_supervision(samples: Sequence[ConfusionSample]):
    """Tensorize nullable identity/quality labels without losing their masks."""
    if not samples:
        raise ValueError("cannot collate an empty confusion batch")

    def masked_float(field: str):
        values = []
        mask = []
        for sample in samples:
            value = getattr(sample, field)
            mask.append(value is not None)
            values.append(0.0 if value is None else float(value))
        return (
            torch.tensor(values, dtype=torch.float32),
            torch.tensor(mask, dtype=torch.bool),
        )

    is_target, is_target_mask = masked_float("is_target")
    target_match, target_match_mask = masked_float("target_match")
    quality, quality_mask = masked_float("quality_for_written_char")
    labels = {
        "is_target": is_target,
        "is_target_mask": is_target_mask,
        "target_match": target_match,
        "target_match_mask": target_match_mask,
        "quality_for_written_char": quality,
        "quality_mask": quality_mask,
        "ambiguity": torch.tensor(
            [sample.ambiguity for sample in samples], dtype=torch.bool
        ),
    }
    metadata = tuple(sample.pair_metadata() for sample in samples)
    return labels, metadata


def collate_coordinate_confusions(batch):
    """Collate coordinate pairs plus the common confusion supervision."""
    uf, us, tf, ts, samples = zip(*batch)
    fu, su, mu = _pad_batch(uf, us)
    ft, st_, mt = _pad_batch(tf, ts)
    labels, metadata = collate_confusion_supervision(samples)
    return (fu, su, mu), (ft, st_, mt), labels, metadata


def collate_rendered_confusions(batch):
    """Collate Chandra-style images/grids with the same pair supervision."""
    user_images, user_grids, template_images, samples = zip(*batch)
    batch_size = len(batch)
    max_strokes = max(grid.shape[0] for grid in user_grids)
    max_tokens = max(grid.shape[1] for grid in user_grids)
    grids = torch.zeros(batch_size, max_strokes, max_tokens)
    stroke_mask = torch.zeros(batch_size, max_strokes, dtype=torch.bool)
    for index, grid in enumerate(user_grids):
        strokes, tokens = grid.shape
        grids[index, :strokes, :tokens] = torch.from_numpy(
            np.asarray(grid, dtype=np.float32)
        )
        stroke_mask[index, :strokes] = True
    labels, metadata = collate_confusion_supervision(samples)
    return (
        list(user_images),
        grids,
        list(template_images),
        stroke_mask,
        labels,
        metadata,
    )
