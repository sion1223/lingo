# -*- coding: utf-8 -*-
"""고수준 추론 API.

    from scorer.score_api import KanjiScorer
    ks = KanjiScorer('checkpoints/scorer.pt', 'kanji')
    report = ks.score('永', user_strokes)   # user_strokes: [(N,2) 픽셀좌표...]
"""
import numpy as np
import torch

from .feedback import analyze
from .kanjivg import load_char
from .model import Scorer


class KanjiScorer:
    def __init__(self, checkpoint='checkpoints/scorer.pt', kanji_dir='kanji'):
        self.kanji_dir = kanji_dir
        ckpt = torch.load(checkpoint, map_location='cpu', weights_only=False)
        sd = ckpt['model']
        self.model = Scorer(max_len=sd['encoder.pos_emb.weight'].shape[0],
                            max_strokes=sd['encoder.stroke_emb.weight'].shape[0])
        self.model.load_state_dict(ckpt['model'])
        self.model.eval()
        self._tmpl_cache = {}

    def template(self, ch):
        if ch not in self._tmpl_cache:
            self._tmpl_cache[ch] = load_char(self.kanji_dir, ch)
        return self._tmpl_cache[ch]

    def score(self, ch, raw_strokes, top_k=3):
        """ch: 목표 글자. raw_strokes: [(N_i,2)...] 임의 좌표계 (픽셀 등).
        반환: JSON 직렬화 가능한 리포트."""
        report = analyze(self.model, self.template(ch), raw_strokes, top_k=top_k)
        return self._jsonable(report)

    @staticmethod
    def _jsonable(report):
        out = dict(report)
        out['grad'] = report['grad'].tolist()
        out['user'] = [s.tolist() for s in report['user']]
        return out
