"""Regression tests for the public POST /score contract."""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from scorer import server


def _fake_report(raw_strokes: list[np.ndarray]) -> dict:
    user = [np.asarray(stroke, dtype=np.float64) for stroke in raw_strokes]
    return {
        "score": 91.5,
        "base_model_score": 91.5,
        "strokes": [
            {
                "index": index,
                "template_index": index,
                "q": 0.915,
                "rev_prob": 0.01,
                "ord_prob": 0.02,
                "pos_err": np.float64(0.01),
                "shape_err": np.float64(0.02),
                "gain": 0.0,
                "messages": ["잘 썼습니다"],
            }
            for index in range(len(user))
        ],
        "missing": [],
        "extra": [],
        "match": list(range(len(user))),
        "corrections": [],
        "user": user,
    }


def test_score_accepts_legacy_xy_arrays_and_preserves_response_contract(monkeypatch):
    captured: dict[str, object] = {}
    template = [np.asarray([[0.2, 0.2], [0.8, 0.8]], dtype=np.float64)]

    monkeypatch.setattr(server, "get_model", lambda: object())
    monkeypatch.setattr(server, "char_to_file", lambda _directory, _char: "fixture.svg")
    monkeypatch.setattr(server.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(server, "load_char", lambda _directory, _char: template)

    def analyze(_model, _template, raw_strokes):
        captured["strokes"] = raw_strokes
        return _fake_report(raw_strokes)

    monkeypatch.setattr(server, "analyze_chandra", analyze)

    client = TestClient(server.app)
    response = client.post(
        "/score",
        json={"char": "永", "strokes": [[[0.2, 0.2], [0.8, 0.8]]]},
    )

    assert response.status_code == 200
    body = response.json()
    assert {
        "char",
        "score",
        "base_model_score",
        "elapsed",
        "template",
        "user",
        "strokes",
        "missing",
        "extra",
        "match",
        "corrections",
    } <= body.keys()
    assert body["char"] == "永"
    assert body["template"] == [[[0.2, 0.2], [0.8, 0.8]]]
    assert body["user"] == [[[0.2, 0.2], [0.8, 0.8]]]
    assert isinstance(body["strokes"][0]["pos_err"], float)
    assert np.asarray(captured["strokes"][0]).shape == (2, 2)


def test_score_rejects_non_finite_legacy_coordinates(monkeypatch):
    monkeypatch.setattr(server, "get_model", lambda: object())
    monkeypatch.setattr(server, "char_to_file", lambda _directory, _char: "fixture.svg")
    monkeypatch.setattr(server.os.path, "exists", lambda _path: True)

    client = TestClient(server.app)
    response = client.post(
        "/score",
        content='{"char":"永","strokes":[[[0.2,0.2],[1e999,0.8]]]}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "좌표는 유한한 숫자여야 합니다"

