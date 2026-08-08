"""Evaluate legacy scorers on deterministic target/competitor fixtures.

The legacy ``overall`` output was trained as writing quality, not calibrated
identity probability.  Reports therefore label threshold and calibration
statistics as proxies and always include the threshold-free pairwise margin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from .chandra_scorer import _score_once, load_chandra_scorer
from .confusions import (
    ConfusionFixture,
    fixture_content_sha256,
    fixture_seed_sha256,
    generate_confusion_fixtures,
    load_confusion_registry,
    template_distance,
)
from .feedback import _to_batch
from .hybrid import HybridScorer, load_stroke_scorer
from .kanjivg import load_char

DEFAULT_BACKENDS = ("template_geometry", "stroke", "chandra", "hybrid")
KNOWN_BACKENDS = frozenset(DEFAULT_BACKENDS)


@dataclass(frozen=True)
class LoadedBackend:
    name: str
    score: Callable[[Sequence[np.ndarray], Sequence[np.ndarray]], float]
    metadata: Mapping[str, object]


def _sha256_file(path: str | Path) -> str | None:
    source = Path(path)
    if not source.is_file():
        return None
    digest = hashlib.sha256()
    with source.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_metadata(path: str | Path) -> dict:
    source = Path(path)
    return {
        "path": source.as_posix(),
        "exists": source.is_file(),
        "size_bytes": source.stat().st_size if source.is_file() else None,
        "sha256": _sha256_file(source),
    }


def _git_source() -> dict:
    def command(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args],
                text=True,
                encoding="utf-8",
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    status = command("status", "--porcelain")
    return {
        "git_sha": command("rev-parse", "HEAD"),
        "branch": command("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_dirty": bool(status) if status is not None else None,
    }


def _environment() -> dict:
    cuda = torch.cuda.is_available()
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "numpy": np.__version__,
        "cuda_available": cuda,
        "cuda_runtime": torch.version.cuda,
        "device": "cpu",
        "gpu_vram_bytes": None,
    }
    if cuda:
        properties = torch.cuda.get_device_properties(0)
        result["device"] = torch.cuda.get_device_name(0)
        result["gpu_vram_bytes"] = int(properties.total_memory)
    return result


def _geometry_score(user, template) -> float:
    return float(math.exp(-4.0 * template_distance(user, template)))


def _stroke_score(model, user, template) -> float:
    try:
        device = next(model.parameters()).device
    except StopIteration:
        device = torch.device("cpu")
    user_batch = tuple(value.to(device) for value in _to_batch(user))
    template_batch = tuple(value.to(device) for value in _to_batch(template))
    with torch.no_grad():
        output = model(user_batch, template_batch)
    return float(output["overall"][0].detach().float().cpu())


def _deep_score(model, user, template) -> float:
    output = _score_once(model, user, template)
    return float(output["overall"][0].detach().float().cpu())


def _load_hybrid_weights(path: str | Path):
    source = Path(path)
    if not source.is_file():
        return 0.35
    value = json.loads(source.read_text(encoding="utf-8"))
    return value.get("weights", value.get("stroke_weight", 0.35))


def load_backends(
    requested: Sequence[str],
    *,
    stroke_checkpoint: str | Path,
    chandra_checkpoint: str | Path,
    hybrid_config: str | Path,
    strict: bool = False,
) -> tuple[list[LoadedBackend], dict[str, dict]]:
    """Load requested backends once and return explicit skipped statuses."""
    unknown = set(requested) - KNOWN_BACKENDS
    if unknown:
        raise ValueError(f"unknown backends: {', '.join(sorted(unknown))}")
    loaded: list[LoadedBackend] = []
    statuses: dict[str, dict] = {}
    stroke_model = None
    vision_model = None

    if "template_geometry" in requested:
        loaded.append(
            LoadedBackend(
                name="template_geometry",
                score=_geometry_score,
                metadata={
                    "score_semantics": "exp(-4 * aligned_template_geometry_distance)",
                    "learned": False,
                },
            )
        )
        statuses["template_geometry"] = {"status": "ready"}

    needs_stroke = bool({"stroke", "hybrid"} & set(requested))
    if needs_stroke:
        try:
            stroke_model = load_stroke_scorer(stroke_checkpoint, device="cpu")
            statuses["stroke"] = {
                "status": "ready",
                "checkpoint": _checkpoint_metadata(stroke_checkpoint),
            }
            if "stroke" in requested:
                loaded.append(
                    LoadedBackend(
                        name="stroke",
                        score=lambda user, template: _stroke_score(
                            stroke_model, user, template
                        ),
                        metadata={
                            "score_semantics": "legacy overall writing-quality sigmoid",
                            "checkpoint": _checkpoint_metadata(stroke_checkpoint),
                        },
                    )
                )
        except Exception as exc:
            statuses["stroke"] = {
                "status": "failed",
                "reason": f"load_failed:{type(exc).__name__}",
                "checkpoint": _checkpoint_metadata(stroke_checkpoint),
            }
            if strict:
                raise

    needs_vision = bool({"chandra", "hybrid"} & set(requested))
    if needs_vision and not torch.cuda.is_available():
        for name in ("chandra", "hybrid"):
            if name in requested:
                statuses[name] = {
                    "status": "skipped",
                    "reason": "cuda_unavailable",
                    "checkpoint": _checkpoint_metadata(chandra_checkpoint),
                }
        if strict:
            raise RuntimeError("CUDA is required for Chandra and hybrid evaluation")
    elif needs_vision:
        try:
            vision_model = load_chandra_scorer(
                chandra_checkpoint, dtype=torch.bfloat16, device="cuda"
            ).eval()
            statuses["chandra"] = {
                "status": "ready",
                "checkpoint": _checkpoint_metadata(chandra_checkpoint),
            }
            if "chandra" in requested:
                loaded.append(
                    LoadedBackend(
                        name="chandra",
                        score=lambda user, template: _deep_score(
                            vision_model, user, template
                        ),
                        metadata={
                            "score_semantics": "legacy overall writing-quality sigmoid",
                            "checkpoint": _checkpoint_metadata(chandra_checkpoint),
                        },
                    )
                )
        except Exception as exc:
            for name in ("chandra", "hybrid"):
                if name in requested:
                    statuses[name] = {
                        "status": "failed",
                        "reason": f"load_failed:{type(exc).__name__}",
                        "checkpoint": _checkpoint_metadata(chandra_checkpoint),
                    }
            if strict:
                raise

    if "hybrid" in requested and vision_model is not None:
        if stroke_model is None:
            statuses["hybrid"] = {
                "status": "failed",
                "reason": "stroke_backend_unavailable",
                "checkpoint": _checkpoint_metadata(chandra_checkpoint),
                "stroke_checkpoint": _checkpoint_metadata(stroke_checkpoint),
            }
            if strict:
                raise RuntimeError("hybrid evaluation requires the stroke checkpoint")
        else:
            weights = _load_hybrid_weights(hybrid_config)
            hybrid = HybridScorer(vision_model, stroke_model, weights).eval()
            statuses["hybrid"] = {
                "status": "ready",
                "checkpoint": _checkpoint_metadata(chandra_checkpoint),
                "stroke_checkpoint": _checkpoint_metadata(stroke_checkpoint),
                "config": _checkpoint_metadata(hybrid_config),
                "weights": hybrid.stroke_weights,
            }
            loaded.append(
                LoadedBackend(
                    name="hybrid",
                    score=lambda user, template: _deep_score(hybrid, user, template),
                    metadata={
                        "score_semantics": "legacy static blend of writing-quality outputs",
                        "checkpoint": _checkpoint_metadata(chandra_checkpoint),
                        "stroke_checkpoint": _checkpoint_metadata(stroke_checkpoint),
                        "config": _checkpoint_metadata(hybrid_config),
                        "weights": hybrid.stroke_weights,
                    },
                )
            )
    return loaded, statuses


def binary_roc_auc(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    labels_array = np.asarray(labels, dtype=np.int64)
    scores_array = np.asarray(scores, dtype=np.float64)
    positives = int(labels_array.sum())
    negatives = len(labels_array) - positives
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(scores_array, kind="mergesort")
    sorted_scores = scores_array[order]
    ranks = np.empty(len(scores_array), dtype=np.float64)
    index = 0
    while index < len(sorted_scores):
        end = index + 1
        while end < len(sorted_scores) and sorted_scores[end] == sorted_scores[index]:
            end += 1
        ranks[order[index:end]] = (index + 1 + end) / 2.0
        index = end
    positive_rank_sum = float(ranks[labels_array == 1].sum())
    return (
        positive_rank_sum - positives * (positives + 1) / 2
    ) / (positives * negatives)


def average_precision(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    labels_array = np.asarray(labels, dtype=np.int64)
    positives = int(labels_array.sum())
    if positives == 0:
        return None
    scores_array = np.asarray(scores, dtype=np.float64)
    order = np.argsort(-scores_array, kind="mergesort")
    ranked_labels = labels_array[order]
    ranked_scores = scores_array[order]
    true_positives = np.cumsum(ranked_labels)
    group_ends = np.flatnonzero(
        np.r_[ranked_scores[1:] != ranked_scores[:-1], True]
    )
    precision = true_positives[group_ends] / (group_ends + 1)
    recall = true_positives[group_ends] / positives
    recall_delta = np.diff(np.r_[0.0, recall])
    return float(np.sum(recall_delta * precision))


def expected_calibration_error(
    labels: Sequence[int], scores: Sequence[float], *, bins: int = 10
) -> float | None:
    if not labels:
        return None
    y = np.asarray(labels, dtype=np.float64)
    probability = np.asarray(scores, dtype=np.float64)
    if np.any((probability < 0) | (probability > 1)):
        raise ValueError("calibration scores must be probabilities in 0..1")
    bucket = np.minimum((probability * bins).astype(int), bins - 1)
    result = 0.0
    for index in range(bins):
        mask = bucket == index
        if not mask.any():
            continue
        result += float(mask.mean()) * abs(
            float(probability[mask].mean()) - float(y[mask].mean())
        )
    return result


def _safe_rate(values: Sequence[bool]) -> float | None:
    return float(np.mean(values)) if values else None


def _round(value: float | None, digits: int = 8) -> float | None:
    return None if value is None else round(float(value), digits)


def summarize_predictions(predictions: Sequence[Mapping], threshold: float) -> dict:
    labeled = [item for item in predictions if item["label"] != "ambiguous"]
    targets = [item for item in labeled if item["label"] == "target"]
    competitors = [item for item in labeled if item["label"] == "competitor"]
    labels = [1 if item["label"] == "target" else 0 for item in labeled]
    target_scores = [float(item["target_score"]) for item in labeled]
    margins = [float(item["margin"]) for item in labeled]
    signed_margins = [
        margin if label == 1 else -margin for label, margin in zip(labels, margins)
    ]
    candidate_correct = [margin > 0 if label == 1 else margin < 0 for label, margin in zip(labels, margins)]
    brier = (
        float(np.mean((np.asarray(target_scores) - np.asarray(labels)) ** 2))
        if labeled
        else None
    )
    ambiguous = [item for item in predictions if item["label"] == "ambiguous"]
    by_kind = {}
    for kind in sorted({str(item["kind"]) for item in predictions}):
        group = [item for item in predictions if item["kind"] == kind]
        negative_group = [item for item in group if item["label"] == "competitor"]
        by_kind[kind] = {
            "count": len(group),
            "competitor_false_acceptance_rate": _round(
                _safe_rate(
                    [float(item["target_score"]) >= threshold for item in negative_group]
                )
            ),
            "median_margin": _round(
                float(np.median([item["margin"] for item in group])) if group else None
            ),
        }
    return {
        "sample_count": len(predictions),
        "labeled_sample_count": len(labeled),
        "target_sample_count": len(targets),
        "competitor_sample_count": len(competitors),
        "ambiguous_sample_count": len(ambiguous),
        "acceptance_threshold": threshold,
        "target_true_acceptance_rate": _round(
            _safe_rate([float(item["target_score"]) >= threshold for item in targets])
        ),
        "competitor_false_acceptance_rate": _round(
            _safe_rate(
                [float(item["target_score"]) >= threshold for item in competitors]
            )
        ),
        "pairwise_candidate_accuracy": _round(_safe_rate(candidate_correct)),
        "pairwise_roc_auc": _round(binary_roc_auc(labels, margins)),
        "pairwise_average_precision": _round(average_precision(labels, margins)),
        "median_signed_margin": _round(
            float(np.median(signed_margins)) if signed_margins else None
        ),
        "target_score_brier_proxy": _round(brier),
        "target_score_ece_proxy": _round(
            expected_calibration_error(labels, target_scores)
        ),
        "ambiguous_median_absolute_margin": _round(
            float(np.median([abs(item["margin"]) for item in ambiguous]))
            if ambiguous
            else None
        ),
        "by_fixture_kind": by_kind,
    }


def _group_summaries(predictions: Sequence[Mapping], threshold: float) -> dict:
    direction_groups: dict[tuple[str, str, str], list[Mapping]] = {}
    pair_groups: dict[str, list[Mapping]] = {}
    for item in predictions:
        direction_key = (
            str(item["pair_id"]),
            str(item["target_char"]),
            str(item["competitor_char"]),
        )
        direction_groups.setdefault(direction_key, []).append(item)
        pair_groups.setdefault(str(item["pair_id"]), []).append(item)
    directions = []
    for (pair_id, target, competitor), group in sorted(direction_groups.items()):
        directions.append(
            {
                "pair_id": pair_id,
                "target_char": target,
                "competitor_char": competitor,
                "direction": f"{target}→{competitor}",
                "metrics": summarize_predictions(group, threshold),
            }
        )
    pairs = {
        pair_id: summarize_predictions(group, threshold)
        for pair_id, group in sorted(pair_groups.items())
    }
    macro_fields = (
        "target_true_acceptance_rate",
        "competitor_false_acceptance_rate",
        "pairwise_candidate_accuracy",
        "pairwise_roc_auc",
        "pairwise_average_precision",
        "median_signed_margin",
        "target_score_brier_proxy",
        "target_score_ece_proxy",
    )
    macro = {}
    for field in macro_fields:
        values = [metrics[field] for metrics in pairs.values() if metrics[field] is not None]
        macro[field] = _round(float(np.mean(values)) if values else None)
    worst_pairs = sorted(
        (
            {
                "pair_id": pair_id,
                "competitor_false_acceptance_rate": metrics[
                    "competitor_false_acceptance_rate"
                ],
            }
            for pair_id, metrics in pairs.items()
        ),
        key=lambda item: (
            item["competitor_false_acceptance_rate"] is None,
            -(
                item["competitor_false_acceptance_rate"]
                if item["competitor_false_acceptance_rate"] is not None
                else 0.0
            ),
            item["pair_id"],
        ),
    )[:10]
    return {
        "aggregate": summarize_predictions(predictions, threshold),
        "macro_over_pairs": macro,
        "pairs": pairs,
        "directions": directions,
        "worst_pairs": worst_pairs,
    }


def evaluate_backend(
    backend: LoadedBackend,
    fixtures: Sequence[ConfusionFixture],
    templates: Mapping[str, Sequence[np.ndarray]],
    *,
    threshold: float,
    threshold_grid: Sequence[float],
) -> dict:
    predictions = []
    latencies = []
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    for fixture in fixtures:
        sample_started = time.perf_counter()
        target_score = backend.score(fixture.strokes, templates[fixture.target_char])
        competitor_score = backend.score(
            fixture.strokes, templates[fixture.competitor_char]
        )
        latency_ms = (time.perf_counter() - sample_started) * 1000
        if not (
            math.isfinite(target_score)
            and math.isfinite(competitor_score)
            and 0 <= target_score <= 1
            and 0 <= competitor_score <= 1
        ):
            raise ValueError(f"backend {backend.name} returned an invalid score")
        margin = target_score - competitor_score
        latencies.append(latency_ms)
        predictions.append(
            {
                "fixture_id": fixture.fixture_id,
                "pair_id": fixture.pair_id,
                "target_char": fixture.target_char,
                "competitor_char": fixture.competitor_char,
                "written_char": fixture.written_char,
                "kind": fixture.kind,
                "label": fixture.label,
                "critical_stroke": fixture.critical_stroke,
                "morph_alpha": fixture.morph_alpha,
                "target_score": round(target_score, 10),
                "competitor_score": round(competitor_score, 10),
                "margin": round(margin, 10),
                "accepted_as_target": target_score >= threshold,
                "latency_ms": round(latency_ms, 6),
            }
        )
    latency_array = np.asarray(latencies, dtype=np.float64)
    result = {
        "status": "ok",
        "metadata": dict(backend.metadata),
        "score_warning": (
            "Legacy learned outputs are writing-quality scores, not calibrated "
            "target-identity probabilities. Threshold, Brier, and ECE are proxies."
        ),
        "metrics": _group_summaries(predictions, threshold),
        "threshold_sensitivity": {
            str(value): summarize_predictions(predictions, value)
            for value in threshold_grid
        },
        "latency_ms_per_two_template_decision": {
            "count": len(latencies),
            "mean": _round(float(latency_array.mean()), 6),
            "p50": _round(float(np.percentile(latency_array, 50)), 6),
            "p95": _round(float(np.percentile(latency_array, 95)), 6),
            "max": _round(float(latency_array.max()), 6),
            "wall_total": _round((time.perf_counter() - started) * 1000, 6),
        },
        "peak_gpu_memory_bytes": (
            int(torch.cuda.max_memory_allocated())
            if torch.cuda.is_available()
            else None
        ),
        "critical_stroke_localization_accuracy": None,
        "critical_stroke_note": "legacy checkpoints have no localization head",
        "predictions": predictions,
    }
    return result


def evaluate(
    *,
    registry_path: str | Path,
    kanji_dir: str | Path,
    requested_backends: Sequence[str],
    stroke_checkpoint: str | Path,
    chandra_checkpoint: str | Path,
    hybrid_config: str | Path,
    threshold: float,
    threshold_grid: Sequence[float],
    strict_backends: bool = False,
) -> dict:
    if not requested_backends:
        raise ValueError("at least one backend must be requested")
    if not threshold_grid:
        raise ValueError("threshold_grid must not be empty")
    if not 0 <= threshold <= 1 or any(not 0 <= value <= 1 for value in threshold_grid):
        raise ValueError("acceptance thresholds must be in 0..1")
    registry = load_confusion_registry(registry_path)
    template_cache: dict[str, tuple[np.ndarray, ...]] = {}

    def template_loader(char: str) -> tuple[np.ndarray, ...]:
        if char not in template_cache:
            template_cache[char] = tuple(load_char(kanji_dir, char))
        return template_cache[char]

    fixtures = generate_confusion_fixtures(registry, template_loader)
    for pair in registry.pairs:
        for char in pair.characters:
            template_loader(char)
    loaded, statuses = load_backends(
        requested_backends,
        stroke_checkpoint=stroke_checkpoint,
        chandra_checkpoint=chandra_checkpoint,
        hybrid_config=hybrid_config,
        strict=strict_backends,
    )
    backends: dict[str, dict] = {name: dict(value) for name, value in statuses.items()}
    for backend in loaded:
        try:
            backends[backend.name] = evaluate_backend(
                backend,
                fixtures,
                template_cache,
                threshold=threshold,
                threshold_grid=threshold_grid,
            )
        except Exception as exc:
            if strict_backends:
                raise
            backends[backend.name] = {
                "status": "failed",
                "reason": f"evaluation_failed:{type(exc).__name__}",
                "metadata": dict(backend.metadata),
            }
    fixture_counts: dict[str, int] = {}
    for fixture in fixtures:
        fixture_counts[fixture.kind] = fixture_counts.get(fixture.kind, 0) + 1
    return {
        "schema_version": "confusion_baseline.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": _git_source(),
        "registry": {
            "path": Path(registry_path).as_posix(),
            "registry_id": registry.registry_id,
            "version": registry.version,
            "sha256": registry.sha256,
        },
        "fixtures": {
            "generator_version": registry.fixture_policy.generator_version,
            "split": registry.fixture_policy.baseline_split,
            "split_seed": registry.fixture_policy.split_seeds[
                registry.fixture_policy.baseline_split
            ],
            "seed_sha256": fixture_seed_sha256(fixtures),
            "content_sha256": fixture_content_sha256(fixtures),
            "count": len(fixtures),
            "counts_by_kind": fixture_counts,
        },
        "evaluation": {
            "acceptance_threshold": threshold,
            "threshold_grid": list(threshold_grid),
            "requested_backends": list(requested_backends),
            "identity_decision": "target_score - competitor_score",
            "score_semantics_warning": (
                "The existing learned overall score is a writing-quality proxy; "
                "it is not a calibrated identity probability."
            ),
        },
        "checkpoints": {
            "stroke": _checkpoint_metadata(stroke_checkpoint),
            "chandra": _checkpoint_metadata(chandra_checkpoint),
            "hybrid_config": _checkpoint_metadata(hybrid_config),
        },
        "environment": _environment(),
        "backends": backends,
    }


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_thresholds(value: str) -> tuple[float, ...]:
    return tuple(float(item) for item in _parse_csv(value))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry", default="configs/confusions/kana_seed_v1.yaml"
    )
    parser.add_argument("--kanji-dir", default="kanji")
    parser.add_argument("--stroke-checkpoint", default="checkpoints/stroke_scorer.pt")
    parser.add_argument(
        "--chandra-checkpoint", default="checkpoints/chandra_scorer.pt"
    )
    parser.add_argument("--hybrid-config", default="checkpoints/hybrid_config.json")
    parser.add_argument("--backends", default=",".join(DEFAULT_BACKENDS))
    parser.add_argument("--acceptance-threshold", type=float, default=0.5)
    parser.add_argument("--threshold-grid", default="0.5,0.6,0.7")
    parser.add_argument("--strict-backends", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    report = evaluate(
        registry_path=args.registry,
        kanji_dir=args.kanji_dir,
        requested_backends=_parse_csv(args.backends),
        stroke_checkpoint=args.stroke_checkpoint,
        chandra_checkpoint=args.chandra_checkpoint,
        hybrid_config=args.hybrid_config,
        threshold=args.acceptance_threshold,
        threshold_grid=_parse_thresholds(args.threshold_grid),
        strict_backends=args.strict_backends,
    )
    if args.summary_only:
        for backend in report["backends"].values():
            backend.pop("predictions", None)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
