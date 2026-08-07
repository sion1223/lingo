from __future__ import annotations

import json

import numpy as np
from fastapi.testclient import TestClient

from scorer import server
from scorer.realtime import FastCoachEngine


def payload():
    return {
        "protocol_version": 1,
        "request_id": "http-request-1",
        "session_id": "session-1",
        "attempt_id": "attempt-1",
        "attempt_revision": 5,
        "char": "永",
        "mode": "trace",
        "accepted_strokes": [],
        "current_stroke": [[0.1, 0.2], [0.9, 0.2]],
        "expected_template_index": 0,
        "client_metrics": {"path_error": 0.0, "direction_cosine": 1.0},
    }


def test_coach_stroke_http_contract_supports_legacy_points(monkeypatch):
    template = [np.linspace((0.1, 0.2), (0.9, 0.2), 12)]
    engine = FastCoachEngine(lambda _char: template)
    monkeypatch.setattr(server, "get_coach_engine", lambda: engine)
    client = TestClient(server.app)

    response = client.post("/coach/stroke", json=payload())

    assert response.status_code == 200
    body = response.json()
    assert body["protocol_version"] == 1
    assert body["request_id"] == "http-request-1"
    assert body["attempt_revision"] == 5
    assert body["engine"] == "geometry-only"
    assert body["accepted"] is True
    assert body["primary_cue"] is None
    assert body["next_action"]["type"] == "complete"


def test_coach_contract_rejects_non_finite_rich_points():
    client = TestClient(server.app)
    body = payload()
    body["current_stroke"] = [{"x": 0.2, "y": 0.8, "t": 0}]
    raw = json.dumps(body).replace('"y": 0.8', '"y": 1e999')

    response = client.post(
        "/coach/stroke", content=raw, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_health_reports_coach_and_deep_readiness_separately(monkeypatch):
    class GeometryEngine:
        mode = "geometry-only"

    monkeypatch.setattr(server, "_coach_engine", GeometryEngine())
    monkeypatch.setattr(server, "_model", None)
    monkeypatch.setattr(server, "_deep_loading", True)
    client = TestClient(server.app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["protocol_version"] == 1
    assert body["build_sha"] == server.BUILD_SHA
    assert body["coach_ready"] is True
    assert body["coach_engine"] == "geometry-only"
    assert body["deep_score_ready"] is False
    assert body["deep_model_loading"] is True


def test_explicit_geometry_only_mode_does_not_load_the_stroke_model(monkeypatch):
    monkeypatch.setattr(server, "_coach_engine", None)
    monkeypatch.setattr(server, "COACH_ENGINE_MODE", "geometry-only")

    def unexpected_load():
        raise AssertionError("geometry-only mode must not load the checkpoint")

    monkeypatch.setattr(server, "get_stroke_model", unexpected_load)

    engine = server.get_coach_engine()

    assert engine.mode == "geometry-only"


def test_internal_coach_failure_returns_safe_message(monkeypatch):
    class BrokenEngine:
        mode = "geometry-only"

        def coach(self, _request):
            raise RuntimeError("private model path C:/secret/checkpoint.pt")

    monkeypatch.setattr(server, "get_coach_engine", lambda: BrokenEngine())
    client = TestClient(server.app)

    response = client.post("/coach/stroke", json=payload())

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "COACH_FAILED",
        "message": "획을 분석하지 못했습니다",
    }
    assert "checkpoint" not in response.text
