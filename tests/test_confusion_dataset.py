from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from scorer.confusion_dataset import (
    CONFUSION_SAMPLE_SCHEMA_VERSION,
    ConfusionSample,
    ConfusionSampleError,
    confusion_samples_from_fixtures,
)
from scorer.confusions import (
    EVIDENCE_CODES,
    generate_confusion_fixtures,
    load_confusion_registry,
)
from scorer.evaluate_confusions import LoadedBackend, evaluate_backend
from scorer.kanjivg import load_char

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs" / "confusions" / "kana_seed_v1.yaml"
SAMPLE_SCHEMA = ROOT / "configs" / "confusions" / "sample.schema.json"
KANJI = ROOT / "kanji"


def _stroke(*points: tuple[float, float]) -> np.ndarray:
    return np.asarray(points, dtype=np.float64)


def _sample(**overrides) -> ConfusionSample:
    values = {
        "sample_id": "hiragana_3044_308a__unit_001",
        "split": "train",
        "kind": "full_competitor",
        "user_strokes": (
            _stroke((0.20, 0.10), (0.25, 0.80)),
            _stroke((0.70, 0.15), (0.65, 0.90)),
        ),
        "target_template": (
            _stroke((0.15, 0.20), (0.30, 0.75)),
            _stroke((0.75, 0.30), (0.68, 0.70)),
        ),
        "written_char": "り",
        "target_char": "い",
        "competitor_char": "り",
        "is_target": False,
        "quality_for_written_char": 0.93,
        "target_match": 0.04,
        "pair_id": "hiragana_3044_308a",
        "critical_strokes": (1,),
        "evidence_labels": ("STROKE_TOO_VERTICAL", "STROKE_TOO_LONG"),
        "ambiguity": False,
        "seed": 17,
        "morph_alpha": None,
    }
    values.update(overrides)
    return ConfusionSample(**values)


def test_common_sample_keeps_identity_and_quality_as_separate_labels():
    sample = _sample()

    assert sample.schema_version == CONFUSION_SAMPLE_SCHEMA_VERSION
    assert sample.label == "competitor"
    assert sample.is_target is False
    assert sample.quality_for_written_char == pytest.approx(0.93)
    assert sample.target_match == pytest.approx(0.04)

    manifest = sample.to_manifest()
    assert manifest["written_char"] == "り"
    assert manifest["target_char"] == "い"
    assert manifest["competitor_char"] == "り"
    assert manifest["is_target"] is False
    assert manifest["ambiguity"] is False
    assert manifest["critical_strokes"] == [1]
    assert manifest["user_strokes"][0][0] == [0.2, 0.1]

    restored = ConfusionSample.from_manifest(manifest)
    assert restored.to_manifest() == manifest


def test_common_sample_manifest_parser_rejects_unknown_or_stale_contracts():
    manifest = _sample().to_manifest()
    manifest["unexpected"] = True
    with pytest.raises(ConfusionSampleError, match="unknown fields"):
        ConfusionSample.from_manifest(manifest)

    manifest = _sample().to_manifest()
    manifest["schema_version"] = "confusion_sample.v0"
    with pytest.raises(ConfusionSampleError, match="unsupported"):
        ConfusionSample.from_manifest(manifest)


def test_common_sample_owns_immutable_finite_stroke_arrays():
    source = _stroke((0.20, 0.10), (0.25, 0.80))
    sample = _sample(user_strokes=(source, _stroke((0.7, 0.1), (0.6, 0.9))))

    source[0, 0] = 999
    assert sample.user_strokes[0][0, 0] == pytest.approx(0.20)
    assert not sample.user_strokes[0].flags.writeable
    with pytest.raises(ValueError):
        sample.user_strokes[0][0, 0] = 0.5
    with pytest.raises(ValueError):
        sample.user_strokes[0].setflags(write=True)

    with pytest.raises(ConfusionSampleError, match="finite"):
        _sample(user_strokes=(_stroke((0.0, 0.0), (np.nan, 1.0)),))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"written_char": None}, "resolved sample"),
        ({"is_target": True}, "written_char must equal target_char"),
        ({"ambiguity": True}, "ambiguous sample"),
        (
            {
                "ambiguity": True,
                "written_char": None,
                "is_target": None,
                "quality_for_written_char": None,
                "morph_alpha": None,
            },
            "AMBIGUOUS_BETWEEN_CHARACTERS",
        ),
        ({"target_char": "い", "competitor_char": "い"}, "must differ"),
        ({"critical_strokes": (2,)}, "outside target_template"),
        ({"evidence_labels": ("NOT_A_REAL_CODE",)}, "unknown evidence"),
    ],
)
def test_common_sample_rejects_contradictory_or_incomplete_labels(overrides, message):
    with pytest.raises(ConfusionSampleError, match=message):
        _sample(**overrides)


def test_ambiguous_sample_has_no_forced_character_identity():
    sample = _sample(
        kind="morph_050",
        written_char=None,
        is_target=None,
        quality_for_written_char=None,
        target_match=None,
        evidence_labels=("AMBIGUOUS_BETWEEN_CHARACTERS",),
        ambiguity=True,
        morph_alpha=0.5,
    )

    assert sample.label == "ambiguous"
    assert sample.written_char is None
    assert sample.is_target is None


def test_fixture_adapter_applies_one_contract_to_every_confusion_case():
    registry = load_confusion_registry(REGISTRY_PATH)
    fixtures = generate_confusion_fixtures(registry, lambda char: load_char(KANJI, char))
    samples = confusion_samples_from_fixtures(
        fixtures,
        registry,
        lambda char: load_char(KANJI, char),
    )

    assert len(samples) == len(fixtures) == 192
    assert len({sample.sample_id for sample in samples}) == len(samples)
    assert Counter(sample.label for sample in samples) == {
        "target": 32,
        "competitor": 64,
        "ambiguous": 96,
    }
    for fixture, sample in zip(fixtures, samples):
        assert sample.sample_id == fixture.fixture_id
        assert sample.pair_id == fixture.pair_id
        assert sample.target_char == fixture.target_char
        assert sample.competitor_char == fixture.competitor_char
        assert sample.label == fixture.label
        assert sample.critical_strokes == (fixture.critical_stroke,)
        assert sample.target_template
        if sample.ambiguity:
            assert sample.evidence_labels == ("AMBIGUOUS_BETWEEN_CHARACTERS",)
        elif sample.is_target:
            assert sample.evidence_labels == ()
        else:
            assert sample.evidence_labels


def test_baseline_evaluator_consumes_the_common_sample_contract():
    sample = _sample()
    competitor_template = (
        _stroke((0.10, 0.10), (0.20, 0.85)),
        _stroke((0.80, 0.10), (0.75, 0.90)),
    )

    def score(_user_strokes, template):
        return 0.2 if np.array_equal(template[0], sample.target_template[0]) else 0.8

    result = evaluate_backend(
        LoadedBackend(name="contract_probe", score=score, metadata={}),
        (sample,),
        {"り": competitor_template},
        threshold=0.5,
        threshold_grid=(0.5,),
    )

    prediction = result["predictions"][0]
    assert prediction["fixture_id"] == sample.sample_id
    assert prediction["label"] == "competitor"
    assert prediction["target_score"] == pytest.approx(0.2)
    assert prediction["competitor_score"] == pytest.approx(0.8)


def test_common_sample_json_schema_is_closed_and_requires_pair_metadata():
    schema = json.loads(SAMPLE_SCHEMA.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == (
        CONFUSION_SAMPLE_SCHEMA_VERSION
    )
    assert set(_sample().to_manifest()) == set(schema["required"])
    assert {
        "user_strokes",
        "target_template",
        "written_char",
        "target_char",
        "competitor_char",
        "is_target",
        "quality_for_written_char",
        "target_match",
        "pair_id",
        "critical_strokes",
        "evidence_labels",
        "ambiguity",
    } <= set(schema["required"])
    assert set(schema["$defs"]["evidence_code"]["enum"]) == EVIDENCE_CODES
