"""Unit tests for the aggregate-only remote validation report helpers."""

from scripts.validate_runpod import HttpResult, latency_summary, percentile


def test_percentile_interpolates_sorted_latency_samples():
    assert percentile([40.0, 10.0, 30.0, 20.0], 0.5) == 25.0
    assert percentile([], 0.95) is None


def test_latency_summary_keeps_aggregates_without_response_bodies():
    results = [
        HttpResult(200, {"private": "must-not-be-copied"}, 10.0),
        HttpResult(200, {"raw_strokes": [[1, 2]]}, 20.0),
        HttpResult(503, {"detail": "upstream"}, 30.0),
        HttpResult(None, None, 3000.0, timed_out=True, network_error="TimeoutError"),
    ]

    summary = latency_summary(results)

    assert summary["requests"] == 4
    assert summary["successful"] == 2
    assert summary["p50_ms"] == 15.0
    assert summary["status_counts"] == {"200": 2, "503": 1, "timeout": 1}
    assert "private" not in str(summary)
    assert "raw_strokes" not in str(summary)
