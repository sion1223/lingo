from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scorer.build_confusion_graph import build_graph
from scorer.confusions import (
    EVIDENCE_CODES,
    ConfusionRegistryError,
    load_confusion_registry,
    mine_template_neighbors,
    template_distance,
)
from scorer.kanjivg import load_char

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "configs" / "confusions" / "kana_seed_v1.yaml"
SCHEMA = ROOT / "configs" / "confusions" / "schema.json"
KANJI = ROOT / "kanji"


def test_registry_schema_is_closed_and_seed_covers_both_mandatory_directions():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    registry = load_confusion_registry(REGISTRY)

    assert schema["properties"]["schema_version"]["const"] == registry.schema_version
    assert schema["additionalProperties"] is False
    assert set(schema["$defs"]["evidence_code"]["enum"]) == EVIDENCE_CODES
    assert registry.registry_id == "kana_seed_v1"
    assert len(registry.pairs) == 1
    pair = registry.pairs[0]
    assert pair.pair_id == "hiragana_3044_308a"
    assert pair.characters == ("\u3044", "\u308a")
    assert {
        (direction.target_char, direction.competitor_char)
        for direction in pair.directions
    } == {("\u3044", "\u308a"), ("\u308a", "\u3044")}
    assert all(direction.critical_stroke == 1 for direction in pair.directions)


def test_registry_rejects_unknown_fields(tmp_path):
    raw = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    raw["pairs"][0]["unreviewed_hint"] = "do not accept typos silently"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfusionRegistryError, match="unknown fields"):
        load_confusion_registry(path)


def test_template_distance_is_symmetric_and_neighbor_mining_is_deterministic():
    chars = ("\u3044", "\u308a", "\u306c", "\u3081", "\u306d")
    templates = {char: load_char(KANJI, char) for char in chars}

    assert template_distance(templates["\u3044"], templates["\u3044"]) == pytest.approx(0.0)
    assert template_distance(templates["\u3044"], templates["\u308a"]) == pytest.approx(
        template_distance(templates["\u308a"], templates["\u3044"])
    )
    first = mine_template_neighbors(templates, top_k=4)
    second = mine_template_neighbors(templates, top_k=4)
    assert first == second
    assert "\u308a" in {item.char for item in first["\u3044"]}
    assert "\u3044" in {item.char for item in first["\u308a"]}


def test_registry_scope_graph_records_seeded_pair_rank_and_hash():
    first = build_graph(
        kanji_dir=KANJI,
        registry_path=REGISTRY,
        scope="registry",
        top_k=10,
    )
    second = build_graph(
        kanji_dir=KANJI,
        registry_path=REGISTRY,
        scope="registry",
        top_k=10,
    )

    assert first["character_count"] == 2
    assert first["parse_errors"] == []
    assert [item["rank_within_top_k"] for item in first["seeded_pair_ranks"]] == [1, 1]
    assert first["graph_sha256"] == second["graph_sha256"]
