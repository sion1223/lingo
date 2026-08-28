from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from scorer.realtime import FastCoachEngine, InvalidStroke
from scorer.schemas import CoachStrokeRequest


def template_strokes():
    return [
        np.linspace((0.1, 0.2), (0.9, 0.2), 12),
        np.linspace((0.8, 0.2), (0.8, 0.9), 12),
    ]


def request(current_stroke, **overrides):
    payload = {
        "protocol_version": 1,
        "request_id": "request-1",
        "session_id": "session-1",
        "attempt_id": "attempt-1",
        "attempt_revision": 3,
        "char": "永",
        "mode": "trace",
        "accepted_strokes": [],
        "current_stroke": current_stroke,
        "expected_template_index": 0,
    }
    payload.update(overrides)
    return CoachStrokeRequest.model_validate(payload)


class FakeStrokeModel(torch.nn.Module):
    def __init__(self, reverse_logit=5.0, order_logit=-5.0):
        super().__init__()
        self.anchor = torch.nn.Parameter(torch.zeros(()))
        self.reverse_logit = reverse_logit
        self.order_logit = order_logit
        self.forward_calls = 0

    def forward(self, user, template):
        self.forward_calls += 1
        stroke_ids = user[1]
        stroke_count = int(stroke_ids.max().item()) + 1
        return {
            "overall": torch.full((1,), 0.7, device=self.anchor.device),
            "q": torch.full((1, stroke_count), 0.6, device=self.anchor.device),
            "rev_logit": torch.full(
                (1, stroke_count), self.reverse_logit, device=self.anchor.device
            ),
            "ord_logit": torch.full(
                (1, stroke_count), self.order_logit, device=self.anchor.device
            ),
        }


class FailingStrokeModel(FakeStrokeModel):
    def forward(self, user, template):
        self.forward_calls += 1
        raise RuntimeError("checkpoint internals must not reach the client")


class CapacityLimitedModel(FakeStrokeModel):
    def __init__(self):
        super().__init__()
        self.encoder = SimpleNamespace(
            stroke_emb=SimpleNamespace(num_embeddings=1),
            pos_emb=SimpleNamespace(num_embeddings=12),
        )


def test_geometry_only_engine_returns_structured_single_cue():
    strokes = template_strokes()
    shifted = strokes[0] + np.array([0.1, 0.0])
    engine = FastCoachEngine(lambda _char: strokes)

    response = engine.coach(request(shifted.tolist()))

    assert response.engine == "geometry-only"
    assert response.request_id == "request-1"
    assert response.attempt_revision == 3
    assert response.primary_cue is not None
    assert response.primary_cue.code == "START_OFFSET"
    assert response.metrics.start_error > 0.09
    assert response.next_action.type in {"draw_next", "retry_current"}
    assert response.latency_ms >= 0


def test_geometry_only_engine_accepts_same_shape_with_parallel_translation():
    strokes = template_strokes()
    shifted = strokes[0] + np.array([0.0, 0.2])
    engine = FastCoachEngine(lambda _char: strokes)

    response = engine.coach(request(shifted.tolist()))

    assert response.metrics.shape_error == pytest.approx(0.0, abs=1e-8)
    assert response.primary_cue.code == "START_OFFSET"
    assert response.accepted is True
    assert response.intervention == "nudge"


def test_recall_engine_accepts_same_form_at_any_position_and_scale_without_a_nudge():
    strokes = template_strokes()
    moved_and_scaled = strokes[0] * 0.55 + np.array([0.25, 0.48])
    engine = FastCoachEngine(lambda _char: strokes)

    response = engine.coach(request(moved_and_scaled.tolist(), mode="recall"))

    assert response.metrics.form_error == pytest.approx(0.0, abs=1e-8)
    assert response.primary_cue is None
    assert response.accepted is True
    assert response.next_action.type == "draw_next"
    assert response.overlay.next_start is None


def test_recall_engine_retries_a_large_form_change_but_not_its_start_position():
    strokes = template_strokes()[:1]
    different_form = np.asarray(
        [[0.5, 0.1], [0.5, 0.5], [0.5, 0.9]],
        dtype=np.float64,
    )
    engine = FastCoachEngine(lambda _char: strokes)

    response = engine.coach(request(different_form.tolist(), mode="recall"))

    assert response.primary_cue.code == "PATH_DEVIATION"
    assert response.accepted is False
    assert response.intervention == "pause_and_retry"


def test_lightweight_model_is_called_once_and_conflict_stays_a_nudge():
    strokes = template_strokes()
    model = FakeStrokeModel(reverse_logit=5.0)
    engine = FastCoachEngine(lambda _char: strokes, stroke_model=model)

    response = engine.coach(request(strokes[0].tolist()))

    assert model.forward_calls == 1
    assert response.engine == "geometry+stroke-model"
    assert response.metrics.model_quality == pytest.approx(0.6)
    assert response.metrics.reverse_probability > 0.99
    assert response.primary_cue.code == "DIRECTION_REVERSED"
    assert response.primary_cue.confidence < 0.82
    assert response.accepted is True


def test_model_failure_falls_back_to_geometry_without_exposing_exception():
    strokes = template_strokes()
    model = FailingStrokeModel()
    engine = FastCoachEngine(lambda _char: strokes, stroke_model=model)

    response = engine.coach(request(strokes[0].tolist()))

    assert model.forward_calls == 1
    assert response.engine == "geometry-only"
    assert response.primary_cue is None
    assert "checkpoint" not in response.model_dump_json()


def test_rich_metadata_is_accepted_while_model_receives_one_forward():
    strokes = template_strokes()
    rich = [
        {"x": float(x), "y": float(y), "t": index * 10, "pressure": 0.5}
        for index, (x, y) in enumerate(strokes[0])
    ]
    model = FakeStrokeModel(reverse_logit=-5.0)
    engine = FastCoachEngine(lambda _char: strokes, stroke_model=model)

    response = engine.coach(request(rich))

    assert model.forward_calls == 1
    assert response.accepted is True
    assert response.metrics.path_error == pytest.approx(0.0, abs=1e-8)


def test_model_capacity_limit_uses_geometry_without_disabling_the_model():
    strokes = template_strokes()
    model = CapacityLimitedModel()
    engine = FastCoachEngine(lambda _char: strokes, stroke_model=model)

    response = engine.coach(request(strokes[0].tolist()))

    assert response.engine == "geometry-only"
    assert model.forward_calls == 0
    assert engine.stroke_model is model


def test_geometry_mode_validates_the_accepted_prefix_contract():
    strokes = template_strokes()
    engine = FastCoachEngine(lambda _char: strokes)

    with pytest.raises(InvalidStroke, match="accepted stroke prefix"):
        engine.coach(request(strokes[1].tolist(), expected_template_index=1))


def test_geometry_mode_rejects_invalid_points_in_accepted_history():
    strokes = template_strokes()
    engine = FastCoachEngine(lambda _char: strokes)

    with pytest.raises(InvalidStroke, match="canvas bounds"):
        engine.coach(
            request(
                strokes[1].tolist(),
                accepted_strokes=[[[-1.0, 0.2], [0.4, 0.2]]],
                expected_template_index=1,
            )
        )
