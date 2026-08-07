from __future__ import annotations

import numpy as np
import pytest

from scorer.realtime import InvalidStroke, compute_stroke_metrics, sanitize_points


def line(start=(0.1, 0.2), end=(0.9, 0.2), points=12):
    return np.linspace(start, end, points, dtype=np.float64)


def test_perfect_stroke_metrics_are_near_zero():
    template = line()
    metrics = compute_stroke_metrics(template, template)

    assert metrics.start_error == pytest.approx(0.0, abs=1e-9)
    assert metrics.end_error == pytest.approx(0.0, abs=1e-9)
    assert metrics.path_error == pytest.approx(0.0, abs=1e-9)
    assert metrics.shape_error == pytest.approx(0.0, abs=1e-9)
    assert metrics.direction_cosine == pytest.approx(1.0)
    assert metrics.length_ratio == pytest.approx(1.0)


def test_metrics_separate_position_shape_direction_and_length():
    template = line()
    shifted = template + np.array([0.08, 0.04])
    reversed_stroke = template[::-1].copy()
    shortened = template[0] + (template - template[0]) * 0.5

    shifted_metrics = compute_stroke_metrics(shifted, template)
    reversed_metrics = compute_stroke_metrics(reversed_stroke, template)
    short_metrics = compute_stroke_metrics(shortened, template)

    assert shifted_metrics.start_error > 0.08
    assert shifted_metrics.shape_error < 1e-8
    assert reversed_metrics.direction_cosine < -0.99
    assert reversed_metrics.looks_reversed is True
    assert short_metrics.length_ratio == pytest.approx(0.5)


def test_legacy_and_rich_points_have_identical_geometry():
    legacy = line().tolist()
    rich = [
        {"x": x, "y": y, "t": index * 16, "pressure": 0.4}
        for index, (x, y) in enumerate(legacy)
    ]

    assert np.array_equal(sanitize_points(legacy), sanitize_points(rich))
    legacy_metrics = compute_stroke_metrics(legacy, legacy)
    rich_metrics = compute_stroke_metrics(rich, legacy)
    assert rich_metrics.path_error == legacy_metrics.path_error


@pytest.mark.parametrize(
    "points",
    [
        [[np.nan, 0.2], [0.4, 0.2]],
        [[0.2, np.inf], [0.4, 0.2]],
        [[-1.0, 0.2], [0.4, 0.2]],
        [],
    ],
)
def test_invalid_points_are_rejected(points):
    with pytest.raises(InvalidStroke):
        sanitize_points(points)


def test_duplicate_and_zero_length_points_are_safe():
    points = [[0.2, 0.2], [0.2, 0.2], [0.2, 0.2]]
    clean = sanitize_points(points)
    metrics = compute_stroke_metrics(points, line())

    assert clean.shape == (1, 2)
    assert metrics.length_ratio == 0.0
    assert np.isfinite(metrics.path_error)


def test_excessive_point_count_is_rejected_before_geometry_work():
    with pytest.raises(InvalidStroke, match="point limit"):
        sanitize_points([[0.2, 0.2]] * 4097)
