# -*- coding: utf-8 -*-
"""Chandra 비전 모델과 좌표 기반 스트로크 모델의 앙상블."""
from __future__ import annotations

import torch

from .chandra_scorer import strokes_to_inputs
from .feedback import _to_batch
from .model import Scorer


def load_stroke_scorer(checkpoint, device='cpu'):
    """학습된 경량 스트로크 채점기를 체크포인트 구조에 맞춰 복원한다."""
    ckpt = torch.load(checkpoint, map_location='cpu', weights_only=False)
    state = ckpt['model']
    model = Scorer(
        max_len=state['encoder.pos_emb.weight'].shape[0],
        max_strokes=state['encoder.stroke_emb.weight'].shape[0],
    )
    model.load_state_dict(state)
    return model.to(device).eval()


def _stroke_weights(value):
    if isinstance(value, dict):
        return {name: float(value.get(name, value.get('overall', 0.35)))
                for name in ('overall', 'q', 'rev', 'ord')}
    return {name: float(value) for name in ('overall', 'q', 'rev', 'ord')}


def _blend_outputs(vision, stroke, stroke_weight):
    """두 모델의 점수와 확률을 같은 출력 계약으로 결합한다."""
    weights = _stroke_weights(stroke_weight)
    if any(not 0.0 <= weight <= 1.0 for weight in weights.values()):
        raise ValueError('stroke weights must be between 0 and 1')
    dev = vision['overall'].device

    def mix(a, b, weight):
        w = weights[weight]
        return a * (1.0 - w) + b.to(dev) * w

    overall = mix(vision['overall'], stroke['overall'], 'overall')
    q = mix(vision['q'], stroke['q'], 'q')
    eps = 1e-5
    rev_prob = mix(torch.sigmoid(vision['rev_logit']),
                   torch.sigmoid(stroke['rev_logit']), 'rev').clamp(eps, 1 - eps)
    ord_prob = mix(torch.sigmoid(vision['ord_logit']),
                   torch.sigmoid(stroke['ord_logit']), 'ord').clamp(eps, 1 - eps)
    return dict(
        overall=overall,
        q=q,
        rev_logit=torch.logit(rev_prob),
        ord_logit=torch.logit(ord_prob),
        smask=vision['smask'],
    )


class HybridScorer:
    """이미지의 형태 판단과 원시 좌표의 순서/방향 판단을 함께 사용한다."""

    def __init__(self, vision_model, stroke_model, stroke_weight=0.35):
        self.vision_model = vision_model
        self.stroke_model = stroke_model
        self.stroke_weights = _stroke_weights(stroke_weight)

    @property
    def size(self):
        return self.vision_model.size

    def eval(self):
        self.vision_model.eval()
        self.stroke_model.eval()
        return self

    @torch.no_grad()
    def score_strokes(self, user, template):
        uimg, ugw = strokes_to_inputs(user, self.vision_model.size)
        timg, _ = strokes_to_inputs(template, self.vision_model.size)
        smask = torch.ones(1, len(user), dtype=torch.bool)
        vision = self.vision_model(
            [uimg], torch.from_numpy(ugw).unsqueeze(0), [timg], smask)

        stroke_dev = next(self.stroke_model.parameters()).device
        user_batch = tuple(x.to(stroke_dev) for x in _to_batch(user))
        tmpl_batch = tuple(x.to(stroke_dev) for x in _to_batch(template))
        stroke = self.stroke_model(user_batch, tmpl_batch)
        return _blend_outputs(vision, stroke, self.stroke_weights)
