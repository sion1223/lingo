from __future__ import annotations

from scorer.evaluate_confusions import (
    average_precision,
    binary_roc_auc,
    expected_calibration_error,
    load_backends,
    summarize_predictions,
)


def _prediction(label, target_score, competitor_score, kind):
    return {
        "label": label,
        "target_score": target_score,
        "competitor_score": competitor_score,
        "margin": target_score - competitor_score,
        "kind": kind,
    }


def test_threshold_free_binary_metrics_handle_perfect_inverse_and_ties():
    labels = [0, 0, 1, 1]

    assert binary_roc_auc(labels, [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert binary_roc_auc(labels, [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert binary_roc_auc(labels, [0.5, 0.5, 0.5, 0.5]) == 0.5
    assert average_precision(labels, [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert average_precision(labels, [0.5, 0.5, 0.5, 0.5]) == 0.5


def test_calibration_error_is_zero_for_exact_group_frequencies():
    labels = [0, 1, 0, 1]
    scores = [0.5, 0.5, 0.5, 0.5]

    assert expected_calibration_error(labels, scores) == 0.0


def test_summary_keeps_acceptance_and_pairwise_identity_separate():
    predictions = [
        _prediction("target", 0.9, 0.2, "clean_target"),
        _prediction("target", 0.4, 0.2, "clean_target"),
        _prediction("competitor", 0.8, 0.9, "full_competitor"),
        _prediction("competitor", 0.3, 0.7, "critical_transplant"),
        _prediction("ambiguous", 0.55, 0.54, "morph_050"),
    ]

    result = summarize_predictions(predictions, threshold=0.5)

    assert result["target_true_acceptance_rate"] == 0.5
    assert result["competitor_false_acceptance_rate"] == 0.5
    assert result["pairwise_candidate_accuracy"] == 1.0
    assert result["ambiguous_median_absolute_margin"] == 0.01
    assert result["by_fixture_kind"]["full_competitor"][
        "competitor_false_acceptance_rate"
    ] == 1.0


def test_deep_backends_are_explicitly_skipped_without_cuda(monkeypatch, tmp_path):
    monkeypatch.setattr("scorer.evaluate_confusions.torch.cuda.is_available", lambda: False)

    loaded, statuses = load_backends(
        ["chandra"],
        stroke_checkpoint=tmp_path / "stroke.pt",
        chandra_checkpoint=tmp_path / "chandra.pt",
        hybrid_config=tmp_path / "hybrid.json",
    )

    assert loaded == []
    assert statuses["chandra"]["status"] == "skipped"
    assert statuses["chandra"]["reason"] == "cuda_unavailable"
