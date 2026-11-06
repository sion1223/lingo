# -*- coding: utf-8 -*-
"""Chandra OCR (datalab-to/chandra, Qwen3-VL 기반) 비전 인코더 전이학습 채점 모델.

- 백본: Chandra 의 비전 타워 (동결, 선택적으로 마지막 N개 블록 미세조정)
- 입력: 시간 인코딩 래스터 이미지 (render.py — 잉크/방향/획순서 3채널)
- 획별 진단: 획별 마스크로 패치 특징을 풀링 -> 획 임베딩
- 헤드: 사용자 획 임베딩이 템플릿 패치 특징을 교차어텐션으로 참조
        -> 전체 점수 / 획별 품질 q / 방향 반전 / 순서 오류

GPU(RunPod 등) 전용 — 8B 모델의 비전 타워라 CPU 학습은 비현실적.
"""
import numpy as np
import torch
import torch.nn as nn

from .render import render_time_encoded, masks_to_grid

CHANDRA_ID = 'datalab-to/chandra'


# qwen3vl 계열 체크포인트의 비전 가중치 접두사 (구버전 호환: 'visual.')
VISION_WEIGHT_PREFIXES = ('model.visual.', 'visual.')


def _vision_state_dict(model_id):
    """비전 가중치가 든 safetensors 샤드만 받아 'visual.' 텐서만 읽는다.

    Chandra 비전 가중치는 첫 샤드(5GB)에만 있으므로 8B 전체(약 17GB)를
    받거나 RAM 에 올릴 필요가 없다 — 4GB 노트북 GPU 에서도 서빙 가능.
    """
    import json
    from huggingface_hub import hf_hub_download
    from safetensors import safe_open
    index_path = hf_hub_download(model_id, 'model.safetensors.index.json')
    with open(index_path, encoding='utf-8') as f:
        weight_map = json.load(f)['weight_map']
    prefix = next((p for p in VISION_WEIGHT_PREFIXES
                   if any(k.startswith(p) for k in weight_map)), None)
    if prefix is None:
        raise ValueError(f'{model_id}: 비전 타워 가중치를 찾지 못했습니다')
    shards = sorted({s for k, s in weight_map.items() if k.startswith(prefix)})
    state = {}
    for shard in shards:
        with safe_open(hf_hub_download(model_id, shard), framework='pt') as f:
            for key in f.keys():
                if key.startswith(prefix):
                    state[key[len(prefix):]] = f.get_tensor(key)
    return state


def _load_vision_tower(model_id, dtype):
    """Chandra 전체를 로드하지 않고 비전 타워만 설정에서 조립해 가중치를 채운다."""
    from transformers import AutoConfig, AutoProcessor
    from transformers.models.qwen3_vl.modeling_qwen3_vl import Qwen3VLVisionModel
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    cfg = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
    visual = Qwen3VLVisionModel._from_config(cfg.vision_config, torch_dtype=dtype)
    visual.load_state_dict(_vision_state_dict(model_id), strict=True)
    visual.eval()
    vis_dim = getattr(cfg.vision_config, 'out_hidden_size',
                      getattr(cfg.vision_config, 'hidden_size', None))
    return visual, processor, int(vis_dim)


class ChandraScorer(nn.Module):
    def __init__(self, model_id=CHANDRA_ID, d_head=256, nhead=4,
                 num_xattn_layers=2, unfreeze_last=0,
                 dtype=torch.bfloat16, size=448):
        super().__init__()
        self.size = size
        self.model_id = model_id
        self.backbone_dtype = dtype
        self.visual, self.processor, vis_dim = _load_vision_tower(model_id, dtype)
        self.visual.requires_grad_(False)
        if unfreeze_last > 0:
            blocks = list(self.visual.blocks)[-unfreeze_last:]
            for b in blocks:
                b.requires_grad_(True)
        d = d_head
        # 비전 특징 차원은 백본 구현에 따라 다르므로 첫 forward에서 자동 결정
        self.proj_user = nn.LazyLinear(d)
        self.proj_tmpl = nn.LazyLinear(d)
        layer = nn.TransformerDecoderLayer(d, nhead, d * 2, 0.1,
                                           batch_first=True, norm_first=True,
                                           activation='gelu')
        self.xattn = nn.TransformerDecoder(layer, num_xattn_layers)
        self.q_head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d // 2),
                                    nn.GELU(), nn.Linear(d // 2, 3))
        self.overall_head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d // 2),
                                          nn.GELU(), nn.Linear(d // 2, 1))

    # ---------- 백본 특징 추출 ----------
    def _encode_images(self, images_u8):
        """images_u8: list of (H,W,3) uint8 -> 이미지별 (N_i, vis_dim) float32 리스트."""
        dev = next(self.proj_user.parameters()).device
        ip = self.processor.image_processor
        enc = ip(images=list(images_u8), return_tensors='pt')
        pixel_values = enc['pixel_values'].to(dev, self.backbone_dtype)
        grid_thw = enc['image_grid_thw'].to(dev)
        need_grad = any(p.requires_grad for p in self.visual.parameters())
        with torch.set_grad_enabled(need_grad and self.training):
            out = self.visual(pixel_values, grid_thw=grid_thw)
        if isinstance(out, (tuple, list)):
            feats = out[0]
        elif hasattr(out, 'last_hidden_state'):
            feats = out.last_hidden_state
        else:
            feats = out
        # 이미지별 토큰 수: 패치수/토큰수 비율을 실측해 분할 (merge 계수에 의존하지 않음)
        patches = grid_thw.prod(dim=1)
        ratio = max(int(patches.sum().item()) // feats.shape[0], 1)
        counts = (patches // ratio).tolist()
        chunks = torch.split(feats, counts)
        return [c.float() for c in chunks]

    @staticmethod
    def _pad_tokens(chunks):
        B = len(chunks)
        N = max(c.shape[0] for c in chunks)
        D = chunks[0].shape[1]
        out = chunks[0].new_zeros(B, N, D)
        mask = torch.ones(B, N, dtype=torch.bool, device=chunks[0].device)
        for i, c in enumerate(chunks):
            out[i, :c.shape[0]] = c
            mask[i, :c.shape[0]] = False
        return out, mask

    def forward(self, user_images, user_grid_weights, tmpl_images, smask):
        """user_images/tmpl_images: list[(H,W,3) uint8]
        user_grid_weights: (B, Smax, N_tok) float — 획별 패치 풀링 가중치
        smask: (B, Smax) bool — 실제 획."""
        dev = next(self.proj_user.parameters()).device
        ut, _ = self._pad_tokens(self._encode_images(user_images))   # (B,N,visD)
        tt, tmask = self._pad_tokens(self._encode_images(tmpl_images))
        w = user_grid_weights.to(dev)
        # 획 임베딩 = 마스크 가중 평균 (그리드 크기가 안 맞으면 자름/패딩)
        N = ut.shape[1]
        if w.shape[2] != N:
            if w.shape[2] > N:
                w = w[:, :, :N]
            else:
                w = torch.nn.functional.pad(w, (0, N - w.shape[2]))
        eu = self.proj_user(torch.bmm(w, ut))  # (B,S,d)
        mem = self.proj_tmpl(tt)               # (B,N,d)
        z = self.xattn(eu, mem, tgt_key_padding_mask=~smask.to(dev),
                       memory_key_padding_mask=tmask)
        heads = self.q_head(z)
        q = torch.sigmoid(heads[..., 0])
        rev_logit = heads[..., 1]
        ord_logit = heads[..., 2]
        keep = smask.to(dev).unsqueeze(-1).to(z.dtype)
        pooled = (z * keep).sum(1) / keep.sum(1).clamp(min=1)
        overall = torch.sigmoid(self.overall_head(pooled)).squeeze(-1)
        return dict(overall=overall, q=q, rev_logit=rev_logit,
                    ord_logit=ord_logit, smask=smask.to(dev))

    # ---------- 저장/로드 (헤드만 — 백본은 HF에서 다시 받는다) ----------
    def head_state_dict(self):
        # 헤드 전체 + 백본 중 미세조정(requires_grad)된 파라미터만 저장
        trainable = {n for n, p in self.named_parameters() if p.requires_grad}
        return {k: v for k, v in self.state_dict().items()
                if not k.startswith('visual.') or k in trainable}

    def save_heads(self, path):
        torch.save({'heads': {k: v.cpu() for k, v in self.head_state_dict().items()},
                    'model_id': self.model_id, 'size': self.size}, path)


def load_chandra_scorer(path, dtype=torch.bfloat16, device='cuda'):
    ckpt = torch.load(path, map_location='cpu', weights_only=False)
    m = ChandraScorer(model_id=ckpt['model_id'], size=ckpt['size'], dtype=dtype)
    m.load_state_dict(ckpt['heads'], strict=False)
    return m.to(device)


# ---------- 전처리 & 피드백 ----------

def strokes_to_inputs(strokes, size=448, grid=None):
    """정규화된 획 -> (uint8 이미지, 그리드 풀링 가중치)."""
    img, masks = render_time_encoded(strokes, size=size)
    u8 = (img.transpose(1, 2, 0) * 255).astype(np.uint8)
    if grid is None:
        grid = size // 16  # 실측: 448px 입력 -> 28x28 토큰 그리드
    gw = masks_to_grid(masks, grid, grid)
    return u8, gw


@torch.no_grad()
def _score_once(model, user, template):
    if hasattr(model, 'score_strokes'):
        return model.score_strokes(user, template)
    uimg, ugw = strokes_to_inputs(user, model.size)
    timg, _ = strokes_to_inputs(template, model.size)
    S = len(user)
    gw = torch.from_numpy(ugw).unsqueeze(0)
    smask = torch.ones(1, S, dtype=torch.bool)
    return model([uimg], gw, [timg], smask)


def analyze_chandra(model, template, raw_user_strokes, top_k=3, mode="trace"):
    """Chandra 채점 + 교정 제안. feedback.analyze 와 동일한 리포트 형식.
    (렌더링이 비미분이라 그래디언트 대신 반사실 분석 + 기하 이동벡터 사용)"""
    from .feedback import prepare_user_strokes, match_strokes
    from .final_report import build_final_report
    model.eval()
    user = prepare_user_strokes(raw_user_strokes)
    match, missing = match_strokes(user, template)
    out = _score_once(model, user, template)
    return build_final_report(
        output=out,
        user=user,
        template=template,
        match=match,
        missing=missing,
        score_once=lambda fixed, target: _score_once(model, fixed, target),
        top_k=top_k,
        mode=mode,
    )
