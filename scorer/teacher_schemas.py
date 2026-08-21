"""Versioned contracts for evidence-locked teacher feedback."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "teacher_feedback.v1"

StableCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    ),
]
PolicyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=400),
]
ProfileKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=48,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
ProfileValue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NextAction = Literal[
    "RETRY_CRITICAL_STROKE",
    "DRAW_NEXT_STROKE",
    "KEEP_DRAWING",
    "COMPLETE_CHARACTER",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class LearnerContext(StrictModel):
    level: Literal["beginner", "intermediate", "advanced"]
    attempt_number: int = Field(ge=1, le=10_000)
    same_error_count: int = Field(ge=0, le=10_000)
    preferred_length: Literal["short", "medium", "long"]


class TeacherTask(StrictModel):
    target_char: str = Field(min_length=1, max_length=2)
    nearest_competitor: str | None = Field(
        min_length=1,
        max_length=2,
    )
    mode: Literal["trace", "recall", "character_summary", "session_summary"]
    critical_stroke: int | None = Field(ge=0, le=63)
    total_strokes: int = Field(ge=1, le=64)

    @field_validator("target_char", "nearest_competitor")
    @classmethod
    def exactly_one_character(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 1:
            raise ValueError("character fields must contain exactly one character")
        codepoint = ord(value)
        supported = (
            0x3040 <= codepoint <= 0x30FF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x20000 <= codepoint <= 0x323AF
        )
        if not supported:
            raise ValueError("character fields must contain kana or CJK ideographs")
        return value

    @model_validator(mode="after")
    def validate_task_relationships(self) -> "TeacherTask":
        if (
            self.nearest_competitor is not None
            and self.nearest_competitor == self.target_char
        ):
            raise ValueError("nearest_competitor must differ from target_char")
        if (
            self.critical_stroke is not None
            and self.critical_stroke >= self.total_strokes
        ):
            raise ValueError("critical_stroke must be a zero-based stroke index")
        return self


class LockedDecision(StrictModel):
    decision_id: str = Field(min_length=1, max_length=128)
    error_code: StableCode
    evidence_codes: list[StableCode] = Field(max_length=32)
    severity: Literal["none", "minor", "major"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    accepted: bool
    next_action: NextAction

    @field_validator("evidence_codes")
    @classmethod
    def unique_evidence_codes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_codes must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_acceptance_action_consistency(self) -> "LockedDecision":
        if self.severity == "none":
            if self.error_code != "NO_ERROR":
                raise ValueError("severity none requires NO_ERROR")
            if not self.accepted:
                raise ValueError("severity none must be accepted")
            if self.evidence_codes:
                raise ValueError("severity none cannot carry error evidence")
        else:
            if self.error_code == "NO_ERROR":
                raise ValueError("error decisions cannot use NO_ERROR")
            if not self.evidence_codes:
                raise ValueError("minor and major decisions require evidence codes")
        if not self.accepted and self.next_action != "RETRY_CRITICAL_STROKE":
            raise ValueError("rejected decisions must retry the critical stroke")
        if self.accepted and self.next_action == "RETRY_CRITICAL_STROKE":
            raise ValueError("accepted decisions cannot require a retry")
        return self


class TeacherEvidence(StrictModel):
    target_margin: FiniteFloat | None
    critical_region: str | None = Field(min_length=1, max_length=64)
    target_feature_profile: dict[ProfileKey, ProfileValue] = Field(max_length=24)
    observed_feature_profile: dict[ProfileKey, ProfileValue] = Field(max_length=24)


REQUIRED_FORBIDDEN_POLICIES = frozenset(
    {
        "change_diagnosis",
        "invent_score",
        "invent_evidence",
        "give_multiple_actions",
    }
)


class TeachingPolicy(StrictModel):
    allowed_strategies: list[
        Literal[
            "direct_correction",
            "brief_contrast",
            "micro_drill",
            "progress_summary",
        ]
    ] = Field(min_length=1, max_length=4)
    max_sentences: int = Field(ge=1, le=4)
    max_characters: int = Field(ge=16, le=400)
    must_preserve_locked_fields: Literal[True]
    forbidden: list[PolicyCode] = Field(min_length=4, max_length=16)

    @field_validator("allowed_strategies", "forbidden")
    @classmethod
    def unique_policy_values(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("policy values must not contain duplicates")
        return value

    @model_validator(mode="after")
    def require_core_safety_policies(self) -> "TeachingPolicy":
        missing = REQUIRED_FORBIDDEN_POLICIES.difference(self.forbidden)
        if missing:
            raise ValueError(
                "forbidden must include the core teacher safety policies"
            )
        return self


class TeacherFeedbackRequest(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    locale: Literal["ko"]
    learner: LearnerContext
    task: TeacherTask
    locked_decision: LockedDecision
    evidence: TeacherEvidence
    teaching_policy: TeachingPolicy

    @model_validator(mode="after")
    def validate_critical_region(self) -> "TeacherFeedbackRequest":
        region = self.evidence.critical_region
        critical = self.task.critical_stroke
        if region and region.startswith("stroke_"):
            suffix = region.removeprefix("stroke_")
            if not suffix.isdigit() or int(suffix) < 1:
                raise ValueError("critical_region stroke number must be one-based")
            if critical is not None and int(suffix) != critical + 1:
                raise ValueError("critical_region must match critical_stroke")
        return self


class TeacherFeedbackOutput(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    decision_id: str = Field(min_length=1, max_length=128)
    error_code: StableCode
    next_action: NextAction
    strategy: Literal[
        "direct_correction",
        "brief_contrast",
        "micro_drill",
        "progress_summary",
    ]
    primary_text: ShortText = Field(min_length=1)
    secondary_text: ShortText
    spoken_text: ShortText = Field(min_length=1)
    emphasis_target: Literal[
        "critical_stroke",
        "target_char",
        "competitor",
        "next_action",
        "whole_character",
        "none",
    ]


class TeacherUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)


FallbackReason = Literal[
    "missing_api_key",
    "dependency_unavailable",
    "timeout",
    "api_error",
    "refusal_or_incomplete",
    "schema_error",
    "semantic_error",
    "capacity_exceeded",
    "rate_limited",
    "daily_budget_exceeded",
]


class TeacherFeedbackEnvelope(StrictModel):
    feedback: TeacherFeedbackOutput
    source: Literal["luna", "cache", "fallback"]
    model: str | None
    fallback_reason: FallbackReason | None
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    cached: bool
    usage: TeacherUsage | None = None
