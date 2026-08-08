"""Evidence-locked GPT-5.6 Luna renderer with deterministic fallback."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import ValidationError

from scorer.teacher_schemas import (
    SCHEMA_VERSION,
    TeacherFeedbackEnvelope,
    TeacherFeedbackOutput,
    TeacherFeedbackRequest,
    TeacherUsage,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_TEACHER_MODEL = "gpt-5.6-luna"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_CACHE_SIZE = 128
MAX_TIMEOUT_SECONDS = 30.0
MAX_CACHE_SIZE = 512
ROOT_DIR = Path(__file__).resolve().parents[1]

RenderPurpose = Literal["verbalize", "summary"]


class MissingApiKeyError(RuntimeError):
    """Raised internally so the endpoint can choose a safe fallback reason."""


class DependencyUnavailableError(RuntimeError):
    """Raised internally when the optional OpenAI SDK is unavailable."""


class TeacherSemanticError(ValueError):
    """Raised when a schema-valid model response violates locked evidence."""

    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class _CachedLanguage:
    strategy: str
    primary_text: str
    secondary_text: str
    spoken_text: str
    emphasis_target: str


@dataclass(frozen=True)
class _ApprovedLanguage:
    """One server-owned option that the provider may select, not rewrite."""

    strategy: str
    primary_text: str
    secondary_text: str
    spoken_text: str
    emphasis_target: str


_EVIDENCE_FALLBACKS: dict[str, tuple[str, str]] = {
    "START_TOO_HIGH": (
        "시작점이 본보기보다 높습니다.",
        "시작점을 조금 낮춰 다시 써 보세요.",
    ),
    "START_TOO_LOW": (
        "시작점이 본보기보다 낮습니다.",
        "시작점을 조금 높여 다시 써 보세요.",
    ),
    "START_TOO_LEFT": (
        "시작점이 본보기보다 왼쪽입니다.",
        "시작점을 조금 오른쪽으로 옮겨 다시 써 보세요.",
    ),
    "START_TOO_RIGHT": (
        "시작점이 본보기보다 오른쪽입니다.",
        "시작점을 조금 왼쪽으로 옮겨 다시 써 보세요.",
    ),
    "END_TOO_HIGH": (
        "끝점이 본보기보다 높습니다.",
        "끝점을 조금 낮춰 다시 써 보세요.",
    ),
    "END_TOO_LOW": (
        "끝점이 본보기보다 낮습니다.",
        "끝점을 조금 높여 다시 써 보세요.",
    ),
    "STROKE_TOO_LONG": (
        "획이 본보기보다 깁니다.",
        "획을 조금 짧게 다시 써 보세요.",
    ),
    "STROKE_TOO_SHORT": (
        "획이 본보기보다 짧습니다.",
        "획을 조금 길게 다시 써 보세요.",
    ),
    "STROKE_TOO_VERTICAL": (
        "획이 본보기보다 세로에 가깝습니다.",
        "본보기의 기울기에 맞춰 다시 써 보세요.",
    ),
    "STROKE_TOO_HORIZONTAL": (
        "획이 본보기보다 가로에 가깝습니다.",
        "본보기의 기울기에 맞춰 다시 써 보세요.",
    ),
    "STROKE_ANGLE_MISMATCH": (
        "획의 기울기가 본보기와 다릅니다.",
        "본보기의 기울기에 맞춰 다시 써 보세요.",
    ),
    "CURVE_TOO_EARLY": (
        "곡선이 본보기보다 일찍 시작됩니다.",
        "조금 더 늦게 휘어 다시 써 보세요.",
    ),
    "CURVE_TOO_LATE": (
        "곡선이 본보기보다 늦게 시작됩니다.",
        "조금 더 일찍 휘어 다시 써 보세요.",
    ),
    "TERMINAL_HOOK_WRONG_DIRECTION": (
        "끝 꺾임 방향이 본보기와 다릅니다.",
        "끝부분을 본보기 방향에 맞춰 다시 써 보세요.",
    ),
    "INTER_STROKE_GAP_TOO_SMALL": (
        "획 사이 간격이 본보기보다 좁습니다.",
        "획 사이를 조금 더 띄워 다시 써 보세요.",
    ),
    "INTER_STROKE_GAP_TOO_LARGE": (
        "획 사이 간격이 본보기보다 넓습니다.",
        "획 사이를 조금 더 가깝게 다시 써 보세요.",
    ),
    "START_OFFSET": (
        "시작 위치가 본보기와 다릅니다.",
        "시작점을 본보기에 맞춰 다시 써 보세요.",
    ),
    "END_OFFSET": (
        "끝 위치가 본보기와 다릅니다.",
        "끝점을 본보기에 맞춰 다시 써 보세요.",
    ),
    "PATH_DEVIATION": (
        "획의 경로가 본보기와 다릅니다.",
        "강조된 경로를 따라 다시 써 보세요.",
    ),
    "CURVE_EARLY": (
        "곡선이 본보기보다 일찍 시작됩니다.",
        "조금 더 늦게 휘어 다시 써 보세요.",
    ),
    "CURVE_LATE": (
        "곡선이 본보기보다 늦게 시작됩니다.",
        "조금 더 일찍 휘어 다시 써 보세요.",
    ),
    "DIRECTION_REVERSED": (
        "획의 진행 방향이 반대입니다.",
        "본보기의 시작점부터 다시 써 보세요.",
    ),
    "WRONG_ORDER": (
        "획 순서가 본보기와 다릅니다.",
        "강조된 획부터 다시 써 보세요.",
    ),
    "EXTRA_STROKE": (
        "본보기에 없는 획이 추가되었습니다.",
        "추가된 획을 빼고 다시 써 보세요.",
    ),
    "MISSING_STROKE": (
        "본보기에 있는 획이 빠졌습니다.",
        "빠진 획을 확인해 다시 써 보세요.",
    ),
    "TOO_SHORT": (
        "획이 본보기보다 짧습니다.",
        "획을 조금 길게 다시 써 보세요.",
    ),
    "TOO_LONG": (
        "획이 본보기보다 깁니다.",
        "획을 조금 짧게 다시 써 보세요.",
    ),
    "POSITION_OFFSET": (
        "획의 위치가 본보기와 다릅니다.",
        "획을 본보기 위치에 맞춰 다시 써 보세요.",
    ),
    "SCALE_MISMATCH": (
        "글자 크기가 본보기와 다릅니다.",
        "전체 크기를 본보기에 맞춰 다시 써 보세요.",
    ),
    "UNCERTAIN_MATCH": (
        "현재 획은 본보기와의 차이가 모호합니다.",
        "강조된 획을 천천히 다시 써 보세요.",
    ),
}

_CONCEPT_PATTERNS: dict[str, re.Pattern[str]] = {
    "left": re.compile(r"왼쪽|좌측|\bleft\b", re.IGNORECASE),
    "right": re.compile(r"오른쪽|우측|\bright\b", re.IGNORECASE),
    "high": re.compile(r"높(?:게|습니다|은|다)?|위쪽|위로|\bhigh\b|\bupward\b", re.IGNORECASE),
    "low": re.compile(r"낮(?:게|습니다|은|다)?|아래쪽|아래로|\blow\b|\bdownward\b", re.IGNORECASE),
    "vertical": re.compile(r"세로|수직|\bvertical\b", re.IGNORECASE),
    "horizontal": re.compile(r"가로|수평|\bhorizontal\b", re.IGNORECASE),
    "long": re.compile(r"길(?:게|고|며|다|습니다)?|\blong(?:er)?\b", re.IGNORECASE),
    "short": re.compile(r"짧(?:게|고|으며|다|습니다)?|\bshort(?:er)?\b", re.IGNORECASE),
    "curve": re.compile(r"곡선|휘(?:어|기|는)|굽(?:혀|은)|\bcurv", re.IGNORECASE),
    "early": re.compile(r"일찍|이르게|\bearl", re.IGNORECASE),
    "late": re.compile(r"늦게|\blate", re.IGNORECASE),
    "hook": re.compile(r"갈고리|꺾임|꺾(?:어|는)|\bhook\b", re.IGNORECASE),
    "gap": re.compile(r"간격|띄워|사이를|\bgap\b|\bspacing\b", re.IGNORECASE),
    "angle": re.compile(r"기울기|각도|\bangle\b|\bslant", re.IGNORECASE),
    "diagonal": re.compile(r"대각선|사선|\bdiagonal\b", re.IGNORECASE),
}

_SCORE_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:점|%|퍼센트)|점수|확률|confidence|score)",
    re.IGNORECASE,
)
_STROKE_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:번째\s*)?(?:획|stroke)",
    re.IGNORECASE,
)
_CONTINUE_PATTERN = re.compile(
    r"다음\s*획|다음으로\s*넘어|계속\s*(?:쓰|진행)|이어\s*(?:쓰|가)|"
    r"\bnext\s+stroke\b|\bmove\s+on\b|\bcontinue\b|\bproceed\b",
    re.IGNORECASE,
)
_ACTION_PATTERN = re.compile(
    r"(?:하세요|해\s*보세요|써\s*보세요|그려\s*보세요|맞춰\s*보세요|"
    r"옮겨\s*보세요|줄여\s*보세요|늘려\s*보세요|확인해\s*보세요|"
    r"\btry\b|\bplease\b|\bwrite\b|\bdraw\b)",
    re.IGNORECASE,
)
_MULTI_ACTION_CONNECTOR = re.compile(
    r"그리고|그다음|그\s*후|동시에|\band\s+then\b|\bthen\b",
    re.IGNORECASE,
)
_RETRY_ACTION_PATTERN = re.compile(
    r"다시\s*(?:써|쓰|그려|해)|한\s*번\s*더|같은\s*획|"
    r"(?:\d+|첫|두|세|네)\s*(?:번째\s*)?획.{0,24}(?:써|쓰|그려)|"
    r"강조된\s*획.{0,20}(?:써|쓰|그려)|"
    r"\bretry\b|\btry\s+again\b|\brewrite\b|\bredo\b",
    re.IGNORECASE,
)
_NEXT_STROKE_ACTION_PATTERN = re.compile(
    r"다음\s*획|다음으로\s*넘어|이어\s*(?:쓰|가)|"
    r"\bnext\s+stroke\b|\bmove\s+on\b",
    re.IGNORECASE,
)
_KEEP_ACTION_PATTERN = re.compile(
    r"계속\s*(?:쓰|진행|해)|이어\s*(?:쓰|가)|"
    r"\bkeep\b|\bcontinue\b|\bproceed\b",
    re.IGNORECASE,
)
_COMPLETE_ACTION_PATTERN = re.compile(
    r"글자.{0,12}마무리|연습.{0,12}끝내|완료(?:하세요|합니다|했)|채점|"
    r"\bfinish(?:\s+the)?\s+(?:character|practice)\b|"
    r"\bcomplete(?:\s+the)?\s+(?:character|practice)\b|\bdone\b",
    re.IGNORECASE,
)
_NO_ERROR_PATTERN = re.compile(
    r"잘\s*맞|정확(?:합니다|해요|한)|문제\s*(?:가\s*)?없|올바릅|"
    r"\bcorrect\b|\bmatches?\s+well\b|\blooks?\s+good\b",
    re.IGNORECASE,
)
_ASSERTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "high": re.compile(
        r"(?:시작점|끝점|위치|획).{0,12}(?:너무\s*)?"
        r"(?:높습니다|높아요|높은\s*편|위쪽에\s*(?:있|놓))",
        re.IGNORECASE,
    ),
    "low": re.compile(
        r"(?:시작점|끝점|위치|획).{0,12}(?:너무\s*)?"
        r"(?:낮습니다|낮아요|낮은\s*편|아래쪽에\s*(?:있|놓))",
        re.IGNORECASE,
    ),
    "left": re.compile(
        r"(?:시작점|끝점|위치|획).{0,12}(?:왼쪽|좌측)(?:입니다|에\s*(?:있|놓))",
        re.IGNORECASE,
    ),
    "right": re.compile(
        r"(?:시작점|끝점|위치|획).{0,12}(?:오른쪽|우측)(?:입니다|에\s*(?:있|놓))",
        re.IGNORECASE,
    ),
    "long": re.compile(
        r"(?:획|길이).{0,12}(?:너무\s*)?(?:깁니다|길어요|긴\s*편)",
        re.IGNORECASE,
    ),
    "short": re.compile(
        r"(?:획|길이).{0,12}(?:너무\s*)?(?:짧습니다|짧아요|짧은\s*편)",
        re.IGNORECASE,
    ),
    "early": re.compile(r"(?:곡선|꺾임).{0,12}(?:일찍|이르게).*(?:시작|됐|되)", re.IGNORECASE),
    "late": re.compile(r"(?:곡선|꺾임).{0,12}늦게.*(?:시작|됐|되)", re.IGNORECASE),
}
_ACTION_CONCEPT_PATTERNS: dict[str, re.Pattern[str]] = {
    "high": re.compile(r"높게|높여|위쪽으로|\bhigher\b|\bmove\s+up\b", re.IGNORECASE),
    "low": re.compile(r"낮게|낮춰|아래쪽으로|\blower\b|\bmove\s+down\b", re.IGNORECASE),
    "left": re.compile(r"왼쪽으로|좌측으로|\bmove\s+left\b", re.IGNORECASE),
    "right": re.compile(r"오른쪽으로|우측으로|\bmove\s+right\b", re.IGNORECASE),
    "long": re.compile(r"길게|늘려|\blonger\b|\blengthen\b", re.IGNORECASE),
    "short": re.compile(r"짧게|줄여|\bshorter\b|\bshorten\b", re.IGNORECASE),
    "early": re.compile(r"일찍|이르게|\bearlier\b", re.IGNORECASE),
    "late": re.compile(r"늦게|\blater\b", re.IGNORECASE),
}
_OPPOSITE_CONCEPT = {
    "HIGH": ("high", "low"),
    "LOW": ("low", "high"),
    "LEFT": ("left", "right"),
    "RIGHT": ("right", "left"),
    "LONG": ("long", "short"),
    "SHORT": ("short", "long"),
    "EARLY": ("early", "late"),
    "LATE": ("late", "early"),
}
_JAPANESE_OR_CJK_PATTERN = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\U00020000-\U000323af]"
)
_SENTENCE_SPLIT = re.compile(r"[.!?。！？]+")


def _safe_float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    if not (low <= value <= high):
        return default
    return value


def _safe_int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    if not (low <= value <= high):
        return default
    return value


def load_local_openai_environment() -> bool:
    """Load repo-local secrets without overriding deployment environment values."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    env_path = ROOT_DIR / ".env.local"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)
    return True


def _sentence_count(text: str) -> int:
    return sum(bool(part.strip()) for part in _SENTENCE_SPLIT.split(text))


def _allowed_concepts(request: TeacherFeedbackRequest) -> set[str]:
    codes = {
        request.locked_decision.error_code,
        *request.locked_decision.evidence_codes,
    }
    allowed: set[str] = set()
    for code in codes:
        if "HIGH" in code or "LOW" in code:
            allowed.update({"high", "low"})
        if "LEFT" in code or "RIGHT" in code:
            allowed.update({"left", "right"})
        if "LONG" in code or "SHORT" in code:
            allowed.update({"long", "short"})
        if "VERTICAL" in code or "HORIZONTAL" in code:
            allowed.update({"vertical", "horizontal", "angle"})
        if "ANGLE" in code:
            allowed.add("angle")
        if "CURVE" in code:
            allowed.update({"curve", "early", "late"})
        if "EARLY" in code or "LATE" in code:
            allowed.update({"early", "late"})
        if "HOOK" in code:
            allowed.add("hook")
        if "GAP" in code:
            allowed.add("gap")

    profile_text = " ".join(
        f"{key} {value}"
        for profile in (
            request.evidence.target_feature_profile,
            request.evidence.observed_feature_profile,
        )
        for key, value in profile.items()
    ).lower().replace("-", "_")
    if any(token in profile_text for token in ("left", "right")):
        allowed.update({"left", "right"})
    if any(token in profile_text for token in ("height", "high", "low", "middle")):
        allowed.update({"high", "low"})
    if "left" in profile_text:
        allowed.add("left")
    if "right" in profile_text:
        allowed.add("right")
    if any(token in profile_text for token in ("vertical", "horizontal")):
        allowed.update({"vertical", "horizontal", "angle"})
    if "direction" in profile_text or "angle" in profile_text:
        allowed.add("angle")
    if any(
        token in profile_text
        for token in (
            "diagonal",
            "down_right",
            "down_left",
            "up_right",
            "up_left",
        )
    ):
        allowed.add("diagonal")
    if any(token in profile_text for token in ("long", "short", "length")):
        allowed.update({"long", "short"})
    if any(token in profile_text for token in ("curve", "curvature")):
        allowed.add("curve")
    if "hook" in profile_text:
        allowed.add("hook")
    if any(token in profile_text for token in ("gap", "spacing")):
        allowed.add("gap")
    return allowed


def _wrong_target_competitor_direction(
    request: TeacherFeedbackRequest,
    text: str,
) -> bool:
    target = request.task.target_char
    competitor = request.task.nearest_competitor
    if not competitor:
        return False
    competitor_first = re.compile(
        rf"{re.escape(competitor)}.{{0,24}}{re.escape(target)}.{{0,12}}"
        rf"(?:처럼|같|비슷|가깝|닮)",
        re.IGNORECASE,
    )
    wrong_english = re.compile(
        rf"{re.escape(competitor)}.{{0,24}}(?:resembles|looks like).{{0,12}}"
        rf"{re.escape(target)}",
        re.IGNORECASE,
    )
    if competitor_first.search(text) or wrong_english.search(text):
        return True
    if request.locked_decision.error_code == "CHARACTER_RESEMBLES_COMPETITOR":
        denied_competitor = re.compile(
            rf"(?:쓴\s*모양|필기|글자).{{0,16}}{re.escape(target)}"
            rf"(?:에|와|과).{{0,10}}(?:가깝|비슷|닮)",
            re.IGNORECASE,
        )
        competitor_as_target = re.compile(
            rf"{re.escape(competitor)}(?:을|를|이|가|은|는)?"
            rf".{{0,16}}(?:목표|정답|본보기)",
            re.IGNORECASE,
        )
        if denied_competitor.search(text) or competitor_as_target.search(text):
            return True
    return False


def _primary_language_code(request: TeacherFeedbackRequest) -> str:
    """Choose prose evidence without letting secondary codes override the lock."""
    locked = request.locked_decision
    if locked.error_code in _EVIDENCE_FALLBACKS:
        return locked.error_code
    return next(
        (
            candidate
            for candidate in locked.evidence_codes
            if candidate in _EVIDENCE_FALLBACKS
        ),
        locked.error_code,
    )


def _evidence_contradiction_errors(
    request: TeacherFeedbackRequest,
    visible_text: str,
    spoken_text: str,
) -> list[str]:
    errors: list[str] = []
    primary_code = _primary_language_code(request)
    combined = " ".join((visible_text, spoken_text))
    for marker, (observed, corrective) in _OPPOSITE_CONCEPT.items():
        if marker not in primary_code:
            continue
        if _ASSERTION_PATTERNS[corrective].search(combined):
            errors.append(f"reversed_evidence:{observed}")
        action_sentences = [
            sentence
            for sentence in _SENTENCE_SPLIT.split(combined)
            if _ACTION_PATTERN.search(sentence)
        ]
        if any(
            _ACTION_CONCEPT_PATTERNS[observed].search(sentence)
            for sentence in action_sentences
        ):
            errors.append(f"reversed_correction:{observed}")
    return errors


def _has_multiple_actions(text: str) -> bool:
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT.split(text)
        if sentence.strip()
    ]
    action_sentences = sum(
        bool(_ACTION_PATTERN.search(sentence)) for sentence in sentences
    )
    return action_sentences > 1 or bool(
        action_sentences and _MULTI_ACTION_CONNECTOR.search(text)
    )


def _next_action_errors(next_action: str, all_text: str) -> list[str]:
    has_retry = bool(_RETRY_ACTION_PATTERN.search(all_text))
    has_next = bool(_NEXT_STROKE_ACTION_PATTERN.search(all_text))
    has_keep = bool(_KEEP_ACTION_PATTERN.search(all_text))
    has_complete = bool(_COMPLETE_ACTION_PATTERN.search(all_text))
    errors: list[str] = []
    if next_action == "RETRY_CRITICAL_STROKE":
        if not has_retry:
            errors.append("missing_retry_action")
        if has_next or has_complete:
            errors.append("action_conflicts_with_retry")
    elif next_action == "DRAW_NEXT_STROKE":
        if not has_next:
            errors.append("missing_next_stroke_action")
        if has_retry or has_complete:
            errors.append("action_conflicts_with_draw_next")
    elif next_action == "COMPLETE_CHARACTER":
        if not has_complete:
            errors.append("missing_complete_action")
        if has_retry or has_next:
            errors.append("action_conflicts_with_complete")
    elif next_action == "KEEP_DRAWING":
        if not has_keep:
            errors.append("missing_keep_drawing_action")
        if has_retry or has_complete:
            errors.append("action_conflicts_with_keep_drawing")
    return errors


def semantic_errors(
    request: TeacherFeedbackRequest,
    output: TeacherFeedbackOutput,
    *,
    purpose: RenderPurpose = "verbalize",
) -> list[str]:
    """Return stable error codes for every semantic contract violation."""
    errors: list[str] = []
    locked = request.locked_decision
    if output.schema_version != request.schema_version:
        errors.append("schema_version_changed")
    if output.decision_id != locked.decision_id:
        errors.append("decision_id_changed")
    if output.error_code != locked.error_code:
        errors.append("error_code_changed")
    if output.next_action != locked.next_action:
        errors.append("next_action_changed")
    if output.strategy not in request.teaching_policy.allowed_strategies:
        errors.append("strategy_not_allowed")

    approved_options = _approved_language_options(request, purpose)
    if not any(
        output.primary_text == option.primary_text
        for option in approved_options
    ):
        errors.append("unapproved_primary_text")
    if not any(
        output.secondary_text == option.secondary_text
        for option in approved_options
    ):
        errors.append("unapproved_secondary_text")
    if not any(
        output.spoken_text == option.spoken_text
        for option in approved_options
    ):
        errors.append("unapproved_spoken_text")
    if not any(
        output.emphasis_target == option.emphasis_target
        for option in approved_options
    ):
        errors.append("unapproved_emphasis_target")
    if not any(
        output.strategy == option.strategy
        and output.primary_text == option.primary_text
        and output.secondary_text == option.secondary_text
        and output.spoken_text == option.spoken_text
        and output.emphasis_target == option.emphasis_target
        for option in approved_options
    ):
        errors.append("unapproved_language_combination")

    visible_text = " ".join(
        part for part in (output.primary_text, output.secondary_text) if part
    )
    all_text = " ".join((visible_text, output.spoken_text)).strip()
    if len(output.primary_text) + len(output.secondary_text) > request.teaching_policy.max_characters:
        errors.append("max_characters_exceeded")
    if len(output.spoken_text) > request.teaching_policy.max_characters:
        errors.append("spoken_max_characters_exceeded")
    if _sentence_count(visible_text) > request.teaching_policy.max_sentences:
        errors.append("max_sentences_exceeded")

    if _SCORE_PATTERN.search(all_text):
        errors.append("invented_score_or_confidence")
    if locked.severity != "none" and _NO_ERROR_PATTERN.search(all_text):
        errors.append("denied_locked_error")

    errors.extend(
        _evidence_contradiction_errors(
            request,
            visible_text,
            output.spoken_text,
        )
    )

    allowed_concepts = _allowed_concepts(request)
    for concept, pattern in _CONCEPT_PATTERNS.items():
        if pattern.search(all_text) and concept not in allowed_concepts:
            errors.append(f"unsupported_concept:{concept}")

    allowed_stroke_number = (
        request.task.critical_stroke + 1
        if request.task.critical_stroke is not None
        else None
    )
    mentioned_strokes = {
        int(match.group(1)) for match in _STROKE_NUMBER_PATTERN.finditer(all_text)
    }
    if mentioned_strokes and mentioned_strokes != {allowed_stroke_number}:
        errors.append("unsupported_stroke_number")

    allowed_characters = {request.task.target_char}
    if request.task.nearest_competitor:
        allowed_characters.add(request.task.nearest_competitor)
    mentioned_characters = set(_JAPANESE_OR_CJK_PATTERN.findall(all_text))
    if mentioned_characters.difference(allowed_characters):
        errors.append("invented_character")
    if _wrong_target_competitor_direction(request, all_text):
        errors.append("target_competitor_swapped")

    if not locked.accepted and _CONTINUE_PATTERN.search(all_text):
        errors.append("continued_after_rejection")
    errors.extend(_next_action_errors(locked.next_action, all_text))

    if _has_multiple_actions(visible_text) or _has_multiple_actions(
        output.spoken_text
    ):
        errors.append("multiple_actions")
    return list(dict.fromkeys(errors))


def validate_teacher_feedback(
    request: TeacherFeedbackRequest,
    output: TeacherFeedbackOutput,
    *,
    purpose: RenderPurpose = "verbalize",
) -> None:
    errors = semantic_errors(request, output, purpose=purpose)
    if errors:
        raise TeacherSemanticError(errors)


def _specific_fallback(
    request: TeacherFeedbackRequest,
) -> tuple[str, str, str]:
    locked = request.locked_decision
    if locked.severity == "none":
        diagnosis = (
            "현재 글자가 본보기와 잘 맞습니다."
            if locked.next_action == "COMPLETE_CHARACTER"
            else "현재 획이 본보기와 잘 맞습니다."
        )
        action = "현재 동작을 계속하세요."
    elif locked.error_code == "CHARACTER_RESEMBLES_COMPETITOR":
        competitor = request.task.nearest_competitor
        if competitor:
            diagnosis = f"쓴 모양이 {competitor}에 더 가깝습니다."
        else:
            diagnosis = "쓴 모양이 목표 글자와 다릅니다."
        action = "강조된 획을 본보기에 맞춰 다시 써 보세요."
    elif locked.error_code == "AMBIGUOUS_BETWEEN_CHARACTERS":
        diagnosis = "두 후보 사이의 차이가 아직 모호합니다."
        action = "강조된 획을 천천히 다시 써 보세요."
    else:
        code = _primary_language_code(request)
        diagnosis, action = _EVIDENCE_FALLBACKS.get(
            code,
            (
                "확인된 획 차이가 있습니다.",
                "강조된 획을 본보기에 맞춰 다시 써 보세요.",
            ),
        )
    if request.task.critical_stroke is not None:
        diagnosis = f"{request.task.critical_stroke + 1}획: {diagnosis}"
    if locked.next_action == "DRAW_NEXT_STROKE":
        action = "다음 획을 이어 쓰세요."
    elif locked.next_action == "COMPLETE_CHARACTER":
        action = "글자를 마무리하세요."
    elif locked.next_action == "KEEP_DRAWING":
        action = "현재 획을 계속 쓰세요."
    return diagnosis, action, action


def _bounded_language_option(
    request: TeacherFeedbackRequest,
    *,
    purpose: RenderPurpose,
    strategy: str,
) -> _ApprovedLanguage:
    """Fit one safe option to the request's length and sentence policy."""
    diagnosis, action, spoken = _specific_fallback(request)
    if purpose == "summary" or strategy == "progress_summary":
        diagnosis = f"이번 연습에서 확인한 점: {diagnosis}"
        action = f"다음에 적용할 점: {action}"
        spoken = action
    elif strategy == "brief_contrast":
        diagnosis = f"본보기와 비교하면 {diagnosis}"
    elif strategy == "micro_drill":
        diagnosis = f"연습 포인트: {diagnosis}"
        action = f"연습 동작: {action}"
        spoken = action

    policy = request.teaching_policy
    if policy.max_sentences == 1:
        primary, secondary = action, ""
    else:
        primary, secondary = diagnosis, action

    if len(primary) + len(secondary) > policy.max_characters:
        primary, secondary = action, ""
    if len(primary) > policy.max_characters:
        primary = {
            "RETRY_CRITICAL_STROKE": "다시 써 보세요.",
            "DRAW_NEXT_STROKE": "다음 획을 쓰세요.",
            "COMPLETE_CHARACTER": "글자를 마무리하세요.",
            "KEEP_DRAWING": "계속 쓰세요.",
        }.get(request.locked_decision.next_action, "계속 쓰세요.")
    if len(spoken) > policy.max_characters:
        spoken = primary

    return _ApprovedLanguage(
        strategy=strategy,
        primary_text=primary,
        secondary_text=secondary,
        spoken_text=spoken,
        emphasis_target=(
            "critical_stroke"
            if request.task.critical_stroke is not None
            else "next_action"
        ),
    )


def _approved_language_options(
    request: TeacherFeedbackRequest,
    purpose: RenderPurpose,
) -> tuple[_ApprovedLanguage, ...]:
    """Build a finite safe choice set from locked evidence and learner policy."""
    return tuple(
        _bounded_language_option(
            request,
            purpose=purpose,
            strategy=strategy,
        )
        for strategy in request.teaching_policy.allowed_strategies
    )


def deterministic_fallback(
    request: TeacherFeedbackRequest,
    *,
    purpose: RenderPurpose = "verbalize",
) -> TeacherFeedbackOutput:
    """Build a policy-bounded response without any model dependency."""
    policy = request.teaching_policy
    preferred_strategy = (
        "progress_summary" if purpose == "summary" else "direct_correction"
    )
    preferred_strategy = (
        preferred_strategy
        if preferred_strategy in policy.allowed_strategies
        else policy.allowed_strategies[0]
    )
    approved = next(
        option
        for option in _approved_language_options(request, purpose)
        if option.strategy == preferred_strategy
    )
    output = TeacherFeedbackOutput(
        schema_version=SCHEMA_VERSION,
        decision_id=request.locked_decision.decision_id,
        error_code=request.locked_decision.error_code,
        next_action=request.locked_decision.next_action,
        strategy=approved.strategy,
        primary_text=approved.primary_text,
        secondary_text=approved.secondary_text,
        spoken_text=approved.spoken_text,
        emphasis_target=approved.emphasis_target,
    )
    validate_teacher_feedback(request, output, purpose=purpose)
    return output


class TeacherRenderer:
    """Lazy Responses API adapter that can never take down scoring paths."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        model: str = DEFAULT_TEACHER_MODEL,
        timeout_seconds: float | None = None,
        cache_size: int | None = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _safe_float_env(
                "OPENAI_TEACHER_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
                1.0,
                MAX_TIMEOUT_SECONDS,
            )
        )
        self.cache_size = (
            cache_size
            if cache_size is not None
            else _safe_int_env(
                "TEACHER_FEEDBACK_CACHE_SIZE",
                DEFAULT_CACHE_SIZE,
                0,
                MAX_CACHE_SIZE,
            )
        )
        if not (1.0 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise ValueError("timeout_seconds is outside the safe range")
        if not (0 <= self.cache_size <= MAX_CACHE_SIZE):
            raise ValueError("cache_size is outside the safe range")
        self._client_factory = client_factory
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._cache: OrderedDict[str, _CachedLanguage] = OrderedDict()
        self._cache_lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            if self._client_factory is not None:
                self._client = self._client_factory()
                return self._client

            load_local_openai_environment()
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise MissingApiKeyError
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise DependencyUnavailableError from exc
            self._client = OpenAI(
                api_key=api_key,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
            return self._client

    @staticmethod
    def _cache_key(
        request: TeacherFeedbackRequest,
        purpose: RenderPurpose,
    ) -> str:
        payload = request.model_dump(mode="json")
        # Language can be reused across attempts, but locked fields are reattached.
        payload["locked_decision"].pop("decision_id", None)
        encoded = json.dumps(
            {"purpose": purpose, "request": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _get_cached(
        self,
        key: str,
        request: TeacherFeedbackRequest,
        purpose: RenderPurpose,
    ) -> TeacherFeedbackOutput | None:
        if self.cache_size == 0:
            return None
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            self._cache.move_to_end(key)
        output = TeacherFeedbackOutput(
            schema_version=SCHEMA_VERSION,
            decision_id=request.locked_decision.decision_id,
            error_code=request.locked_decision.error_code,
            next_action=request.locked_decision.next_action,
            strategy=cached.strategy,
            primary_text=cached.primary_text,
            secondary_text=cached.secondary_text,
            spoken_text=cached.spoken_text,
            emphasis_target=cached.emphasis_target,
        )
        validate_teacher_feedback(request, output, purpose=purpose)
        return output

    def _put_cached(self, key: str, output: TeacherFeedbackOutput) -> None:
        if self.cache_size == 0:
            return
        cached = _CachedLanguage(
            strategy=output.strategy,
            primary_text=output.primary_text,
            secondary_text=output.secondary_text,
            spoken_text=output.spoken_text,
            emphasis_target=output.emphasis_target,
        )
        with self._cache_lock:
            self._cache[key] = cached
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    @staticmethod
    def _system_prompt(purpose: RenderPurpose) -> str:
        purpose_line = (
            "Summarize the completed character or session."
            if purpose == "summary"
            else "Explain the locked diagnosis and give one correction."
        )
        return (
            "You are a controlled language renderer for a handwriting tutor. "
            f"{purpose_line} Select exactly one complete object from "
            "approved_language_options. Prefer micro_drill when same_error_count is "
            "at least 2, and prefer progress_summary for a summary. Copy that object's "
            "strategy, primary_text, secondary_text, spoken_text, and emphasis_target "
            "exactly; do not mix options, rewrite, translate, shorten, expand, or add "
            "claims. Copy schema_version, decision_id, error_code, and next_action "
            "exactly from request. Respond only through the provided structured "
            "output schema."
        )

    @staticmethod
    def _has_refusal(response: Any) -> bool:
        for item in getattr(response, "output", None) or []:
            for content in getattr(item, "content", None) or []:
                if getattr(content, "type", None) == "refusal":
                    return True
                if getattr(content, "refusal", None):
                    return True
        return False

    @staticmethod
    def _usage(response: Any) -> TeacherUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        input_details = getattr(usage, "input_tokens_details", None)
        cached_tokens = (
            getattr(input_details, "cached_tokens", None)
            if input_details is not None
            else None
        )
        try:
            return TeacherUsage(
                input_tokens=int(getattr(usage, "input_tokens")),
                output_tokens=int(getattr(usage, "output_tokens")),
                total_tokens=int(getattr(usage, "total_tokens")),
                cached_input_tokens=(
                    int(cached_tokens) if cached_tokens is not None else None
                ),
            )
        except (TypeError, ValueError, ValidationError):
            return None

    def _fallback_envelope(
        self,
        request: TeacherFeedbackRequest,
        purpose: RenderPurpose,
        reason: str,
        started: float,
        *,
        usage: TeacherUsage | None = None,
        provider_model: str | None = None,
    ) -> TeacherFeedbackEnvelope:
        return TeacherFeedbackEnvelope(
            feedback=deterministic_fallback(request, purpose=purpose),
            source="fallback",
            model=provider_model,
            fallback_reason=reason,
            latency_ms=(time.perf_counter() - started) * 1000,
            cached=False,
            usage=usage,
        )

    def render(
        self,
        request: TeacherFeedbackRequest,
        *,
        purpose: RenderPurpose = "verbalize",
    ) -> TeacherFeedbackEnvelope:
        started = time.perf_counter()
        provider_usage: TeacherUsage | None = None
        provider_model: str | None = None
        key = self._cache_key(request, purpose)
        try:
            cached = self._get_cached(key, request, purpose)
        except (TeacherSemanticError, ValidationError):
            cached = None
        if cached is not None:
            return TeacherFeedbackEnvelope(
                feedback=cached,
                source="cache",
                model=self.model,
                fallback_reason=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                cached=True,
                usage=None,
            )

        try:
            client = self._get_client()
            approved_options = _approved_language_options(request, purpose)
            provider_payload = {
                "request": request.model_dump(mode="json"),
                "approved_language_options": [
                    {
                        "strategy": option.strategy,
                        "primary_text": option.primary_text,
                        "secondary_text": option.secondary_text,
                        "spoken_text": option.spoken_text,
                        "emphasis_target": option.emphasis_target,
                    }
                    for option in approved_options
                ],
            }
            response = client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": self._system_prompt(purpose)},
                    {
                        "role": "user",
                        "content": json.dumps(
                            provider_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                text_format=TeacherFeedbackOutput,
                reasoning={"effort": "none"},
                max_output_tokens=512,
                store=False,
                timeout=self.timeout_seconds,
            )
            provider_usage = self._usage(response)
            response_model = getattr(response, "model", None)
            provider_model = (
                str(response_model) if response_model else self.model
            )
            status = getattr(response, "status", "completed")
            if (
                status != "completed"
                or self._has_refusal(response)
                or getattr(response, "output_parsed", None) is None
            ):
                return self._fallback_envelope(
                    request,
                    purpose,
                    "refusal_or_incomplete",
                    started,
                    usage=provider_usage,
                    provider_model=provider_model,
                )
            output = TeacherFeedbackOutput.model_validate(response.output_parsed)
            validate_teacher_feedback(request, output, purpose=purpose)
            self._put_cached(key, output)
            return TeacherFeedbackEnvelope(
                feedback=output,
                source="luna",
                model=provider_model,
                fallback_reason=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                cached=False,
                usage=provider_usage,
            )
        except MissingApiKeyError:
            reason = "missing_api_key"
        except DependencyUnavailableError:
            reason = "dependency_unavailable"
        except TeacherSemanticError as exc:
            LOGGER.warning(
                "teacher feedback semantic validation failed (%s)",
                ",".join(exc.errors),
            )
            reason = "semantic_error"
        except ValidationError:
            reason = "schema_error"
        except Exception as exc:  # OpenAI/network failures must never fail scoring.
            exception_name = type(exc).__name__.lower()
            reason = "timeout" if "timeout" in exception_name else "api_error"
            LOGGER.warning(
                "teacher feedback provider failed (%s)",
                type(exc).__name__,
            )
        return self._fallback_envelope(
            request,
            purpose,
            reason,
            started,
            usage=provider_usage,
            provider_model=provider_model,
        )
