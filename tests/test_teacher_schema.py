from __future__ import annotations

from copy import deepcopy
import threading

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from scorer import server
from scorer.teacher_renderer import (
    DEFAULT_TEACHER_MODEL,
    MissingApiKeyError,
    TeacherRenderer,
    deterministic_fallback,
)
from scorer.teacher_schemas import (
    SCHEMA_VERSION,
    TeacherFeedbackEnvelope,
    TeacherFeedbackOutput,
    TeacherFeedbackRequest,
)


def teacher_payload() -> dict:
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


def test_teacher_feedback_v1_request_and_output_are_strict():
    request = TeacherFeedbackRequest.model_validate(teacher_payload())
    output = TeacherFeedbackOutput(
        schema_version=SCHEMA_VERSION,
        decision_id=request.locked_decision.decision_id,
        error_code=request.locked_decision.error_code,
        next_action=request.locked_decision.next_action,
        strategy="brief_contrast",
        primary_text="2획이 길고 세로로 내려가 り처럼 보입니다.",
        secondary_text="い는 오른쪽 중간에서 시작해 짧게 오른쪽 아래로 마무리하세요.",
        spoken_text="두 번째 획을 조금 짧고 오른쪽 아래로 써 보세요.",
        emphasis_target="critical_stroke",
    )

    assert request.schema_version == SCHEMA_VERSION
    assert output.schema_version == SCHEMA_VERSION
    assert TeacherFeedbackRequest.model_json_schema()["additionalProperties"] is False
    assert TeacherFeedbackOutput.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"raw_strokes": [[[0, 0], [1, 1]]]}),
        lambda body: body["evidence"].update({"image": "data:image/png;base64,..."}),
        lambda body: body["locked_decision"].update({"error_code": "bad-code"}),
        lambda body: body["teaching_policy"].update({"must_preserve_locked_fields": False}),
        lambda body: body["teaching_policy"].update(
            {"forbidden": ["change_diagnosis", "invent_score"]}
        ),
        lambda body: body["task"].update({"critical_stroke": 2}),
        lambda body: body["evidence"].update({"critical_region": "stroke_1"}),
        lambda body: body["locked_decision"].update(
            {"accepted": False, "next_action": "DRAW_NEXT_STROKE"}
        ),
        lambda body: body["locked_decision"].update(
            {"accepted": False, "next_action": "KEEP_DRAWING"}
        ),
        lambda body: body["locked_decision"].update(
            {"accepted": True, "next_action": "RETRY_CRITICAL_STROKE"}
        ),
        lambda body: body["locked_decision"].update(
            {"accepted": True, "next_action": "UNKNOWN_ACTION"}
        ),
        lambda body: body["locked_decision"].update(
            {
                "error_code": "NO_ERROR",
                "severity": "major",
                "accepted": False,
            }
        ),
        lambda body: body["locked_decision"].update(
            {
                "error_code": "SOME_ERROR",
                "evidence_codes": [],
                "severity": "none",
                "accepted": True,
                "next_action": "KEEP_DRAWING",
            }
        ),
    ],
)
def test_teacher_request_rejects_unversioned_or_sensitive_shape_changes(mutate):
    body = deepcopy(teacher_payload())
    mutate(body)

    with pytest.raises(ValidationError):
        TeacherFeedbackRequest.model_validate(body)


def test_teacher_model_default_is_exact_luna_id():
    assert DEFAULT_TEACHER_MODEL == "gpt-5.6-luna"


def test_teacher_v1_rejects_locales_without_validated_fallbacks():
    body = teacher_payload()
    body["locale"] = "ja"

    with pytest.raises(ValidationError):
        TeacherFeedbackRequest.model_validate(body)


@pytest.mark.parametrize("value", ["가", ".", "!", "?"])
@pytest.mark.parametrize("field", ["target_char", "nearest_competitor"])
def test_teacher_task_rejects_characters_outside_kana_and_cjk(field, value):
    body = teacher_payload()
    body["task"][field] = value

    with pytest.raises(ValidationError):
        TeacherFeedbackRequest.model_validate(body)


def test_teacher_endpoints_share_locked_envelope_without_loading_scorers(monkeypatch):
    monkeypatch.delenv("TEACHER_API_TOKEN", raising=False)
    request = TeacherFeedbackRequest.model_validate(teacher_payload())

    class FakeRenderer:
        purposes: list[str] = []

        def render(self, received, *, purpose):
            assert received == request
            self.purposes.append(purpose)
            return TeacherFeedbackEnvelope(
                feedback=deterministic_fallback(received, purpose=purpose),
                source="fallback",
                model=None,
                fallback_reason="api_error",
                latency_ms=0.1,
                cached=False,
                usage=None,
            )

    fake = FakeRenderer()
    monkeypatch.setattr(server, "get_teacher_renderer", lambda: fake)
    monkeypatch.setattr(
        server,
        "get_model",
        lambda: (_ for _ in ()).throw(AssertionError("must not load deep model")),
    )
    monkeypatch.setattr(
        server,
        "get_coach_engine",
        lambda: (_ for _ in ()).throw(AssertionError("must not load coach model")),
    )
    client = TestClient(server.app)

    verbalize = client.post("/coach/verbalize", json=teacher_payload())
    summary = client.post("/coach/summary", json=teacher_payload())

    assert verbalize.status_code == 200
    assert summary.status_code == 200
    assert verbalize.json()["feedback"]["decision_id"] == "decision-1"
    assert verbalize.json()["source"] == "fallback"
    assert summary.json()["feedback"]["schema_version"] == SCHEMA_VERSION
    assert fake.purposes == ["verbalize", "summary"]


def test_teacher_token_is_optional_locally_and_required_when_configured(monkeypatch):
    request = TeacherFeedbackRequest.model_validate(teacher_payload())

    class FakeRenderer:
        def render(self, received, *, purpose):
            return TeacherFeedbackEnvelope(
                feedback=deterministic_fallback(received, purpose=purpose),
                source="fallback",
                model=None,
                fallback_reason="api_error",
                latency_ms=0.1,
                cached=False,
                usage=None,
            )

    monkeypatch.setattr(server, "get_teacher_renderer", lambda: FakeRenderer())
    monkeypatch.setenv("TEACHER_API_TOKEN", "unit-test-teacher-token")
    client = TestClient(server.app)

    missing = client.post("/coach/verbalize", json=teacher_payload())
    invalid = client.post(
        "/coach/verbalize",
        json=teacher_payload(),
        headers={"X-Lingo-Teacher-Token": "wrong-token"},
    )
    accepted = client.post(
        "/coach/verbalize",
        json=teacher_payload(),
        headers={"X-Lingo-Teacher-Token": "unit-test-teacher-token"},
    )

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "TEACHER_TOKEN_REQUIRED"
    assert invalid.status_code == 403
    assert invalid.json()["detail"]["code"] == "TEACHER_TOKEN_INVALID"
    assert accepted.status_code == 200
    assert accepted.json()["feedback"]["decision_id"] == request.locked_decision.decision_id
    assert "unit-test-teacher-token" not in missing.text
    assert "unit-test-teacher-token" not in invalid.text


def test_teacher_capacity_exhaustion_fails_fast_with_locked_http_200_fallback(
    monkeypatch,
):
    monkeypatch.delenv("TEACHER_API_TOKEN", raising=False)
    occupied_slot = threading.BoundedSemaphore(1)
    assert occupied_slot.acquire(blocking=False)
    monkeypatch.setattr(server, "_teacher_slots", occupied_slot)

    def unexpected_renderer():
        raise AssertionError("renderer must not run without a provider slot")

    monkeypatch.setattr(server, "get_teacher_renderer", unexpected_renderer)
    client = TestClient(server.app)
    try:
        response = client.post("/coach/verbalize", json=teacher_payload())
    finally:
        occupied_slot.release()

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert body["fallback_reason"] == "capacity_exceeded"
    assert body["feedback"]["decision_id"] == "decision-1"
    assert body["feedback"]["next_action"] == "RETRY_CRITICAL_STROKE"


def test_teacher_slot_is_released_after_unexpected_renderer_failure(monkeypatch):
    monkeypatch.delenv("TEACHER_API_TOKEN", raising=False)
    monkeypatch.setattr(server, "_teacher_slots", threading.BoundedSemaphore(1))
    calls = 0

    class FlakyRenderer:
        def render(self, received, *, purpose):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("provider failed")
            return TeacherFeedbackEnvelope(
                feedback=deterministic_fallback(received, purpose=purpose),
                source="fallback",
                model=None,
                fallback_reason="api_error",
                latency_ms=0.1,
                cached=False,
                usage=None,
            )

    renderer = FlakyRenderer()
    monkeypatch.setattr(server, "get_teacher_renderer", lambda: renderer)
    client = TestClient(server.app)

    first = client.post("/coach/verbalize", json=teacher_payload())
    second = client.post("/coach/verbalize", json=teacher_payload())

    assert first.status_code == 200
    assert first.json()["fallback_reason"] == "api_error"
    assert second.status_code == 200
    assert calls == 2


def test_provider_failure_with_secondary_opposite_evidence_still_returns_200(
    monkeypatch,
):
    monkeypatch.delenv("TEACHER_API_TOKEN", raising=False)
    body = teacher_payload()
    body["locked_decision"].update(
        {
            "error_code": "START_TOO_HIGH",
            "evidence_codes": ["END_TOO_LOW"],
        }
    )
    body["evidence"].update(
        {
            "target_feature_profile": {},
            "observed_feature_profile": {},
        }
    )

    def missing_key_factory():
        raise MissingApiKeyError

    monkeypatch.setattr(
        server,
        "get_teacher_renderer",
        lambda: TeacherRenderer(client_factory=missing_key_factory),
    )
    monkeypatch.setattr(server, "_teacher_slots", threading.BoundedSemaphore(1))

    response = TestClient(server.app).post("/coach/verbalize", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "fallback"
    assert payload["fallback_reason"] == "missing_api_key"
    assert "높" in payload["feedback"]["primary_text"]


@pytest.mark.parametrize("value", ["0", "33", "invalid"])
def test_invalid_teacher_concurrency_uses_safe_default(monkeypatch, value):
    monkeypatch.setenv("TEACHER_MAX_CONCURRENCY", value)

    assert (
        server._configured_teacher_concurrency()
        == server.DEFAULT_TEACHER_MAX_CONCURRENCY
    )


def test_valid_teacher_concurrency_is_configurable(monkeypatch):
    monkeypatch.setenv("TEACHER_MAX_CONCURRENCY", "7")

    assert server._configured_teacher_concurrency() == 7
