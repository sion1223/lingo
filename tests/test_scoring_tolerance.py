from __future__ import annotations

import numpy as np
import torch

from scorer.chandra_scorer import analyze_chandra
from scorer.feedback import match_strokes
from scorer.kanjivg import normalize_strokes
from scorer.synth import compute_labels


def _line(start, end):
    return np.linspace(start, end, 12, dtype=np.float64)


class _SequencedFakeModel:
    """Return a pessimistic first score and optimistic counterfactual scores."""

    def __init__(self, first_score=0.25, counterfactual_score=0.9):
        self.first_score = first_score
        self.counterfactual_score = counterfactual_score
        self.calls = 0

    def eval(self):
        return self

    def score_strokes(self, user, _template):
        self.calls += 1
        score = self.first_score if self.calls == 1 else self.counterfactual_score
        count = len(user)
        return {
            "overall": torch.tensor([score]),
            "q": torch.full((1, count), 0.9),
            "rev_logit": torch.full((1, count), -8.0),
            "ord_logit": torch.full((1, count), -8.0),
        }


def test_same_shape_is_matched_even_when_its_position_is_shifted():
    template = [_line((0.1, 0.2), (0.8, 0.2))]
    shifted = [_line((0.1, 0.65), (0.8, 0.65))]

    match, missing = match_strokes(shifted, template)

    assert match.tolist() == [0]
    assert missing == []


def test_training_labels_make_shape_more_important_than_position():
    template = [_line((0.1, 0.2), (0.8, 0.2))]
    shifted = [_line((0.1, 0.3), (0.8, 0.3))]

    labels = compute_labels(
        shifted,
        template,
        perm=np.array([0]),
        reversed_flags=np.array([False]),
    )

    assert float(labels["overall"]) >= 0.75


def test_final_score_rescues_correct_form_when_model_is_position_sensitive():
    template = normalize_strokes([
        _line((0.1, 0.2), (0.8, 0.2)),
        _line((0.8, 0.2), (0.8, 0.9)),
    ])
    shifted = [stroke + np.array([0.08, 0.04]) for stroke in template]

    report = analyze_chandra(_SequencedFakeModel(), template, shifted)

    assert report["score"] >= 75.0
    assert report["shape_score"] >= 90.0
    assert report["score_policy"] == "shape_tolerant_v1"


def test_recall_score_ignores_global_start_position_and_uniform_character_size():
    template = normalize_strokes([
        _line((0.1, 0.2), (0.8, 0.2)),
        _line((0.8, 0.2), (0.8, 0.9)),
    ])
    moved_and_scaled = [stroke * 0.52 + np.array([0.34, 0.31]) for stroke in template]

    report = analyze_chandra(
        _SequencedFakeModel(first_score=0.25),
        template,
        moved_and_scaled,
        mode="recall",
    )

    assert report["score"] >= 90.0
    assert report["shape_score"] >= 99.0
    assert report["score_policy"] == "recall_shape_only_v1"
    assert report["score_components"]["position_weight"] == 0.0
    assert all(
        correction.get("error_code") != "POSITION_OFFSET"
        for correction in report["corrections"]
    )


def test_well_formed_stroke_is_not_listed_as_a_correction_from_gain_alone():
    template = normalize_strokes([_line((0.1, 0.2), (0.8, 0.2))])

    report = analyze_chandra(_SequencedFakeModel(), template, template)

    assert report["strokes"][0]["messages"] == ["잘 썼습니다"]
    assert report["corrections"] == []


def test_shape_tolerance_does_not_turn_a_different_form_into_a_high_score():
    template = normalize_strokes([
        _line((0.1, 0.2), (0.8, 0.2)),
        _line((0.8, 0.2), (0.8, 0.9)),
    ])
    different_form = normalize_strokes([
        _line((0.1, 0.1), (0.9, 0.9)),
        _line((0.9, 0.1), (0.1, 0.9)),
    ])

    report = analyze_chandra(
        _SequencedFakeModel(first_score=0.95, counterfactual_score=0.95),
        template,
        different_form,
    )

    assert report["shape_score"] < 60.0
    assert report["score"] < 70.0
    assert report["corrections"]
