from __future__ import annotations

from fastapi.testclient import TestClient

from scorer import server


def _attempt_payload():
    return {
        "protocol_version": 1,
        "session_id": "session-anonymous",
        "attempt_id": "attempt-1",
        "attempt_revision": 3,
        "char": "語",
        "mode": "recall",
        "ended_reason": "scored",
        "started_at": "2026-08-21T12:00:00.000Z",
        "ended_at": "2026-08-21T12:00:02.000Z",
        "strokes": [[
            {
                "x": 0.2,
                "y": 0.3,
                "t": 0,
                "pressure": 0.41,
                "tiltX": 3,
                "tiltY": -2,
                "pointerType": "pen",
            },
            {
                "x": 0.4,
                "y": 0.6,
                "t": 45,
                "pressure": 0.57,
                "tiltX": 4,
                "tiltY": -1,
                "pointerType": "pen",
            },
        ]],
        "stroke_results": [{
            "stroke_index": 0,
            "matched_template_index": 0,
            "accepted": True,
            "error_code": None,
            "confidence": 0.96,
            "intervention": "silent",
        }],
        "final_score": 88.5,
        "client_version": "web-v1",
    }


def test_attempt_endpoint_preserves_rich_points_and_stroke_order(monkeypatch):
    stored = []
    monkeypatch.setattr(server, "record_attempt_event", stored.append, raising=False)

    response = TestClient(server.app).post("/attempt/events", json=_attempt_payload())

    assert response.status_code == 202
    assert response.json()["stored"] is True
    assert len(stored) == 1
    event = stored[0].model_dump(mode="json")
    assert event["strokes"][0][1]["t"] == 45
    assert event["strokes"][0][1]["pressure"] == 0.57
    assert event["stroke_results"][0]["stroke_index"] == 0
