"""Regression gates for display/scoring separation and reviewed-source integrity."""
import hashlib
import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from scorer.kanjivg import char_to_file, load_char, load_display_paths, parse_svg
from scorer.server import app

ROOT = Path(__file__).resolve().parents[1]
PACK = json.loads((ROOT / "web/data/learning-practice.json").read_text(encoding="utf-8"))


def test_pack_preserves_base_model_templates_and_reviewed_source_hashes():
    for char, entry in PACK["characters"].items():
        base = entry["forms"][0]
        assert Path(char_to_file(ROOT / "kanji", char)).name == base["file"]
        np.testing.assert_allclose(base["strokes"], load_char(ROOT / "kanji", char), atol=0.00000051)
        for form in entry["forms"]:
            path = ROOT / "kanji" / form["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == form["source_sha256"]
            assert len(form["strokes"]) == len(form["display"]["paths"]) == form["stroke_count"]
            assert all(len(stroke) == 12 for stroke in form["strokes"])


def test_curves_and_scoring_share_coordinates_without_reinterpreting_checkpoints():
    for entry in PACK["characters"].values():
        for form in entry["forms"]:
            raw = parse_svg(ROOT / "kanji" / form["file"])
            display = form["display"]
            transformed = [stroke * display["scale"] + display["offset"] for stroke in raw]
            np.testing.assert_allclose(transformed, form["strokes"], atol=0.00000051)


def test_registry_has_only_one_alternate_practice_form_and_no_remote_approval():
    extras = [form for entry in PACK["characters"].values() for form in entry["forms"] if form["id"] != "default"]
    assert [(form["id"], form["usage"]) for form in extras] == [("print-reference", "reference"), ("kaisho-hook", "practice")]
    assert all(form["remote_scoring"] is False for form in extras)


def test_template_api_adds_curves_but_keeps_legacy_strokes_unchanged(monkeypatch):
    from scorer import server
    monkeypatch.setattr(server, "KANJI_DIR", str(ROOT / "kanji"))
    client = TestClient(app)
    body = client.get("/template/木").json()
    np.testing.assert_array_equal(body["strokes"], load_char(ROOT / "kanji", "木"))
    assert body["display"] == load_display_paths(ROOT / "kanji/06728.svg")
    assert client.get("/template/木木").status_code == 404
    assert client.get("/web/data/learning-practice.json").status_code == 200
