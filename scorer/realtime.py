"""Low-latency geometry and optional stroke-model coaching.

This module is intentionally independent from the Chandra vision path. A
request performs at most one lightweight ``Scorer`` forward pass and falls
back to deterministic geometry when the checkpoint is absent or fails.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import torch

from .data import POINTS_PER_STROKE
from .feedback import _to_batch
from .kanjivg import resample_stroke
from .schemas import (
    Anchor,
    CoachMetrics,
    CoachOverlay,
    CoachStrokeRequest,
    CoachStrokeResponse,
    CurvatureHotspot,
    NextAction,
    PrimaryCue,
    RichPoint,
    Vector,
)

LOGGER = logging.getLogger(__name__)
SAMPLE_COUNT = 28
MAX_POINTS = 4096
BOUNDS_TOLERANCE = 0.25
MIN_STEP = 0.00025
EPSILON = 1e-9


class InvalidStroke(ValueError):
    """Raised when point input is unsafe or unusable."""


class TemplateUnavailable(LookupError):
    """Raised when no realtime template exists for a character."""


@dataclass(frozen=True)
class DtwResult:
    distance: float
    path: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class GeometryMetrics:
    start_error: float
    end_error: float
    path_error: float
    shape_error: float
    direction_cosine: float
    length_ratio: float
    bbox_shift: tuple[float, float]
    scale_ratio: float
    curvature_difference: float
    curvature_user: tuple[float, float]
    curvature_target: tuple[float, float]
    reverse_path_error: float
    reverse_advantage: float
    looks_reversed: bool
    start_vector: tuple[float, float]
    end_vector: tuple[float, float]
    problem_segment: tuple[tuple[float, float], ...]
    target_segment: tuple[tuple[float, float], ...]
    aligned_user: np.ndarray
    aligned_template: np.ndarray


@dataclass(frozen=True)
class CausalMatch:
    matched_template_index: int | None
    expected_template_index: int
    metrics: GeometryMetrics | None
    match_confidence: float
    wrong_order: bool = False
    extra_stroke: bool = False


@dataclass(frozen=True)
class ModelEvidence:
    quality: float
    reverse_probability: float
    order_probability: float


@dataclass(frozen=True)
class CueCandidate:
    cue: PrimaryCue
    priority: float
    major: bool


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, float(value)))


def _point_xy(point) -> tuple[float, float]:
    if isinstance(point, RichPoint):
        x, y = point.x, point.y
    elif isinstance(point, dict):
        x, y = point.get("x"), point.get("y")
    else:
        try:
            x, y = point[0], point[1]
        except (IndexError, KeyError, TypeError) as exc:
            raise InvalidStroke("point must contain x and y") from exc
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError) as exc:
        raise InvalidStroke("point coordinates must be numbers") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise InvalidStroke("point coordinates must be finite")
    if (
        x < -BOUNDS_TOLERANCE
        or x > 1 + BOUNDS_TOLERANCE
        or y < -BOUNDS_TOLERANCE
        or y > 1 + BOUNDS_TOLERANCE
    ):
        raise InvalidStroke("point is outside the supported canvas bounds")
    return _clamp(x), _clamp(y)


def sanitize_points(points: Sequence, *, max_points: int = MAX_POINTS) -> np.ndarray:
    """Convert legacy/rich points to finite deduplicated canvas coordinates."""
    if isinstance(points, (str, bytes)):
        raise InvalidStroke("stroke must be a point sequence")
    try:
        point_count = len(points)
    except TypeError as exc:
        raise InvalidStroke("stroke must be a point sequence") from exc
    if point_count == 0:
        raise InvalidStroke("stroke must contain at least one point")
    if point_count > max_points:
        raise InvalidStroke(f"stroke exceeds the {max_points} point limit")
    clean: list[tuple[float, float]] = []
    for point in points:
        xy = _point_xy(point)
        if not clean or math.dist(clean[-1], xy) >= MIN_STEP:
            clean.append(xy)
    return np.asarray(clean, dtype=np.float64)


def _arc_length(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def banded_dtw(first: np.ndarray, second: np.ndarray, band_ratio: float = 0.3) -> DtwResult:
    rows, columns = len(first), len(second)
    if rows == 0 or columns == 0:
        return DtwResult(math.inf, ())
    band = max(abs(rows - columns), math.ceil(max(rows, columns) * band_ratio))
    costs = np.full((rows + 1, columns + 1), np.inf, dtype=np.float64)
    previous = np.zeros((rows + 1, columns + 1), dtype=np.uint8)
    costs[0, 0] = 0.0
    for row in range(1, rows + 1):
        start = max(1, row - band)
        end = min(columns, row + band)
        for column in range(start, end + 1):
            local = float(np.linalg.norm(first[row - 1] - second[column - 1]))
            options = (
                costs[row - 1, column - 1],
                costs[row - 1, column],
                costs[row, column - 1],
            )
            choice = int(np.argmin(options))
            costs[row, column] = local + options[choice]
            previous[row, column] = choice + 1
    if not np.isfinite(costs[rows, columns]):
        return DtwResult(math.inf, ())
    path: list[tuple[int, int]] = []
    row, column = rows, columns
    while row > 0 and column > 0:
        path.append((row - 1, column - 1))
        step = previous[row, column]
        if step == 1:
            row -= 1
            column -= 1
        elif step == 2:
            row -= 1
        elif step == 3:
            column -= 1
        else:
            break
    path.reverse()
    return DtwResult(float(costs[rows, columns] / max(len(path), 1)), tuple(path))


def _aligned_mean_distance(
    first: np.ndarray, second: np.ndarray, path: Iterable[tuple[int, int]]
) -> float:
    pairs = tuple(path)
    if not pairs:
        return math.inf
    return float(np.mean([np.linalg.norm(first[i] - second[j]) for i, j in pairs]))


def _direction_cosine(user: np.ndarray, template: np.ndarray) -> float:
    user_vector = user[-1] - user[0]
    template_vector = template[-1] - template[0]
    denominator = float(np.linalg.norm(user_vector) * np.linalg.norm(template_vector))
    if denominator < EPSILON:
        return 0.0
    return _clamp(float(np.dot(user_vector, template_vector) / denominator), -1.0, 1.0)


def _bounding_box(points: np.ndarray) -> tuple[np.ndarray, float]:
    low, high = points.min(axis=0), points.max(axis=0)
    return (low + high) / 2, float(np.linalg.norm(high - low))


def _curvature(points: np.ndarray) -> np.ndarray:
    values = np.zeros(len(points), dtype=np.float64)
    for index in range(1, len(points) - 1):
        first = points[index] - points[index - 1]
        second = points[index + 1] - points[index]
        cross = first[0] * second[1] - first[1] * second[0]
        dot = float(np.dot(first, second))
        values[index] = math.atan2(float(cross), dot)
    return values


def compute_stroke_metrics(user_points: Sequence, template_points: Sequence) -> GeometryMetrics:
    raw_user = sanitize_points(user_points)
    raw_template = sanitize_points(template_points)
    user = resample_stroke(raw_user, SAMPLE_COUNT)
    template = resample_stroke(raw_template, SAMPLE_COUNT)
    forward = banded_dtw(user, template)
    reversed_alignment = banded_dtw(user[::-1].copy(), template)

    user_center = user.mean(axis=0)
    template_center = template.mean(axis=0)
    centered_user = user - user_center
    centered_template = template - template_center
    shape_alignment = banded_dtw(centered_user, centered_template)
    path_error = _aligned_mean_distance(user, template, forward.path)
    shape_error = _aligned_mean_distance(
        centered_user, centered_template, shape_alignment.path
    )
    direction = _direction_cosine(user, template)
    user_box_center, user_diagonal = _bounding_box(user)
    template_box_center, template_diagonal = _bounding_box(template)
    user_curvature = _curvature(user)
    template_curvature = _curvature(template)

    curvature_difference = -1.0
    curvature_pair = (0, 0)
    maximum_error = -1.0
    maximum_path_index = 0
    for path_index, (user_index, template_index) in enumerate(forward.path):
        difference = abs(user_curvature[user_index] - template_curvature[template_index])
        if difference > curvature_difference:
            curvature_difference = float(difference)
            curvature_pair = (user_index, template_index)
        aligned_error = float(np.linalg.norm(user[user_index] - template[template_index]))
        if aligned_error > maximum_error:
            maximum_error = aligned_error
            maximum_path_index = path_index

    segment_start = max(0, maximum_path_index - 2)
    segment_end = min(len(forward.path), maximum_path_index + 3)
    problem_path = forward.path[segment_start:segment_end]
    reverse_advantage = _clamp(
        (path_error - reversed_alignment.distance) / max(path_error, 0.02), -1.0, 1.0
    )
    user_index, template_index = curvature_pair
    bbox_vector = template_box_center - user_box_center
    start_vector = template[0] - user[0]
    end_vector = template[-1] - user[-1]

    return GeometryMetrics(
        start_error=float(np.linalg.norm(user[0] - template[0])),
        end_error=float(np.linalg.norm(user[-1] - template[-1])),
        path_error=path_error,
        shape_error=shape_error,
        direction_cosine=direction,
        length_ratio=_arc_length(raw_user) / max(_arc_length(raw_template), EPSILON),
        bbox_shift=(float(bbox_vector[0]), float(bbox_vector[1])),
        scale_ratio=user_diagonal / max(template_diagonal, EPSILON),
        curvature_difference=max(curvature_difference, 0.0),
        curvature_user=(float(user[user_index, 0]), float(user[user_index, 1])),
        curvature_target=(float(template[template_index, 0]), float(template[template_index, 1])),
        reverse_path_error=reversed_alignment.distance,
        reverse_advantage=reverse_advantage,
        looks_reversed=bool(direction < -0.35 and reversed_alignment.distance + 0.012 < path_error),
        start_vector=(float(start_vector[0]), float(start_vector[1])),
        end_vector=(float(end_vector[0]), float(end_vector[1])),
        problem_segment=tuple(
            (float(user[i, 0]), float(user[i, 1])) for i, _ in problem_path
        ),
        target_segment=tuple(
            (float(template[j, 0]), float(template[j, 1])) for _, j in problem_path
        ),
        aligned_user=user,
        aligned_template=template,
    )


def _match_cost(metrics: GeometryMetrics) -> float:
    return (
        metrics.path_error
        + 0.25 * metrics.start_error
        + 0.10 * metrics.end_error
        + 0.035 * (1 - metrics.direction_cosine)
    )


def causal_match(
    current_stroke: Sequence,
    template_strokes: Sequence[Sequence],
    expected_index: int,
) -> CausalMatch:
    if expected_index >= len(template_strokes):
        return CausalMatch(None, expected_index, None, 0.98, extra_stroke=True)
    expected_metrics = compute_stroke_metrics(current_stroke, template_strokes[expected_index])
    matched_index = expected_index
    metrics = expected_metrics
    wrong_order = False
    expected_cost = _match_cost(expected_metrics)
    if expected_index + 1 < len(template_strokes):
        next_metrics = compute_stroke_metrics(current_stroke, template_strokes[expected_index + 1])
        next_cost = _match_cost(next_metrics)
        if next_cost + 0.025 < expected_cost * 0.72 and next_metrics.start_error < 0.12:
            matched_index = expected_index + 1
            metrics = next_metrics
            wrong_order = True
    confidence = _clamp(1 - _match_cost(metrics) / 0.22)
    return CausalMatch(
        matched_index,
        expected_index,
        metrics,
        confidence,
        wrong_order=wrong_order,
    )


def _confidence(value: float, nudge: float, retry: float) -> float:
    return _clamp(0.55 + 0.44 * (value - nudge) / max(retry - nudge, EPSILON))


def _direction_text(vector: tuple[float, float]) -> str:
    dx, dy = vector
    horizontal = "오른쪽" if dx > 0.018 else "왼쪽" if dx < -0.018 else ""
    vertical = "아래" if dy > 0.018 else "위" if dy < -0.018 else ""
    return "·".join(filter(None, (horizontal, vertical))) or "표시된 방향"


def _anchor(point: Sequence[float] | None) -> Anchor | None:
    if point is None:
        return None
    return Anchor(x=float(point[0]), y=float(point[1]))


def _vector(value: tuple[float, float] | None) -> Vector | None:
    if value is None:
        return None
    return Vector(dx=float(value[0]), dy=float(value[1]))


def _candidate(
    code: str,
    text: str,
    confidence: float,
    priority: float,
    major: bool,
    *,
    anchor: Sequence[float] | None = None,
    vector: tuple[float, float] | None = None,
) -> CueCandidate:
    return CueCandidate(
        cue=PrimaryCue(
            code=code,
            text=text,
            confidence=_clamp(confidence),
            anchor=_anchor(anchor),
            vector=_vector(vector),
        ),
        priority=priority,
        major=major,
    )


def _select_cue(
    match: CausalMatch,
    evidence: ModelEvidence | None,
) -> CueCandidate | None:
    metrics = match.metrics
    if match.extra_stroke:
        return _candidate(
            "EXTRA_STROKE",
            "예상 획을 모두 썼습니다. 이 획은 지우고 채점을 확인해 보세요.",
            0.98,
            110,
            True,
        )
    if match.wrong_order:
        return _candidate(
            "WRONG_ORDER",
            f"{match.expected_template_index + 1}번 획을 먼저 써 보세요.",
            match.match_confidence,
            105,
            True,
            anchor=metrics.aligned_user[0],
        )
    if metrics is None:
        return None

    candidates: list[CueCandidate] = []
    position_only = (
        metrics.shape_error <= 0.035
        and metrics.direction_cosine >= 0.75
        and 0.78 <= metrics.length_ratio <= 1.28
    )
    reverse_probability = evidence.reverse_probability if evidence else None
    order_probability = evidence.order_probability if evidence else None
    if order_probability is not None and order_probability >= 0.8:
        order_confidence = order_probability
        if not match.wrong_order:
            order_confidence = min(order_confidence, 0.78)
        candidates.append(
            _candidate(
                "WRONG_ORDER",
                f"{match.expected_template_index + 1}번 획의 순서를 다시 확인해 보세요.",
                order_confidence,
                105,
                True,
                anchor=metrics.aligned_user[0],
            )
        )
    if metrics.looks_reversed or metrics.direction_cosine < -0.45 or (
        reverse_probability is not None and reverse_probability >= 0.8
    ):
        confidence = max(
            0.86 if metrics.looks_reversed else 0.0,
            _clamp(-metrics.direction_cosine),
            reverse_probability or 0.0,
        )
        if reverse_probability is not None and reverse_probability < 0.25 and metrics.looks_reversed:
            confidence *= 0.65
        if (
            reverse_probability is not None
            and reverse_probability >= 0.8
            and not metrics.looks_reversed
            and metrics.direction_cosine > 0.65
        ):
            confidence = min(confidence, 0.78)
        candidates.append(
            _candidate(
                "DIRECTION_REVERSED",
                "획의 방향이 반대예요. 반대쪽 끝에서 시작해 다시 써 보세요.",
                confidence,
                100,
                True,
                anchor=metrics.aligned_user[0],
                vector=metrics.start_vector,
            )
        )
    if metrics.start_error > 0.045:
        candidates.append(
            _candidate(
                "START_OFFSET",
                f"시작점을 {_direction_text(metrics.start_vector)}으로 옮겨 보세요.",
                _confidence(metrics.start_error, 0.045, 0.16),
                95,
                metrics.start_error >= 0.16 and not position_only,
                anchor=metrics.aligned_user[0],
                vector=metrics.start_vector,
            )
        )
    if metrics.path_error > 0.048 and not position_only:
        midpoint = metrics.problem_segment[len(metrics.problem_segment) // 2]
        candidates.append(
            _candidate(
                "PATH_DEVIATION",
                "강조된 구간을 점선 가이드 쪽으로 붙여 보세요.",
                _confidence(metrics.path_error, 0.048, 0.14),
                85,
                metrics.path_error >= 0.14,
                anchor=midpoint,
            )
        )
    if metrics.end_error > 0.055:
        candidates.append(
            _candidate(
                "END_OFFSET",
                f"끝점을 {_direction_text(metrics.end_vector)}으로 마무리해 보세요.",
                _confidence(metrics.end_error, 0.055, 0.18),
                60,
                metrics.end_error >= 0.18 and not position_only,
                anchor=metrics.aligned_user[-1],
                vector=metrics.end_vector,
            )
        )
    if metrics.length_ratio < 0.72:
        candidates.append(
            _candidate(
                "TOO_SHORT",
                "획이 짧아요. 가이드의 끝점까지 조금 더 이어 보세요.",
                _confidence(0.72 - metrics.length_ratio, 0.0, 0.24),
                52,
                metrics.length_ratio <= 0.48,
                anchor=metrics.aligned_user[-1],
                vector=metrics.end_vector,
            )
        )
    if metrics.length_ratio > 1.34:
        candidates.append(
            _candidate(
                "TOO_LONG",
                "획이 길어요. 표시된 끝점에서 조금 일찍 멈춰 보세요.",
                _confidence(metrics.length_ratio - 1.34, 0.0, 0.38),
                52,
                metrics.length_ratio >= 1.72,
                anchor=metrics.aligned_user[-1],
                vector=metrics.end_vector,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.priority * candidate.cue.confidence)


def _move_batch(batch, device: torch.device):
    return tuple(value.to(device) for value in batch)


class FastCoachEngine:
    """Causal realtime coach with an optional one-forward stroke model."""

    def __init__(
        self,
        template_loader: Callable[[str], Sequence[Sequence]],
        stroke_model=None,
    ) -> None:
        self.template_loader = template_loader
        self.stroke_model = stroke_model
        self._template_cache: dict[str, tuple[np.ndarray, ...]] = {}
        self._template_batch_cache: dict[str, tuple[torch.Tensor, ...]] = {}
        self._cache_lock = threading.Lock()
        self._model_lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "geometry+stroke-model" if self.stroke_model is not None else "geometry-only"

    def _template(self, char: str) -> tuple[np.ndarray, ...]:
        with self._cache_lock:
            cached = self._template_cache.get(char)
        if cached is not None:
            return cached
        try:
            loaded = self.template_loader(char)
        except Exception as exc:
            raise TemplateUnavailable(f"template unavailable for {char!r}") from exc
        if not loaded:
            raise TemplateUnavailable(f"template unavailable for {char!r}")
        template = tuple(sanitize_points(stroke) for stroke in loaded)
        with self._cache_lock:
            self._template_cache[char] = template
        return template

    def _template_batch(self, char: str, template: Sequence[np.ndarray]):
        with self._cache_lock:
            batch = self._template_batch_cache.get(char)
        if batch is None:
            sampled = [resample_stroke(stroke, POINTS_PER_STROKE) for stroke in template]
            batch = _to_batch(sampled)
            with self._cache_lock:
                self._template_batch_cache[char] = batch
        return batch

    def _model_evidence(
        self,
        request: CoachStrokeRequest,
        template: Sequence[np.ndarray],
        accepted: Sequence[np.ndarray],
        current: np.ndarray,
    ) -> ModelEvidence | None:
        model = self.stroke_model
        if model is None:
            return None
        try:
            user = list(accepted)
            user.append(current)
            sampled_user = [resample_stroke(stroke, POINTS_PER_STROKE) for stroke in user]
            if hasattr(model, "encoder"):
                max_strokes = model.encoder.stroke_emb.num_embeddings
                max_length = model.encoder.pos_emb.num_embeddings
                if (
                    len(sampled_user) > max_strokes
                    or len(template) > max_strokes
                    or len(sampled_user) * POINTS_PER_STROKE > max_length
                    or len(template) * POINTS_PER_STROKE > max_length
                ):
                    return None
            try:
                device = next(model.parameters()).device
            except StopIteration:
                device = torch.device("cpu")
            user_batch = _move_batch(_to_batch(sampled_user), device)
            template_batch = _move_batch(self._template_batch(request.char, template), device)
            with self._model_lock, torch.no_grad():
                output = model(user_batch, template_batch)
            index = len(sampled_user) - 1
            return ModelEvidence(
                quality=float(output["q"][0, index].detach().cpu()),
                reverse_probability=float(
                    torch.sigmoid(output["rev_logit"][0, index]).detach().cpu()
                ),
                order_probability=float(
                    torch.sigmoid(output["ord_logit"][0, index]).detach().cpu()
                ),
            )
        except Exception:
            LOGGER.exception("lightweight stroke model failed; using geometry-only fallback")
            self.stroke_model = None
            return None

    def coach(self, request: CoachStrokeRequest) -> CoachStrokeResponse:
        started = time.perf_counter()
        template = self._template(request.char)
        accepted = [sanitize_points(stroke) for stroke in request.accepted_strokes]
        if len(accepted) != request.expected_template_index:
            raise InvalidStroke("accepted stroke prefix does not match expected index")
        current = sanitize_points(request.current_stroke)
        match = causal_match(current, template, request.expected_template_index)
        evidence = self._model_evidence(request, template, accepted, current)
        cue_candidate = _select_cue(match, evidence)
        cue = cue_candidate.cue if cue_candidate else None
        accepted = not (
            cue_candidate
            and cue_candidate.major
            and cue_candidate.cue.confidence >= 0.82
        )
        severity = "none" if cue is None else "major" if not accepted else "minor"
        intervention = (
            "silent" if cue is None else "pause_and_retry" if not accepted else "nudge"
        )
        next_index = (
            request.expected_template_index + 1 if accepted and not match.extra_stroke
            else request.expected_template_index
        )
        next_start = template[next_index][0] if next_index < len(template) else None
        metrics = match.metrics
        if metrics is None:
            # Extra strokes still receive a complete, finite metric contract.
            metrics = compute_stroke_metrics(current, template[-1])
        response_metrics = CoachMetrics(
            start_error=metrics.start_error,
            end_error=metrics.end_error,
            path_error=metrics.path_error,
            shape_error=metrics.shape_error,
            direction_cosine=metrics.direction_cosine,
            length_ratio=metrics.length_ratio,
            bbox_shift=Vector(dx=metrics.bbox_shift[0], dy=metrics.bbox_shift[1]),
            scale_ratio=metrics.scale_ratio,
            curvature_hotspot=CurvatureHotspot(
                difference=metrics.curvature_difference,
                user=_anchor(metrics.curvature_user),
                target=_anchor(metrics.curvature_target),
            ),
            model_quality=evidence.quality if evidence else None,
            reverse_probability=evidence.reverse_probability if evidence else None,
            order_error_probability=evidence.order_probability if evidence else None,
        )
        next_action_type = (
            "retry_current" if not accepted
            else "complete" if next_index >= len(template)
            else "draw_next"
        )
        return CoachStrokeResponse(
            protocol_version=1,
            request_id=request.request_id,
            attempt_revision=request.attempt_revision,
            engine="geometry+stroke-model" if evidence else "geometry-only",
            matched_template_index=match.matched_template_index,
            expected_template_index=request.expected_template_index,
            match_confidence=match.match_confidence,
            accepted=accepted,
            severity=severity,
            intervention=intervention,
            primary_cue=cue,
            metrics=response_metrics,
            overlay=CoachOverlay(
                problem_segment=list(metrics.problem_segment),
                target_segment=list(metrics.target_segment),
                next_start=_anchor(next_start),
            ),
            next_action=NextAction(type=next_action_type, template_index=next_index, hint_level=0),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
