from __future__ import annotations

from scripts.validate_teacher_feedback import (
    MUTATIONS,
    _estimated_cost,
    run_synthetic,
)


def test_synthetic_teacher_gate_reports_full_contract_coverage():
    report = run_synthetic(32)

    assert report["schema_or_fallback"]["rate"] == 1.0
    assert report["locked_decision_preserved"]["rate"] == 1.0
    assert report["provider_failure_fallback"]["rate"] == 1.0
    for mutation in MUTATIONS:
        assert report["unsafe_mutations_rejected"][mutation]["rate"] == 1.0


def test_live_cost_estimate_separates_cached_input_tokens():
    assert _estimated_cost(
        {
            "input_tokens": 1_000,
            "cached_input_tokens": 200,
            "output_tokens": 100,
        }
    ) == 0.00142


def test_small_synthetic_run_does_not_divide_by_missing_mutation_trials():
    report = run_synthetic(1)

    assert report["unsafe_mutations_rejected"]["decision_id"]["rate"] == 1.0
    assert report["unsafe_mutations_rejected"]["error_code"]["rate"] is None
