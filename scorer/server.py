# -*- coding: utf-8 -*-
"""채점 API 서버 (RunPod GPU pod 용).

POST /score    {"char": "永", "strokes": [[[x,y],...], ...]}  -> 채점 리포트
GET  /template/{char}                                          -> 정답 획 궤적
GET  /health                                                   -> 상태 확인
"""
import json
import os
from pathlib import Path
import threading
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scorer.chandra_scorer import load_chandra_scorer, analyze_chandra
from scorer.hybrid import HybridScorer, load_stroke_scorer
from scorer.kanjivg import load_char, char_to_file

KANJI_DIR = os.environ.get('KANJI_DIR', 'kanji')
CKPT = os.environ.get('CKPT', 'checkpoints/chandra_scorer.pt')
STROKE_CKPT = os.environ.get('STROKE_CKPT', 'checkpoints/stroke_scorer.pt')
HYBRID_CONFIG = os.environ.get('HYBRID_CONFIG', 'checkpoints/hybrid_config.json')
ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / 'web'

app = FastAPI(title='lingo-kanji-scorer')
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['*'], allow_headers=['*'])
app.mount('/web', StaticFiles(directory=WEB_DIR), name='web')

_model = None
_loaded_at = None
_model_kind = None
_load_error = None
_lock = threading.Lock()


def get_model():
    global _model, _loaded_at, _model_kind, _load_error
    with _lock:
        if _model is None:
            t0 = time.time()
            try:
                vision = load_chandra_scorer(CKPT)
                if os.path.exists(STROKE_CKPT):
                    weights = 0.35
                    if os.path.exists(HYBRID_CONFIG):
                        with open(HYBRID_CONFIG, encoding='utf-8') as f:
                            config = json.load(f)
                            weights = config.get('weights',
                                                 config.get('stroke_weight', weights))
                    if 'STROKE_WEIGHT' in os.environ:
                        weights = float(os.environ['STROKE_WEIGHT'])
                    stroke = load_stroke_scorer(STROKE_CKPT, device='cpu')
                    _model = HybridScorer(vision, stroke, weights).eval()
                    _model_kind = f'hybrid(weights={_model.stroke_weights})'
                else:
                    _model = vision
                    _model_kind = 'chandra-only'
                _model.eval()
                _loaded_at = time.time() - t0
                _load_error = None
            except Exception as exc:
                _load_error = f'{type(exc).__name__}: {exc}'
                raise
    return _model


@app.on_event('startup')
def _preload():
    # 서버 기동 직후 백그라운드로 모델 로드 (health 는 즉시 응답)
    threading.Thread(target=get_model, daemon=True).start()


class ScoreRequest(BaseModel):
    char: str
    strokes: list[list[tuple[float, float]]]


def _sanitize(report):
    """analyze_chandra 리포트에서 JSON 직렬화 불가 항목 정리."""
    out = dict(report)
    out['user'] = [s.tolist() for s in report['user']]
    out.pop('grad', None)
    for e in out['strokes']:
        for k in ('pos_err', 'shape_err'):
            if k in e:
                e[k] = float(e[k])
    return out


@app.get('/')
def index():
    from fastapi.responses import HTMLResponse
    return HTMLResponse((WEB_DIR / 'index.html').read_text(encoding='utf-8'))


_chars_cache = None


@app.get('/chars')
def chars():
    """kanji 디렉토리에 템플릿이 있는 문자 목록 (CJK/가나만)."""
    global _chars_cache
    if _chars_cache is None:
        import re
        cs = []
        for f in os.listdir(KANJI_DIR):
            m = re.fullmatch(r'([0-9a-f]{5})\.svg', f)
            if m:
                cp = int(m.group(1), 16)
                if cp >= 0x2E80:  # 부수/가나/한자 이상만 (라틴·기호 제외)
                    cs.append(chr(cp))
        cs.sort()
        _chars_cache = ''.join(cs)
    return dict(chars=_chars_cache, count=len(_chars_cache))


@app.get('/health')
def health():
    import torch
    ok = _model is not None and _load_error is None
    content = dict(ok=ok, model_loaded=_model is not None,
                   model_kind=_model_kind, load_error=_load_error,
                   cuda=torch.cuda.is_available(),
                   device=torch.cuda.get_device_name(0)
                   if torch.cuda.is_available() else 'cpu',
                   load_seconds=_loaded_at)
    return JSONResponse(content=content, status_code=200 if ok else 503)


@app.get('/template/{char}')
def template(char: str):
    if len(char) != 1 or not os.path.exists(char_to_file(KANJI_DIR, char)):
        raise HTTPException(404, f'문자 {char!r} 의 템플릿이 없습니다')
    strokes = load_char(KANJI_DIR, char)
    return dict(char=char, strokes=[s.tolist() for s in strokes])


@app.post('/score')
def score(req: ScoreRequest):
    ch = req.char.strip()
    if len(ch) != 1:
        raise HTTPException(400, '한 글자만 보내세요')
    if not os.path.exists(char_to_file(KANJI_DIR, ch)):
        raise HTTPException(404, f'문자 {ch!r} 의 템플릿이 없습니다')
    try:
        model = get_model()
    except Exception as exc:
        raise HTTPException(503, f'모델을 불러오지 못했습니다: {type(exc).__name__}') from exc
    max_strokes = 64
    if isinstance(model, HybridScorer):
        stroke_model = model.stroke_model
        max_strokes = min(
            stroke_model.encoder.stroke_emb.num_embeddings,
            stroke_model.encoder.pos_emb.num_embeddings // 12)
    if len(req.strokes) > max_strokes or any(len(stroke) > 4096 for stroke in req.strokes):
        raise HTTPException(400, '획 또는 좌표가 너무 많습니다')
    strokes = [np.asarray(s, dtype=np.float64) for s in req.strokes if len(s) >= 2]
    if not strokes:
        raise HTTPException(400, '획이 없습니다')
    if any(not np.isfinite(stroke).all() for stroke in strokes):
        raise HTTPException(400, '좌표는 유한한 숫자여야 합니다')
    tmpl = load_char(KANJI_DIR, ch)
    t0 = time.time()
    report = analyze_chandra(model, tmpl, strokes)
    out = _sanitize(report)
    out['char'] = ch
    out['template'] = [s.tolist() for s in tmpl]
    out['elapsed'] = round(time.time() - t0, 2)
    return out


if __name__ == '__main__':
    import uvicorn
    get_model()  # 기동 시 미리 로드
    uvicorn.run(app, host='0.0.0.0', port=8000)
