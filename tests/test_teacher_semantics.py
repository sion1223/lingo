from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scorer.teacher_renderer import (
    DEFAULT_TEACHER_MODEL,
    MissingApiKeyError,
    TeacherRenderer,
    TeacherSemanticError,
    _approved_language_options,
    deterministic_fallback,
    semantic_errors,
    validate_teacher_feedback,
)
from scorer.teacher_schemas import TeacherFeedbackOutput, TeacherFeedbackRequest


def request_payload() -> dict:
    return {
        "schema_version": "teacher_feedback.v1",
        "locale": "ko",
        "learner": {
            "level": "beginner",
            "attempt_number": 3,
            "same_error_count": 2,
            "preferred_length": "short",
        },
        "task": {
            "target_char": "い",
            "nearest_competitor": "り",
            "mode": "recall",
            "critical_stroke": 1,
            "total_strokes": 2,
        },
        "locked_decision": {
            "decision_id": "decision-1",
            "error_code": "CHARACTER_RESEMBLES_COMPETITOR",
            "evidence_codes": [
                "STROKE_TOO_VERTICAL",
                "STROKE_TOO_LONG",
                "START_TOO_HIGH",
            ],
            "severity": "major",
            "confidence": 0.94,
            "accepted": False,
            "next_action": "RETRY_CRITICAL_STROKE",
        },
        "evidence": {
            "target_margin": -0.56,
            "critical_region": "stroke_2",
            "target_feature_profile": {
                "primary_direction": "down_right",
                "relative_length": "shorter",
                "start_height": "middle",
            },
            "observed_feature_profile": {
                "primary_direction": "mostly_down",
                "relative_length": "long",
                "start_height": "high",
            },
        },
        "teaching_policy": {
            "allowed_strategies": [
                "direct_correction",
                "brief_contrast",
                "micro_drill",
            ],
            "max_sentences": 2,
            "max_characters": 100,
            "must_preserve_locked_fields": True,
            "forbidden": [
                "change_diagnosis",
                "invent_score",
                "invent_evidence",
                "give_multiple_actions",
            ],
        },
    }


def valid_request(**changes) -> TeacherFeedbackRequest:
    body = deepcopy(request_payload())
    for section, values in changes.items():
        body[section].update(values)
    return TeacherFeedbackRequest.model_validate(body)


def valid_output(request: TeacherFeedbackRequest, **changes) -> TeacherFeedbackOutput:
    body = deterministic_fallback(request).model_dump(mode="json")
    body.update(changes)
    return TeacherFeedbackOutput.model_validate(body)


def test_valid_locked_feedback_passes_semantic_validation():
    request = valid_request()

    validate_teacher_feedback(request, valid_output(request))


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        ({"decision_id": "different"}, "decision_id_changed"),
        ({"error_code": "START_TOO_LOW"}, "error_code_changed"),
        ({"next_action": "DRAW_NEXT_STROKE"}, "next_action_changed"),
        ({"strategy": "progress_summary"}, "strategy_not_allowed"),
        (
            {"primary_text": "점수는 61점입니다."},
            "invented_score_or_confidence",
        ),
        (
            {"primary_text": "3획이 길어 り처럼 보입니다."},
            "unsupported_stroke_number",
        ),
        (
            {"primary_text": "り가 い처럼 보입니다."},
            "target_competitor_swapped",
        ),
        (
            {"secondary_text": "다음 획으로 넘어가세요."},
            "continued_after_rejection",
        ),
        (
            {"primary_text": "2획이 木처럼 보입니다."},
            "invented_character",
        ),
    ],
)
def test_semantic_validator_rejects_locked_or_invented_changes(
    changes,
    expected_error,
):
    request = valid_request()

    with pytest.raises(TeacherSemanticError) as caught:
        validate_teacher_feedback(request, valid_output(request, **changes))

    assert expected_error in caught.value.errors


def test_semantic_validator_rejects_direction_not_present_in_evidence():
    request = valid_request(
        locked_decision={
            "error_code": "PATH_DEVIATION",
            "evidence_codes": ["PATH_DEVIATION"],
        },
        evidence={
            "target_feature_profile": {},
            "observed_feature_profile": {},
        },
    )
    output = valid_output(
        request,
        error_code="PATH_DEVIATION",
        primary_text="획이 오른쪽으로 치우쳤습니다.",
        secondary_text="강조된 경로를 따라 다시 써 보세요.",
        spoken_text="강조된 경로를 따라 다시 써 보세요.",
    )

    with pytest.raises(TeacherSemanticError) as caught:
        validate_teacher_feedback(request, output)

    assert "unsupported_concept:right" in caught.value.errors


def test_semantic_validator_rejects_reversed_high_low_evidence():
    request = valid_request(
        locked_decision={
            "error_code": "START_TOO_HIGH",
            "evidence_codes": ["START_TOO_HIGH"],
        }
    )
    output = valid_output(
        request,
        error_code="START_TOO_HIGH",
        primary_text="시작점이 너무 낮습니다.",
        secondary_text="시작점을 더 높게 써 보세요.",
        spoken_text="시작점을 더 높게 써 보세요.",
    )

    errors = semantic_errors(request, output)

    assert "reversed_evidence:high" in errors
    assert "reversed_correction:high" in errors


def test_semantic_validator_checks_multiple_actions_in_spoken_text():
    request = valid_request()
    output = valid_output(
        request,
        primary_text="확인된 획 차이가 있습니다.",
        secondary_text="획을 짧게 써 보세요.",
        spoken_text=(
            "획을 짧게 써 보세요. 그리고 시작점을 낮춰 써 보세요."
        ),
    )

    assert "multiple_actions" in semantic_errors(request, output)


def test_semantic_validator_rejects_rephrased_target_competitor_swap():
    request = valid_request()
    output = valid_output(
        request,
        primary_text="쓴 모양이 い에 더 가깝습니다.",
        secondary_text="り를 목표로 다시 써 보세요.",
        spoken_text="り를 목표로 다시 써 보세요.",
    )

    assert "target_competitor_swapped" in semantic_errors(request, output)


def test_semantic_validator_rejects_invented_diagonal_direction():
    request = valid_request(
        locked_decision={
            "error_code": "PATH_DEVIATION",
            "evidence_codes": ["PATH_DEVIATION"],
        },
        evidence={
            "target_feature_profile": {},
            "observed_feature_profile": {},
        },
    )
    output = valid_output(
        request,
        error_code="PATH_DEVIATION",
        primary_text="획이 대각선으로 치우쳤습니다.",
        secondary_text="강조된 경로를 따라 다시 써 보세요.",
        spoken_text="강조된 경로를 따라 다시 써 보세요.",
    )

    assert "unsupported_concept:diagonal" in semantic_errors(request, output)


def test_accepted_minor_fallback_preserves_evidence_and_advances():
    request = valid_request(
        locked_decision={
            "error_code": "TOO_SHORT",
            "evidence_codes": ["TOO_SHORT"],
            "severity": "minor",
            "accepted": True,
            "next_action": "DRAW_NEXT_STROKE",
        },
        evidence={
            "target_feature_profile": {"relative_length": "longer"},
            "observed_feature_profile": {"relative_length": "short"},
        },
    )

    output = deterministic_fallback(request)

    assert "짧" in output.primary_text
    assert "잘 맞" not in output.primary_text
    assert "다음 획" in output.secondary_text
    validate_teacher_feedback(request, output)


@pytest.mark.parametrize(
    "secondary_code",
    ["START_TOO_LOW", "END_TOO_LOW"],
)
def test_fallback_primary_error_cannot_be_overridden_by_secondary_evidence(
    secondary_code,
):
    request = valid_request(
        locked_decision={
            "error_code": "START_TOO_HIGH",
            "evidence_codes": [secondary_code],
        },
        evidence={
            "target_feature_profile": {},
            "observed_feature_profile": {},
        },
    )

    output = deterministic_fallback(request)

    assert "높" in output.primary_text
    assert "낮춰" in output.secondary_text
    validate_teacher_feedback(request, output)


def test_textual_action_must_match_locked_next_action():
    accepted = valid_request(
        locked_decision={
            "error_code": "TOO_SHORT",
            "evidence_codes": ["TOO_SHORT"],
            "severity": "minor",
            "accepted": True,
            "next_action": "DRAW_NEXT_STROKE",
        },
        evidence={
            "target_feature_profile": {"relative_length": "longer"},
            "observed_feature_profile": {"relative_length": "short"},
        },
    )
    retry_text = valid_output(
        accepted,
        error_code="TOO_SHORT",
        next_action="DRAW_NEXT_STROKE",
        primary_text="획이 조금 짧습니다.",
        secondary_text="같은 획을 다시 써 보세요.",
        spoken_text="같은 획을 다시 써 보세요.",
    )
    rejected = valid_request()
    complete_text = valid_output(
        rejected,
        primary_text="2획의 차이가 확인됐습니다.",
        secondary_text="연습을 끝내세요.",
        spoken_text="연습을 끝내세요.",
    )

    accepted_errors = semantic_errors(accepted, retry_text)
    rejected_errors = semantic_errors(rejected, complete_text)
    assert "action_conflicts_with_draw_next" in accepted_errors
    assert "missing_next_stroke_action" in accepted_errors
    assert "action_conflicts_with_retry" in rejected_errors
    assert "missing_retry_action" in rejected_errors


@pytest.mark.parametrize(
    ("request_changes", "output_changes"),
    [
        (
            {
                "locked_decision": {
                    "error_code": "START_TOO_HIGH",
                    "evidence_codes": ["START_TOO_HIGH"],
                }
            },
            {
                "error_code": "START_TOO_HIGH",
                "primary_text": "시작점이 아래에 있습니다.",
                "secondary_text": "시작점을 위에 두고 다시 써 보세요.",
                "spoken_text": "시작점을 위에 두고 다시 써 보세요.",
            },
        ),
        (
            {},
            {
                "primary_text": "정답은 り이고 지금 쓴 것은 い입니다.",
                "secondary_text": "핵심 획을 다시 써 보세요.",
                "spoken_text": "핵심 획을 다시 써 보세요.",
            },
        ),
        (
            {
                "locked_decision": {
                    "error_code": "PATH_DEVIATION",
                    "evidence_codes": ["PATH_DEVIATION"],
                },
                "evidence": {
                    "target_feature_profile": {},
                    "observed_feature_profile": {},
                },
            },
            {
                "error_code": "PATH_DEVIATION",
                "primary_text": "획이 비스듬히 내려갑니다.",
                "secondary_text": "강조된 경로를 따라 다시 써 보세요.",
                "spoken_text": "강조된 경로를 따라 다시 써 보세요.",
            },
        ),
        (
            {},
            {
                "primary_text": "획을 줄이세요.",
                "secondary_text": "시작점도 내린 다음 다시 써 보세요.",
                "spoken_text": "시작점도 내린 다음 다시 써 보세요.",
            },
        ),
        (
            {},
            {
                "primary_text": "다시 써야 할 것 같지만 연습을 종료하세요.",
                "secondary_text": "",
                "spoken_text": "다시 써야 할 것 같지만 연습을 종료하세요.",
            },
        ),
        (
            {
                "locked_decision": {
                    "error_code": "START_OFFSET",
                    "evidence_codes": ["START_OFFSET"],
                },
                "evidence": {
                    "target_feature_profile": {
                        "start_position": "right_same_y"
                    },
                    "observed_feature_profile": {
                        "start_position": "offset_from_template"
                    },
                },
            },
            {
                "error_code": "START_OFFSET",
                "primary_text": "시작점 위치가 다릅니다.",
                "secondary_text": "시작점을 왼쪽으로 옮겨 다시 써 보세요.",
                "spoken_text": "시작점을 왼쪽으로 옮겨 다시 써 보세요.",
            },
        ),
    ],
)
def test_controlled_language_rejects_any_unapproved_paraphrase(
    request_changes,
    output_changes,
):
    request = valid_request(**request_changes)
    output = valid_output(request, **output_changes)

    errors = semantic_errors(request, output)

    assert any(error.startswith("unapproved_") for error in errors)


@pytest.mark.parametrize(
    "code",
    [
        "START_TOO_HIGH",
        "START_TOO_LOW",
        "START_TOO_LEFT",
        "START_TOO_RIGHT",
        "END_TOO_HIGH",
        "END_TOO_LOW",
        "STROKE_TOO_LONG",
        "STROKE_TOO_SHORT",
        "STROKE_TOO_VERTICAL",
        "STROKE_TOO_HORIZONTAL",
        "STROKE_ANGLE_MISMATCH",
        "CURVE_TOO_EARLY",
        "CURVE_TOO_LATE",
        "TERMINAL_HOOK_WRONG_DIRECTION",
        "INTER_STROKE_GAP_TOO_SMALL",
        "INTER_STROKE_GAP_TOO_LARGE",
        "START_OFFSET",
        "END_OFFSET",
        "PATH_DEVIATION",
        "CURVE_EARLY",
        "CURVE_LATE",
        "DIRECTION_REVERSED",
        "WRONG_ORDER",
        "EXTRA_STROKE",
        "MISSING_STROKE",
        "TOO_SHORT",
        "TOO_LONG",
        "POSITION_OFFSET",
        "SCALE_MISMATCH",
        "UNCERTAIN_MATCH",
    ],
)
def test_every_supported_evidence_code_has_valid_deterministic_fallback(code):
    request = valid_request(
        locked_decision={"error_code": code, "evidence_codes": [code]},
        evidence={
            "target_feature_profile": {},
            "observed_feature_profile": {},
        },
    )

    output = deterministic_fallback(request)

    assert output.error_code == code
    assert output.primary_text
    validate_teacher_feedback(request, output)


class FakeResponses:
    def __init__(
        self,
        output=None,
        error=None,
        *,
        status="completed",
        response_output=None,
        response_model="gpt-5.6-luna",
    ):
        self.output = output
        self.error = error
        self.status = status
        self.response_output = response_output or []
        self.response_model = response_model
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            status=self.status,
            model=self.response_model,
            output=self.response_output,
            output_parsed=self.output,
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=40,
                total_tokens=160,
                input_tokens_details=SimpleNamespace(cached_tokens=20),
            ),
        )


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


def test_renderer_uses_responses_parse_and_relocks_bounded_cache():
    request = valid_request()
    responses = FakeResponses(valid_output(request))
    renderer = TeacherRenderer(
        client_factory=lambda: FakeClient(responses),
        cache_size=1,
    )

    first = renderer.render(request)
    second_body = request.model_dump(mode="json")
    second_body["locked_decision"]["decision_id"] = "decision-2"
    second = renderer.render(TeacherFeedbackRequest.model_validate(second_body))

    assert first.source == "luna"
    assert first.model == DEFAULT_TEACHER_MODEL
    assert first.usage is not None and first.usage.total_tokens == 160
    assert second.source == "cache"
    assert second.feedback.decision_id == "decision-2"
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["model"] == "gpt-5.6-luna"
    assert call["text_format"] is TeacherFeedbackOutput
    assert call["reasoning"] == {"effort": "none"}
    assert call["max_output_tokens"] == 512
    assert call["store"] is False
    assert "raw_strokes" not in call["input"][1]["content"]
    provider_payload = json.loads(call["input"][1]["content"])
    approved_options = provider_payload["approved_language_options"]
    assert len(approved_options) == len(
        request.teaching_policy.allowed_strategies
    )
    assert len({option["primary_text"] for option in approved_options}) > 1
    approved = next(
        option
        for option in approved_options
        if option["strategy"] == first.feedback.strategy
    )
    assert approved["primary_text"] == first.feedback.primary_text
    assert approved["secondary_text"] == first.feedback.secondary_text
    assert approved["spoken_text"] == first.feedback.spoken_text
    assert approved["emphasis_target"] == first.feedback.emphasis_target
    assert provider_payload["request"]["locked_decision"]["decision_id"] == (
        "decision-1"
    )


def test_summary_uses_distinct_safe_language_and_purpose_validation():
    request = valid_request()

    verbalize = deterministic_fallback(request, purpose="verbalize")
    summary = deterministic_fallback(request, purpose="summary")

    assert summary.primary_text != verbalize.primary_text
    assert "이번 연습" in summary.primary_text
    validate_teacher_feedback(request, summary, purpose="summary")
    assert "unapproved_language_combination" in semantic_errors(
        request,
        summary,
        purpose="verbalize",
    )


@pytest.mark.parametrize(
    ("accepted", "next_action"),
    [
        (False, "RETRY_CRITICAL_STROKE"),
        (True, "DRAW_NEXT_STROKE"),
        (True, "COMPLETE_CHARACTER"),
        (True, "KEEP_DRAWING"),
    ],
)
@pytest.mark.parametrize("purpose", ["verbalize", "summary"])
def test_every_short_policy_option_keeps_the_locked_action(
    accepted,
    next_action,
    purpose,
):
    request = valid_request(
        locked_decision={
            "accepted": accepted,
            "next_action": next_action,
        },
        teaching_policy={
            "max_sentences": 1,
            "max_characters": 16,
        },
    )

    for option in _approved_language_options(request, purpose):
        output = TeacherFeedbackOutput(
            schema_version=request.schema_version,
            decision_id=request.locked_decision.decision_id,
            error_code=request.locked_decision.error_code,
            next_action=request.locked_decision.next_action,
            strategy=option.strategy,
            primary_text=option.primary_text,
            secondary_text=option.secondary_text,
            spoken_text=option.spoken_text,
            emphasis_target=option.emphasis_target,
        )
        validate_teacher_feedback(request, output, purpose=purpose)


@pytest.mark.parametrize(
    ("error", "reason"),
    [(TimeoutError("slow"), "timeout"), (RuntimeError("503"), "api_error")],
)
def test_renderer_provider_errors_return_http_safe_fallback(error, reason):
    request = valid_request()
    responses = FakeResponses(error=error)
    renderer = TeacherRenderer(client_factory=lambda: FakeClient(responses))

    envelope = renderer.render(request)

    assert envelope.source == "fallback"
    assert envelope.model is None
    assert envelope.fallback_reason == reason
    assert envelope.feedback.decision_id == request.locked_decision.decision_id
    validate_teacher_feedback(request, envelope.feedback)


def test_renderer_missing_key_returns_deterministic_fallback_without_env_access():
    request = valid_request()

    def missing_key_factory():
        raise MissingApiKeyError

    renderer = TeacherRenderer(client_factory=missing_key_factory)

    envelope = renderer.render(request)

    assert envelope.source == "fallback"
    assert envelope.fallback_reason == "missing_api_key"
    assert envelope.feedback.decision_id == "decision-1"


@pytest.mark.parametrize(
    "responses",
    [
        FakeResponses(output=None, status="incomplete"),
        FakeResponses(
            output=None,
            response_output=[
                SimpleNamespace(
                    content=[SimpleNamespace(type="refusal", refusal="blocked")]
                )
            ],
        ),
        FakeResponses(output=None),
    ],
)
def test_renderer_refusal_incomplete_or_empty_output_falls_back(responses):
    request = valid_request()
    renderer = TeacherRenderer(client_factory=lambda: FakeClient(responses))

    envelope = renderer.render(request)

    assert envelope.source == "fallback"
    assert envelope.fallback_reason == "refusal_or_incomplete"
    assert envelope.feedback.next_action == "RETRY_CRITICAL_STROKE"
    validate_teacher_feedback(request, envelope.feedback)


def test_renderer_semantic_failure_discards_model_response():
    request = valid_request()
    unsafe = valid_output(request, next_action="DRAW_NEXT_STROKE")
    responses = FakeResponses(unsafe)
    renderer = TeacherRenderer(client_factory=lambda: FakeClient(responses))

    envelope = renderer.render(request)

    assert envelope.source == "fallback"
    assert envelope.fallback_reason == "semantic_error"
    assert envelope.model == DEFAULT_TEACHER_MODEL
    assert envelope.usage is not None and envelope.usage.total_tokens == 160
    assert envelope.feedback.next_action == "RETRY_CRITICAL_STROKE"
