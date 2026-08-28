"""Pydantic contracts for the realtime stroke coach."""

from __future__ import annotations

from enum import Enum
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
LegacyPoint = tuple[FiniteFloat, FiniteFloat]


class ErrorCode(str, Enum):
    START_OFFSET = "START_OFFSET"
    END_OFFSET = "END_OFFSET"
    PATH_DEVIATION = "PATH_DEVIATION"
    CURVE_EARLY = "CURVE_EARLY"
    CURVE_LATE = "CURVE_LATE"
    DIRECTION_REVERSED = "DIRECTION_REVERSED"
    WRONG_ORDER = "WRONG_ORDER"
    EXTRA_STROKE = "EXTRA_STROKE"
    MISSING_STROKE = "MISSING_STROKE"
    TOO_SHORT = "TOO_SHORT"
    TOO_LONG = "TOO_LONG"
    POSITION_OFFSET = "POSITION_OFFSET"
    SCALE_MISMATCH = "SCALE_MISMATCH"
    UNCERTAIN_MATCH = "UNCERTAIN_MATCH"


class ApiErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_STROKE = "INVALID_STROKE"
    TEMPLATE_UNAVAILABLE = "TEMPLATE_UNAVAILABLE"
    COACH_FAILED = "COACH_FAILED"


class RichPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: FiniteFloat
    y: FiniteFloat
    t: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    pressure: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    tiltX: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)] | None = None
    tiltY: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)] | None = None
    pointerType: Literal["pen", "touch", "mouse"] | None = None


InputPoint = LegacyPoint | RichPoint
Stroke = Annotated[list[InputPoint], Field(min_length=1, max_length=4096)]


class ClientMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path_error: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    direction_cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)] | None = None


class CoachStrokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    attempt_revision: int = Field(ge=0)
    char: str = Field(min_length=1, max_length=2)
    mode: Literal["trace", "recall"]
    accepted_strokes: Annotated[list[Stroke], Field(max_length=64)] = Field(default_factory=list)
    current_stroke: Stroke
    expected_template_index: int = Field(ge=0, le=64)
    client_metrics: ClientMetrics | None = None

    @field_validator("char")
    @classmethod
    def one_character(cls, value: str) -> str:
        value = value.strip()
        if len(value) != 1:
            raise ValueError("char must contain exactly one character")
        return value


class Anchor(BaseModel):
    x: float
    y: float


class Vector(BaseModel):
    dx: float
    dy: float


class PrimaryCue(BaseModel):
    code: ErrorCode
    text: str
    confidence: float = Field(ge=0, le=1)
    anchor: Anchor | None = None
    vector: Vector | None = None


class CurvatureHotspot(BaseModel):
    difference: float = Field(ge=0)
    user: Anchor
    target: Anchor


class CoachMetrics(BaseModel):
    start_error: float = Field(ge=0)
    end_error: float = Field(ge=0)
    path_error: float = Field(ge=0)
    shape_error: float = Field(ge=0)
    direction_cosine: float = Field(ge=-1, le=1)
    length_ratio: float = Field(ge=0)
    bbox_shift: Vector
    scale_ratio: float = Field(ge=0)
    curvature_hotspot: CurvatureHotspot
    model_quality: float | None = Field(default=None, ge=0, le=1)
    reverse_probability: float | None = Field(default=None, ge=0, le=1)
    order_error_probability: float | None = Field(default=None, ge=0, le=1)


class CoachOverlay(BaseModel):
    problem_segment: list[LegacyPoint] = Field(default_factory=list)
    target_segment: list[LegacyPoint] = Field(default_factory=list)
    next_start: Anchor | None = None


class NextAction(BaseModel):
    type: Literal["draw_next", "retry_current", "complete", "keep_drawing"]
    template_index: int = Field(ge=0)
    hint_level: int = Field(default=0, ge=0)


class CoachStrokeResponse(BaseModel):
    protocol_version: Literal[1] = 1
    request_id: str
    attempt_revision: int = Field(ge=0)
    engine: Literal["geometry-only", "geometry+stroke-model"]
    matched_template_index: int | None
    expected_template_index: int = Field(ge=0)
    match_confidence: float = Field(ge=0, le=1)
    accepted: bool
    severity: Literal["none", "minor", "major"]
    intervention: Literal["silent", "nudge", "pause_and_retry"]
    primary_cue: PrimaryCue | None = None
    metrics: CoachMetrics
    overlay: CoachOverlay
    next_action: NextAction
    latency_ms: float = Field(ge=0)


class AttemptStrokeResult(BaseModel):
    """Append-only history for every completed stroke, including undone ones."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(default=0, ge=0, le=4095)
    stroke_index: int = Field(ge=0, le=63)
    stroke: Stroke | None = None
    matched_template_index: int | None = Field(default=None, ge=0, le=63)
    accepted: bool
    error_code: ErrorCode | None = None
    confidence: float = Field(ge=0, le=1)
    intervention: Literal["silent", "nudge", "pause_and_retry"]
    source: Literal["local", "server"] = "local"
    undone: bool = False


class AttemptEvent(BaseModel):
    """One batched, anonymous writing attempt with lossless point metadata."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    session_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    attempt_revision: int = Field(ge=0)
    char: str = Field(min_length=1, max_length=2)
    mode: Literal["trace", "recall"]
    ended_reason: Literal[
        "scored",
        "score_failed",
        "cleared",
        "character_changed",
        "page_hidden",
    ]
    started_at: datetime
    ended_at: datetime
    strokes: Annotated[list[Stroke], Field(max_length=64)] = Field(default_factory=list)
    stroke_results: Annotated[
        list[AttemptStrokeResult],
        Field(max_length=4096),
    ] = Field(default_factory=list)
    final_score: float | None = Field(default=None, ge=0, le=100)
    training_consent: Literal[False] = False
    client_version: str = Field(min_length=1, max_length=64)

    @field_validator("char")
    @classmethod
    def one_attempt_character(cls, value: str) -> str:
        value = value.strip()
        if len(value) != 1:
            raise ValueError("char must contain exactly one character")
        return value

    @model_validator(mode="after")
    def chronological_attempt(self):
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        if not self.strokes and not self.stroke_results:
            raise ValueError("attempt must contain at least one stroke event")
        return self
