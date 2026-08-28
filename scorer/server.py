# -*- coding: utf-8 -*-
"""채점 API 서버 (RunPod GPU pod 용).

POST /score    {"char": "永", "strokes": [[[x,y],...], ...]}  -> 채점 리포트
GET  /template/{char}                                          -> 정답 획 궤적
GET  /health                                                   -> 상태 확인
"""
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scorer.chandra_scorer import analyze_chandra, load_chandra_scorer
from scorer.hybrid import HybridScorer, load_stroke_scorer
from scorer.kanjivg import char_to_file, load_char
from scorer.realtime import FastCoachEngine, InvalidStroke, TemplateUnavailable
from scorer.schemas import (
    ApiErrorCode,
    AttemptEvent,
    CoachStrokeRequest,
    CoachStrokeResponse,
)
from scorer.teacher_renderer import TeacherRenderer, deterministic_fallback
from scorer.teacher_schemas import TeacherFeedbackEnvelope, TeacherFeedbackRequest

KANJI_DIR = os.environ.get('KANJI_DIR', 'kanji')
CKPT = os.environ.get('CKPT', 'checkpoints/chandra_scorer.pt')
STROKE_CKPT = os.environ.get('STROKE_CKPT', 'checkpoints/stroke_scorer.pt')
HYBRID_CONFIG = os.environ.get('HYBRID_CONFIG', 'checkpoints/hybrid_config.json')
ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / 'web'
LOGGER = logging.getLogger(__name__)
PROTOCOL_VERSION = 1
BUILD_SHA = os.environ.get('BUILD_SHA', 'unknown').strip() or 'unknown'
COACH_ENGINE_MODE = os.environ.get('COACH_ENGINE', 'auto').strip().lower()
if COACH_ENGINE_MODE not in {'auto', 'geometry-only'}:
    LOGGER.warning('unknown COACH_ENGINE=%r; using auto', COACH_ENGINE_MODE)
    COACH_ENGINE_MODE = 'auto'

DEFAULT_TEACHER_MAX_CONCURRENCY = 4
MAX_TEACHER_MAX_CONCURRENCY = 32
DEFAULT_TEACHER_RATE_LIMIT_PER_MINUTE = 60
MAX_TEACHER_RATE_LIMIT_PER_MINUTE = 600
DEFAULT_TEACHER_DAILY_REQUEST_LIMIT = 1000
MAX_TEACHER_DAILY_REQUEST_LIMIT = 100000
MAX_TEACHER_CLIENT_BUCKETS = 4096


def _configured_service_mode():
    value = os.environ.get('LINGO_SERVICE_MODE', 'full').strip().lower()
    return value if value in {'full', 'teacher-only'} else 'full'


def _configured_teacher_concurrency():
    try:
        value = int(os.environ.get(
            'TEACHER_MAX_CONCURRENCY',
            DEFAULT_TEACHER_MAX_CONCURRENCY,
        ))
    except (TypeError, ValueError):
        return DEFAULT_TEACHER_MAX_CONCURRENCY
    if not 1 <= value <= MAX_TEACHER_MAX_CONCURRENCY:
        return DEFAULT_TEACHER_MAX_CONCURRENCY
    return value


def _configured_positive_int(name, default, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    if not 1 <= value <= maximum:
        return default
    return value


def _configured_teacher_rate_limit():
    return _configured_positive_int(
        'TEACHER_RATE_LIMIT_PER_MINUTE',
        DEFAULT_TEACHER_RATE_LIMIT_PER_MINUTE,
        MAX_TEACHER_RATE_LIMIT_PER_MINUTE,
    )


def _configured_teacher_daily_limit():
    return _configured_positive_int(
        'TEACHER_DAILY_REQUEST_LIMIT',
        DEFAULT_TEACHER_DAILY_REQUEST_LIMIT,
        MAX_TEACHER_DAILY_REQUEST_LIMIT,
    )


class TeacherRequestBudget:
    """Bound public provider traffic without retaining raw client addresses."""

    def __init__(
        self,
        per_minute,
        daily_limit,
        *,
        monotonic_clock=time.monotonic,
        utc_day_clock=None,
    ):
        self.per_minute = per_minute
        self.daily_limit = daily_limit
        self._monotonic_clock = monotonic_clock
        self._utc_day_clock = utc_day_clock or (
            lambda: datetime.now(timezone.utc).date().isoformat()
        )
        self._lock = threading.Lock()
        self._client_windows = OrderedDict()
        self._day = self._utc_day_clock()
        self._daily_count = 0

    def consume(self, client_key):
        with self._lock:
            day = self._utc_day_clock()
            if day != self._day:
                self._day = day
                self._daily_count = 0
                self._client_windows.clear()

            if self._daily_count >= self.daily_limit:
                return 'daily_budget_exceeded'

            now = self._monotonic_clock()
            cutoff = now - 60.0
            window = self._client_windows.get(client_key)
            if window is None:
                window = deque()
                self._client_windows[client_key] = window
            else:
                self._client_windows.move_to_end(client_key)
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self.per_minute:
                return 'rate_limited'

            window.append(now)
            self._daily_count += 1
            while len(self._client_windows) > MAX_TEACHER_CLIENT_BUCKETS:
                self._client_windows.popitem(last=False)
            return None

_model = None
_loaded_at = None
_model_kind = None
_load_error = None
_deep_loading = False
_deep_lock = threading.Lock()

_stroke_model = None
_stroke_load_attempted = False
_stroke_load_error = None
_stroke_lock = threading.Lock()

_coach_engine = None
_coach_loaded_at = None
_coach_load_error = None
_coach_lock = threading.Lock()

_teacher_renderer = None
_teacher_lock = threading.Lock()
_teacher_slots = threading.BoundedSemaphore(_configured_teacher_concurrency())
_teacher_request_budget = TeacherRequestBudget(
    _configured_teacher_rate_limit(),
    _configured_teacher_daily_limit(),
)
_teacher_client_salt = os.urandom(16)
_attempt_log_lock = threading.Lock()


def _attempt_log_path():
    configured = os.environ.get('ATTEMPT_LOG_PATH', '').strip()
    return Path(configured) if configured else ROOT_DIR / 'artifacts' / 'attempt-events.jsonl'


def record_attempt_event(event: AttemptEvent):
    """Append an anonymous attempt atomically enough for the single server process."""
    path = _attempt_log_path()
    payload = event.model_dump_json(exclude_none=False)
    with _attempt_log_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8', newline='\n') as log:
            log.write(payload)
            log.write('\n')


def get_stroke_model():
    """Load the small coordinate model once; failure keeps coaching available."""
    global _stroke_model, _stroke_load_attempted, _stroke_load_error
    with _stroke_lock:
        if _stroke_load_attempted:
            return _stroke_model
        _stroke_load_attempted = True
        if not os.path.exists(STROKE_CKPT):
            _stroke_load_error = 'checkpoint-not-found'
            return None
        try:
            _stroke_model = load_stroke_scorer(STROKE_CKPT, device='cpu')
            _stroke_load_error = None
        except Exception as exc:
            _stroke_load_error = type(exc).__name__
            LOGGER.exception('lightweight stroke model failed to load')
    return _stroke_model


def get_coach_engine():
    """Return the cached realtime engine without touching the vision model."""
    global _coach_engine, _coach_loaded_at, _coach_load_error
    with _coach_lock:
        if _coach_engine is None:
            started = time.perf_counter()
            try:
                stroke_model = (
                    None if COACH_ENGINE_MODE == 'geometry-only'
                    else get_stroke_model()
                )
                _coach_engine = FastCoachEngine(
                    lambda char: load_char(KANJI_DIR, char),
                    stroke_model=stroke_model,
                )
                _coach_loaded_at = time.perf_counter() - started
                _coach_load_error = None
            except Exception as exc:
                _coach_load_error = type(exc).__name__
                raise
    return _coach_engine


def get_teacher_renderer():
    """Return the lazy language renderer without loading either scoring model."""
    global _teacher_renderer
    with _teacher_lock:
        if _teacher_renderer is None:
            _teacher_renderer = TeacherRenderer()
    return _teacher_renderer


def require_teacher_api_token(
    x_lingo_teacher_token: str | None = Header(
        default=None,
        alias='X-Lingo-Teacher-Token',
    ),
):
    """Require the private Edge-to-scorer token only when configured."""
    expected = os.environ.get('TEACHER_API_TOKEN')
    if not expected:
        return
    supplied = x_lingo_teacher_token or ''
    matches = hmac.compare_digest(
        supplied.encode('utf-8'),
        expected.encode('utf-8'),
    )
    if matches:
        return
    if x_lingo_teacher_token is None:
        raise HTTPException(
            status_code=401,
            detail={
                'code': 'TEACHER_TOKEN_REQUIRED',
                'message': '교사 API 인증 토큰이 필요합니다',
            },
        )
    raise HTTPException(
        status_code=403,
        detail={
            'code': 'TEACHER_TOKEN_INVALID',
            'message': '교사 API 인증 토큰이 올바르지 않습니다',
        },
    )


def _teacher_client_key(request):
    forwarded = request.headers.get('x-forwarded-for', '')
    address = forwarded.split(',', 1)[0].strip()
    if not address and request.client is not None:
        address = request.client.host
    if not address:
        address = 'unknown'
    return hashlib.sha256(
        _teacher_client_salt + address.encode('utf-8', errors='replace')
    ).hexdigest()


def _teacher_fallback_envelope(req, purpose, reason, started):
    return TeacherFeedbackEnvelope(
        feedback=deterministic_fallback(req, purpose=purpose),
        source='fallback',
        model=None,
        fallback_reason=reason,
        latency_ms=(time.perf_counter() - started) * 1000,
        cached=False,
        usage=None,
    )


def _render_teacher(req, purpose, request=None):
    """Fail fast when all provider slots are occupied; never queue scoring."""
    started = time.perf_counter()
    if request is not None:
        limit_reason = _teacher_request_budget.consume(
            _teacher_client_key(request)
        )
        if limit_reason is not None:
            return _teacher_fallback_envelope(
                req,
                purpose,
                limit_reason,
                started,
            )
    if not _teacher_slots.acquire(blocking=False):
        return _teacher_fallback_envelope(
            req,
            purpose,
            'capacity_exceeded',
            started,
        )
    try:
        return get_teacher_renderer().render(req, purpose=purpose)
    except Exception as exc:
        LOGGER.warning(
            'teacher feedback rendering failed (%s)',
            type(exc).__name__,
        )
        return _teacher_fallback_envelope(
            req,
            purpose,
            'api_error',
            started,
        )
    finally:
        _teacher_slots.release()


def _configured_stroke_weight():
    weights = 0.35
    if os.path.exists(HYBRID_CONFIG):
        with open(HYBRID_CONFIG, encoding='utf-8') as file:
            config = json.load(file)
        weights = config.get('weights', config.get('stroke_weight', weights))
    if 'STROKE_WEIGHT' in os.environ:
        weights = float(os.environ['STROKE_WEIGHT'])
    return weights


def get_model():
    """Load the slower deep scorer used only by the final /score endpoint."""
    global _model, _loaded_at, _model_kind, _load_error, _deep_loading
    with _deep_lock:
        if _model is not None:
            return _model
        _deep_loading = True
        started = time.perf_counter()
        try:
            vision = load_chandra_scorer(CKPT)
            stroke = get_stroke_model()
            if stroke is not None:
                _model = HybridScorer(
                    vision, stroke, _configured_stroke_weight()
                ).eval()
                _model_kind = f'hybrid(weights={_model.stroke_weights})'
            else:
                _model = vision.eval()
                _model_kind = 'chandra-only'
            _loaded_at = time.perf_counter() - started
            _load_error = None
        except Exception as exc:
            _load_error = type(exc).__name__
            LOGGER.exception('deep scorer failed to load')
            raise
        finally:
            _deep_loading = False
    return _model


def _warm(loader, label):
    try:
        loader()
    except Exception:
        LOGGER.exception('%s preload failed', label)


@asynccontextmanager
async def lifespan(_app):
    if _configured_service_mode() == 'full':
        # The realtime path and deep final scorer warm independently.
        threading.Thread(
            target=_warm, args=(get_coach_engine, 'coach'), daemon=True
        ).start()
        threading.Thread(
            target=_warm, args=(get_model, 'deep scorer'), daemon=True
        ).start()
    yield


app = FastAPI(title='lingo-kanji-scorer', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['*'], allow_headers=['*'])
app.mount('/web', StaticFiles(directory=WEB_DIR, html=True), name='web')


@app.exception_handler(RequestValidationError)
async def request_validation_error(_request, exc):
    # Avoid echoing non-finite or otherwise sensitive raw inputs in 422 JSON.
    details = [
        {key: value for key, value in error.items() if key not in {'input', 'ctx'}}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            'detail': {
                'code': ApiErrorCode.INVALID_REQUEST.value,
                'message': '요청 형식이 올바르지 않습니다',
                'errors': details,
            }
        },
    )


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
    service_mode = _configured_service_mode()
    teacher_ready = True  # The deterministic fallback is always available.
    coach_ready = _coach_engine is not None
    deep_ready = _model is not None and _load_error is None
    healthy = teacher_ready if service_mode == 'teacher-only' else coach_ready
    content = dict(
        ok=healthy,
        protocol_version=PROTOCOL_VERSION,
        build_sha=BUILD_SHA,
        service_mode=service_mode,
        teacher_ready=teacher_ready,
        teacher_rate_limit_per_minute=_teacher_request_budget.per_minute,
        teacher_daily_request_limit=_teacher_request_budget.daily_limit,
        coach_ready=coach_ready,
        coach_engine=_coach_engine.mode if coach_ready else None,
        coach_load_error=_coach_load_error,
        coach_load_seconds=_coach_loaded_at,
        stroke_model_error=_stroke_load_error,
        deep_score_ready=deep_ready,
        deep_model_loading=_deep_loading,
        # Legacy health fields remain available for existing clients.
        model_loaded=_model is not None,
        model_kind=_model_kind,
        load_error=_load_error,
        cuda=torch.cuda.is_available(),
        device=(
            torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'
        ),
        load_seconds=_loaded_at,
    )
    return JSONResponse(content=content, status_code=200 if healthy else 503)


@app.get('/template/{char}')
def template(char: str):
    if len(char) != 1 or not os.path.exists(char_to_file(KANJI_DIR, char)):
        raise HTTPException(404, f'문자 {char!r} 의 템플릿이 없습니다')
    strokes = load_char(KANJI_DIR, char)
    return dict(char=char, strokes=[s.tolist() for s in strokes])


@app.post('/coach/stroke', response_model=CoachStrokeResponse)
def coach_stroke(req: CoachStrokeRequest):
    try:
        return get_coach_engine().coach(req)
    except TemplateUnavailable as exc:
        raise HTTPException(404, {
            'code': ApiErrorCode.TEMPLATE_UNAVAILABLE.value,
            'message': '문자 템플릿을 찾지 못했습니다',
        }) from exc
    except InvalidStroke as exc:
        raise HTTPException(400, {
            'code': ApiErrorCode.INVALID_STROKE.value,
            'message': str(exc),
        }) from exc
    except Exception as exc:
        LOGGER.exception('realtime stroke coaching failed')
        raise HTTPException(500, {
            'code': ApiErrorCode.COACH_FAILED.value,
            'message': '획을 분석하지 못했습니다',
        }) from exc


@app.post('/attempt/events', status_code=202)
def attempt_events(req: AttemptEvent):
    """Persist one end-of-attempt batch without entering either scoring path."""
    try:
        record_attempt_event(req)
    except OSError as exc:
        LOGGER.exception('attempt event persistence failed')
        raise HTTPException(503, {
            'code': 'ATTEMPT_STORE_UNAVAILABLE',
            'message': '필기 기록을 저장하지 못했습니다',
        }) from exc
    return {
        'stored': True,
        'attempt_id': req.attempt_id,
        'attempt_revision': req.attempt_revision,
    }


@app.post('/coach/verbalize', response_model=TeacherFeedbackEnvelope)
def coach_verbalize(
    req: TeacherFeedbackRequest,
    request: Request,
    _authorization: None = Depends(require_teacher_api_token),
):
    """Render locked evidence; provider failures return deterministic feedback."""
    return _render_teacher(req, purpose='verbalize', request=request)


@app.post('/coach/summary', response_model=TeacherFeedbackEnvelope)
def coach_summary(
    req: TeacherFeedbackRequest,
    request: Request,
    _authorization: None = Depends(require_teacher_api_token),
):
    """Render a completion summary without entering either scoring hot path."""
    return _render_teacher(req, purpose='summary', request=request)


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
    get_coach_engine()
    uvicorn.run(app, host='0.0.0.0', port=8000)
