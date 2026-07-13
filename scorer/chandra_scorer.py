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


def _load_vision_tower(model_id, dtype):
    """Chandra 전체를 로드하지 않고 비전 타워만 떼어낸다."""
    import gc
    from transformers import AutoConfig, AutoModelForImageTextToText, AutoProcessor
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    full = AutoModelForImageTextToText.from_pretrained(
        model_id, torch_dtype=dtype, trust_remote_code=True,
        low_cpu_mem_usage=True)
    # qwen3vl 계열: full.model.visual (구버전 호환: full.visual)
    visual = getattr(getattr(full, 'model', full), 'visual', None)
    if visual is None:
        visual = full.visual
    cfg = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
    vis_dim = getattr(cfg.vision_config, 'out_hidden_size',
                      getattr(cfg.vision_config, 'hidden_size', None))
    del full  # LLM 부분 메모리 해제 (visual 은 참조 유지)
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
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
        return {k: v for k, v in self.state_dict().items()
                if not k.startswith('visual.')}

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
    uimg, ugw = strokes_to_inputs(user, model.size)
    timg, _ = strokes_to_inputs(template, model.size)
    S = len(user)
    gw = torch.from_numpy(ugw).unsqueeze(0)
    smask = torch.ones(1, S, dtype=torch.bool)
    return model([uimg], gw, [timg], smask)


def analyze_chandra(model, template, raw_user_strokes, top_k=3):
    """Chandra 채점 + 교정 제안. feedback.analyze 와 동일한 리포트 형식.
    (렌더링이 비미분이라 그래디언트 대신 반사실 분석 + 기하 이동벡터 사용)"""
    from .feedback import prepare_user_strokes, match_strokes
    from .synth import stroke_errors
    model.eval()
    user = prepare_user_strokes(raw_user_strokes)
    match, missing = match_strokes(user, template)
    extra = [i for i in range(len(user)) if match[i] < 0]

    out = _score_once(model, user, template)
    base = float(out['overall'][0])
    q = out['q'][0].float().cpu().numpy()
    rev_p = torch.sigmoid(out['rev_logit'][0]).float().cpu().numpy()
    ord_p = torch.sigmoid(out['ord_logit'][0]).float().cpu().numpy()

    nt = len(template)
    coverage = (nt - len(missing)) / max(nt, 1)
    score = base * coverage

    strokes_report = []
    for i in range(len(user)):
        j = int(match[i])
        entry = dict(index=i, template_index=j, q=float(q[i]),
                     rev_prob=float(rev_p[i]), ord_prob=float(ord_p[i]))
        if j >= 0:
            pe, se, looks_rev = stroke_errors(user[i], template[j])
            entry.update(pos_err=pe, shape_err=se)
            fixed = list(user)
            fixed[i] = template[j].copy()
            cf = _score_once(model, fixed, template)
            entry['gain'] = (float(cf['overall'][0]) - base) * coverage * 100
            entry['move'] = (template[j].mean(0) - user[i].mean(0)).tolist()
            msgs = []
            if entry['rev_prob'] > 0.5 or looks_rev:
                msgs.append('필순 방향이 반대입니다 — 반대쪽 끝에서 시작하세요')
            if entry['ord_prob'] > 0.5:
                msgs.append(f'획 순서 오류 — 이 획은 {j + 1}번째로 써야 합니다')
            if pe > 0.06:
                dx, dy = entry['move']
                h = '오른쪽' if dx > 0.02 else ('왼쪽' if dx < -0.02 else '')
                v = '아래' if dy > 0.02 else ('위' if dy < -0.02 else '')
                msgs.append(f'위치를 {h}{"·" if h and v else ""}{v}로 옮기세요')
            if se > 0.05:
                msgs.append('모양을 정답 궤적에 가깝게 교정하세요')
            entry['messages'] = msgs or ['잘 썼습니다']
        else:
            entry.update(gain=0.0, messages=['불필요한(대응 없는) 획입니다 — 지우세요'])
        strokes_report.append(entry)

    corrections = sorted(
        [e for e in strokes_report if e.get('gain', 0) > 0.5 or e['template_index'] < 0],
        key=lambda e: -(e.get('gain') or 0))[:top_k]
    for j in missing:
        corrections.append(dict(index=-1, template_index=j, gain=None,
                                messages=[f'{j + 1}번째 획이 빠졌습니다 — 추가하세요']))

    return dict(score=round(score * 100, 1), base_model_score=round(base * 100, 1),
                strokes=strokes_report, missing=missing, extra=extra,
                match=match.tolist(), corrections=corrections, user=user)
