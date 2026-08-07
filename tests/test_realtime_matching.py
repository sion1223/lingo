from __future__ import annotations

import numpy as np

from scorer.realtime import causal_match


def test_causal_match_prefers_the_expected_stroke():
    horizontal = np.linspace((0.1, 0.2), (0.9, 0.2), 12)
    vertical = np.linspace((0.8, 0.2), (0.8, 0.9), 12)

    match = causal_match(horizontal, [horizontal, vertical], expected_index=0)

    assert match.matched_template_index == 0
    assert match.wrong_order is False
    assert match.extra_stroke is False


def test_causal_match_detects_only_the_immediate_next_stroke():
    horizontal = np.linspace((0.1, 0.2), (0.9, 0.2), 12)
    vertical = np.linspace((0.8, 0.2), (0.8, 0.9), 12)
    diagonal = np.linspace((0.2, 0.8), (0.8, 0.3), 12)

    match = causal_match(vertical, [horizontal, vertical, diagonal], expected_index=0)

    assert match.matched_template_index == 1
    assert match.wrong_order is True


def test_causal_match_marks_input_after_template_as_extra():
    stroke = np.linspace((0.1, 0.2), (0.9, 0.2), 12)

    match = causal_match(stroke, [stroke], expected_index=1)

    assert match.matched_template_index is None
    assert match.extra_stroke is True
    assert match.metrics is None
