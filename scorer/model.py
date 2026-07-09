# -*- coding: utf-8 -*-
"""모델 정의.

PointEncoder  : 점 시퀀스 Transformer 인코더 (인식/채점 공용 백본)
Recognizer    : 1단계 — 문자 분류 (사전학습 = '손글씨 인식 모델')
Scorer        : 2단계 — 인코더 전이 + 템플릿 교차어텐션 채점 헤드
"""
import torch
import torch.nn as nn


def stroke_pool(h, stroke_ids, max_strokes=None):
    """점 임베딩 (B,L,D) -> 획 임베딩 (B,S,D) 평균 풀링.
    stroke_ids: (B,L), 패딩 -1. 반환 smask (B,S): True=실제 획."""
    B, L, D = h.shape
    S = max_strokes or int(stroke_ids.max().item()) + 1
    sums = h.new_zeros(B, S, D)
    cnts = h.new_zeros(B, S, 1)
    valid = stroke_ids >= 0
    idx = stroke_ids.clamp(min=0).unsqueeze(-1).expand(-1, -1, D)
    sums.scatter_add_(1, idx, h * valid.unsqueeze(-1))
    cnts.scatter_add_(1, stroke_ids.clamp(min=0).unsqueeze(-1),
                      valid.unsqueeze(-1).to(h.dtype))
    emb = sums / cnts.clamp(min=1)
    smask = cnts.squeeze(-1) > 0
    return emb, smask


class PointEncoder(nn.Module):
    def __init__(self, d_model=128, nhead=4, num_layers=3, dim_ff=256,
                 dropout=0.1, max_len=512, max_strokes=40):
        super().__init__()
        self.input_proj = nn.Linear(5, d_model)
        self.pos_emb = nn.Embedding(max_len, d_model)
        self.stroke_emb = nn.Embedding(max_strokes, d_model)
        layer = nn.TransformerEncoderLayer(
            d_model, nhead, dim_ff, dropout, batch_first=True,
            norm_first=True, activation='gelu')
        self.encoder = nn.TransformerEncoder(layer, num_layers)
        self.d_model = d_model

    def forward(self, feats, stroke_ids, pad_mask):
        """feats (B,L,5), stroke_ids (B,L), pad_mask (B,L) True=패딩."""
        B, L, _ = feats.shape
        pos = torch.arange(L, device=feats.device).unsqueeze(0)
        h = (self.input_proj(feats)
             + self.pos_emb(pos)
             + self.stroke_emb(stroke_ids.clamp(min=0)))
        return self.encoder(h, src_key_padding_mask=pad_mask)


class Recognizer(nn.Module):
    """1단계: 어떤 글자인지 분류하는 인식 모델."""

    def __init__(self, n_classes, **enc_kw):
        super().__init__()
        self.encoder = PointEncoder(**enc_kw)
        d = self.encoder.d_model
        self.head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, n_classes))

    def forward(self, feats, stroke_ids, pad_mask):
        h = self.encoder(feats, stroke_ids, pad_mask)
        keep = (~pad_mask).unsqueeze(-1).to(h.dtype)
        pooled = (h * keep).sum(1) / keep.sum(1).clamp(min=1)
        return self.head(pooled)


class Scorer(nn.Module):
    """2단계: 인식 인코더를 전이해 (사용자, 템플릿) 쌍을 채점.

    출력:
      overall     (B,)   전체 점수 0~1
      q           (B,S)  획별 품질 0~1
      rev_logit   (B,S)  방향(필순) 반전 로짓
      ord_logit   (B,S)  획 순서 오류 로짓
    """

    def __init__(self, nhead=4, num_xattn_layers=2, **enc_kw):
        super().__init__()
        self.encoder = PointEncoder(**enc_kw)
        d = self.encoder.d_model
        layer = nn.TransformerDecoderLayer(
            d, nhead, d * 2, 0.1, batch_first=True,
            norm_first=True, activation='gelu')
        self.xattn = nn.TransformerDecoder(layer, num_xattn_layers)
        self.q_head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d // 2),
                                    nn.GELU(), nn.Linear(d // 2, 3))
        self.overall_head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d // 2),
                                          nn.GELU(), nn.Linear(d // 2, 1))

    def forward(self, user, template):
        """user/template: (feats, stroke_ids, pad_mask) 튜플."""
        fu, su, mu = user
        ft, st, mt = template
        hu = self.encoder(fu, su, mu)
        ht = self.encoder(ft, st, mt)
        eu, smask_u = stroke_pool(hu, su)
        et, smask_t = stroke_pool(ht, st)
        # 사용자 획이 템플릿 획들을 참조
        z = self.xattn(eu, et, tgt_key_padding_mask=~smask_u,
                       memory_key_padding_mask=~smask_t)
        heads = self.q_head(z)                      # (B,S,3)
        q = torch.sigmoid(heads[..., 0])
        rev_logit = heads[..., 1]
        ord_logit = heads[..., 2]
        keep = smask_u.unsqueeze(-1).to(z.dtype)
        pooled = (z * keep).sum(1) / keep.sum(1).clamp(min=1)
        overall = torch.sigmoid(self.overall_head(pooled)).squeeze(-1)
        return dict(overall=overall, q=q, rev_logit=rev_logit,
                    ord_logit=ord_logit, smask=smask_u)

    def load_pretrained_encoder(self, recognizer_state):
        """1단계 인식 모델 체크포인트에서 인코더 가중치 전이."""
        enc_state = {k[len('encoder.'):]: v for k, v in recognizer_state.items()
                     if k.startswith('encoder.')}
        self.encoder.load_state_dict(enc_state)


def scorer_loss(out, labels, w_overall=4.0, w_q=2.0, w_flag=0.5):
    m = labels['smask'] & out['smask']
    mf = m.to(out['q'].dtype)
    n = mf.sum().clamp(min=1)
    loss_q = (((out['q'] - labels['q']) ** 2) * mf).sum() / n
    bce = nn.functional.binary_cross_entropy_with_logits
    loss_rev = (bce(out['rev_logit'], labels['rev'], reduction='none') * mf).sum() / n
    loss_ord = (bce(out['ord_logit'], labels['ord'], reduction='none') * mf).sum() / n
    loss_overall = ((out['overall'] - labels['overall']) ** 2).mean()
    total = (w_overall * loss_overall + w_q * loss_q
             + w_flag * (loss_rev + loss_ord))
    return total, dict(overall=loss_overall.item(), q=loss_q.item(),
                       rev=loss_rev.item(), ord=loss_ord.item())
