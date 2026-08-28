"""Shared, shape-tolerant final-score and correction policy.

The learned score remains visible as ``base_model_score``. The public task
score combines it with a whole-character form comparison that removes one
global translation, so a correctly shaped character is not failed merely for
being written off-centre. Stroke layout, missing/extra strokes, direction and
order remain part of the result.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import torch

from .synth import stroke_errors

SCORE_POLICY = "shape_tolerant_v1"
MODEL_WEIGHT = 0.22
SHAPE_WEIGHT = 0.68
POSITION_WEIGHT = 0.10
SHAPE_ERROR_SCALE = 6.0
POSITION_ERROR_SCALE = 2.0
SHAPE_CORRECTION_THRESHOLD = 0.07
POSITION_CORRECTION_THRESHOLD = 0.12
DIRECTION_PENALTY = 0.18
ORDER_PENALTY = 0.12


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    base_model_score: float
    shape_score: float
    position_score: float
    structure_factor: float
    technique_factor: float


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _as_numpy(value) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        value = value.detach().float().cpu().numpy()
    return np.asarray(value, dtype=np.float64)


def _probabilities(logits) -> np.ndarray:
    values = _as_numpy(logits)
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))


def _model_arrays(output: dict, stroke_count: int):
    q = _as_numpy(output["q"])[0][:stroke_count]
    reverse = _probabilities(output["rev_logit"])[0][:stroke_count]
    order = _probabilities(output["ord_logit"])[0][:stroke_count]
    return q, reverse, order


def _oriented_pair(user: np.ndarray, template: np.ndarray):
    user_centered = user - user.mean(axis=0)
    template_centered = template - template.mean(axis=0)
    forward = np.linalg.norm(user_centered - template_centered, axis=1).mean()
    reversed_error = np.linalg.norm(
        user_centered[::-1] - template_centered,
        axis=1,
    ).mean()
    return (user[::-1] if reversed_error < forward else user), reversed_error < forward


def compute_score_breakdown(
    *,
    base_score: float,
    user: Sequence[np.ndarray],
    template: Sequence[np.ndarray],
    match: Sequence[int],
    missing: Sequence[int],
    extra: Sequence[int],
) -> ScoreBreakdown:
    """Blend learned evidence with translation-invariant whole-form geometry."""
    aligned_user = []
    aligned_template = []
    position_errors = []
    reversed_count = 0
    order_error_count = 0
    for user_index, template_index_value in enumerate(match):
        template_index = int(template_index_value)
        if template_index < 0:
            continue
        user_stroke = np.asarray(user[user_index], dtype=np.float64)
        template_stroke = np.asarray(template[template_index], dtype=np.float64)
        oriented, looks_reversed = _oriented_pair(user_stroke, template_stroke)
        aligned_user.append(oriented)
        aligned_template.append(template_stroke)
        position_errors.append(
            float(np.linalg.norm(user_stroke.mean(0) - template_stroke.mean(0)))
        )
        reversed_count += int(looks_reversed)
        order_error_count += int(user_index != template_index)

    matched_count = len(aligned_user)
    if matched_count:
        user_points = np.concatenate(aligned_user)
        template_points = np.concatenate(aligned_template)
        translation = template_points.mean(0) - user_points.mean(0)
        form_error = float(
            np.linalg.norm(user_points + translation - template_points, axis=1).mean()
        )
        position_error = float(np.mean(position_errors))
        shape_quality = float(np.exp(-SHAPE_ERROR_SCALE * form_error))
        position_quality = float(np.exp(-POSITION_ERROR_SCALE * position_error))
    else:
        shape_quality = 0.0
        position_quality = 0.0

    template_count = len(template)
    coverage = (template_count - len(missing)) / max(template_count, 1)
    precision = (len(user) - len(extra)) / max(len(user), 1)
    structure_factor = _clamp(coverage * precision)
    reverse_ratio = reversed_count / max(matched_count, 1)
    order_ratio = order_error_count / max(matched_count, 1)
    technique_factor = (
        (1.0 - DIRECTION_PENALTY * reverse_ratio)
        * (1.0 - ORDER_PENALTY * order_ratio)
    )
    base = _clamp(base_score)
    blended = (
        SHAPE_WEIGHT * shape_quality
        + MODEL_WEIGHT * base
        + POSITION_WEIGHT * position_quality
    )
    task_score = _clamp(blended * structure_factor * technique_factor)
    return ScoreBreakdown(
        score=task_score,
        base_model_score=base,
        shape_score=_clamp(shape_quality),
        position_score=_clamp(position_quality),
        structure_factor=structure_factor,
        technique_factor=technique_factor,
    )


def _direction_text(move: Sequence[float]) -> str:
    dx, dy = move
    horizontal = "오른쪽" if dx > 0.02 else "왼쪽" if dx < -0.02 else ""
    vertical = "아래" if dy > 0.02 else "위" if dy < -0.02 else ""
    return "·".join(filter(None, (horizontal, vertical))) or "표시된 방향"


def _stroke_issue(
    *,
    user_index: int,
    template_index: int,
    position_error: float,
    shape_error: float,
    looks_reversed: bool,
    move: Sequence[float],
):
    if looks_reversed:
        return (
            "DIRECTION_REVERSED",
            "필순 방향이 반대입니다 — 반대쪽 끝에서 시작하세요",
            100,
            "major",
        )
    if user_index != template_index:
        return (
            "WRONG_ORDER",
            f"획 순서 오류 — 이 획은 {template_index + 1}번째로 써야 합니다",
            95,
            "major",
        )
    if shape_error > SHAPE_CORRECTION_THRESHOLD:
        return (
            "PATH_DEVIATION",
            "모양이 다른 구간을 점선 정답 궤적에 가깝게 다듬어 보세요",
            75,
            "minor" if shape_error < 0.14 else "major",
        )
    if position_error > POSITION_CORRECTION_THRESHOLD:
        return (
            "POSITION_OFFSET",
            f"형태는 유지하고 획 위치만 {_direction_text(move)}으로 옮겨 보세요",
            35,
            "minor",
        )
    return None


def build_final_report(
    *,
    output: dict,
    user: Sequence[np.ndarray],
    template: Sequence[np.ndarray],
    match: Sequence[int],
    missing: Sequence[int],
    score_once: Callable[[Sequence[np.ndarray], Sequence[np.ndarray]], dict],
    top_k: int = 3,
) -> dict:
    """Create one consistent score/correction report for both scorer backends."""
    match_array = np.asarray(match, dtype=int)
    missing_list = [int(index) for index in missing]
    extra = [index for index, value in enumerate(match_array) if value < 0]
    base = float(_as_numpy(output["overall"])[0])
    q, reverse_probability, order_probability = _model_arrays(output, len(user))
    breakdown = compute_score_breakdown(
        base_score=base,
        user=user,
        template=template,
        match=match_array,
        missing=missing_list,
        extra=extra,
    )

    strokes_report = []
    correction_candidates = []
    for user_index in range(len(user)):
        template_index = int(match_array[user_index])
        entry = {
            "index": user_index,
            "template_index": template_index,
            "q": float(q[user_index]),
            "rev_prob": float(reverse_probability[user_index]),
            "ord_prob": float(order_probability[user_index]),
        }
        if template_index < 0:
            entry.update(
                gain=0.0,
                error_code="EXTRA_STROKE",
                severity="major",
                messages=["불필요한(대응 없는) 획입니다 — 지우세요"],
            )
            correction_candidates.append((110, entry))
            strokes_report.append(entry)
            continue

        position_error, shape_error, looks_reversed = stroke_errors(
            user[user_index],
            template[template_index],
        )
        move = (template[template_index].mean(0) - user[user_index].mean(0)).tolist()
        issue = _stroke_issue(
            user_index=user_index,
            template_index=template_index,
            position_error=position_error,
            shape_error=shape_error,
            looks_reversed=looks_reversed,
            move=move,
        )
        entry.update(
            pos_err=position_error,
            shape_err=shape_error,
            move=move,
            error_code=issue[0] if issue else None,
            severity=issue[3] if issue else "none",
            messages=[issue[1]] if issue else ["잘 썼습니다"],
            gain=0.0,
        )
        if issue:
            fixed = list(user)
            fixed[user_index] = np.asarray(template[template_index]).copy()
            counterfactual = score_once(fixed, template)
            counterfactual_breakdown = compute_score_breakdown(
                base_score=float(_as_numpy(counterfactual["overall"])[0]),
                user=fixed,
                template=template,
                match=match_array,
                missing=missing_list,
                extra=extra,
            )
            entry["gain"] = max(
                0.0,
                (counterfactual_breakdown.score - breakdown.score) * 100,
            )
            correction_candidates.append((issue[2], entry))
        strokes_report.append(entry)

    for template_index in missing_list:
        correction_candidates.append((
            120,
            {
                "index": -1,
                "template_index": template_index,
                "gain": None,
                "error_code": "MISSING_STROKE",
                "severity": "major",
                "messages": [f"{template_index + 1}번째 획이 빠졌습니다 — 추가하세요"],
            },
        ))

    correction_candidates.sort(
        key=lambda item: (item[0], item[1].get("gain") or 0.0),
        reverse=True,
    )
    corrections = [entry for _priority, entry in correction_candidates[:top_k]]
    return {
        "score": round(breakdown.score * 100, 1),
        "base_model_score": round(breakdown.base_model_score * 100, 1),
        "shape_score": round(breakdown.shape_score * 100, 1),
        "position_score": round(breakdown.position_score * 100, 1),
        "score_policy": SCORE_POLICY,
        "score_components": {
            "model_weight": MODEL_WEIGHT,
            "shape_weight": SHAPE_WEIGHT,
            "position_weight": POSITION_WEIGHT,
            "structure_factor": round(breakdown.structure_factor, 4),
            "technique_factor": round(breakdown.technique_factor, 4),
        },
        "strokes": strokes_report,
        "missing": missing_list,
        "extra": extra,
        "match": match_array.tolist(),
        "corrections": corrections,
        "user": user,
    }
