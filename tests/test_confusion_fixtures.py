from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np

from scorer.confusions import (
    fixture_content_sha256,
    fixture_seed_sha256,
    generate_confusion_fixtures,
    load_confusion_registry,
)
from scorer.kanjivg import load_char, resample_stroke

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs" / "confusions" / "kana_seed_v1.yaml"
KANJI = ROOT / "kanji"


def _load(char):
    return load_char(KANJI, char)


def test_fixture_suite_is_deterministic_and_has_declared_case_balance():
    registry = load_confusion_registry(REGISTRY_PATH)
    first = generate_confusion_fixtures(registry, _load)
    second = generate_confusion_fixtures(registry, _load)

    assert len(first) == 192
    assert len({fixture.fixture_id for fixture in first}) == len(first)
    assert Counter(fixture.kind for fixture in first) == {
        "clean_target": 32,
        "full_competitor": 32,
        "critical_transplant": 32,
        "morph_025": 32,
        "morph_050": 32,
        "morph_075": 32,
    }
    assert Counter(fixture.label for fixture in first) == {
        "target": 32,
        "competitor": 64,
        "ambiguous": 96,
    }
    assert fixture_seed_sha256(first) == (
        "e9d00b4d8f633150b0a17df0784f08d87aaca3359f0ff96a0e2e786da67414e7"
    )
    assert fixture_content_sha256(first) == fixture_content_sha256(second)


def test_fixture_seed_families_do_not_leak_across_splits():
    registry = load_confusion_registry(REGISTRY_PATH)
    split_fixtures = {
        split: generate_confusion_fixtures(registry, _load, split=split)
        for split in ("train", "validation", "test")
    }

    seed_sets = {
        split: {fixture.seed for fixture in fixtures}
        for split, fixtures in split_fixtures.items()
    }
    assert seed_sets["train"].isdisjoint(seed_sets["validation"])
    assert seed_sets["train"].isdisjoint(seed_sets["test"])
    assert seed_sets["validation"].isdisjoint(seed_sets["test"])
    assert len({fixture_content_sha256(value) for value in split_fixtures.values()}) == 3


def test_zero_style_critical_fixture_changes_only_the_declared_stroke():
    registry = load_confusion_registry(REGISTRY_PATH)
    policy = replace(
        registry.fixture_policy,
        samples_per_direction=1,
        style_severity=0.0,
    )
    registry = replace(registry, fixture_policy=policy)
    fixtures = generate_confusion_fixtures(registry, _load)
    fixture = next(
        item
        for item in fixtures
        if item.target_char == "\u3044" and item.kind == "critical_transplant"
    )
    target = _load("\u3044")
    competitor = _load("\u308a")

    for index, stroke in enumerate(fixture.strokes):
        if index == fixture.critical_stroke:
            expected = resample_stroke(competitor[index], len(target[index]))
        else:
            expected = target[index]
        assert np.array_equal(stroke, expected)


def test_unreviewed_morph_ladder_is_ambiguous_in_both_directions():
    registry = load_confusion_registry(REGISTRY_PATH)
    fixtures = generate_confusion_fixtures(registry, _load)
    morphs = [fixture for fixture in fixtures if fixture.morph_alpha is not None]

    assert len(morphs) == 96
    assert all(fixture.label == "ambiguous" for fixture in morphs)
    assert all(fixture.written_char is None for fixture in morphs)
