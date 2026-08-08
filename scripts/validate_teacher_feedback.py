"""Validate the evidence-locked teacher renderer and optionally call Luna live.

The synthetic suite never needs an API key. ``--live`` exercises the real
FastAPI ``/coach/verbalize`` endpoint with the repo-local ``.env.local`` key,
but never prints or persists the credential.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scorer.teacher_renderer import (
    DEFAULT_TEACHER_MODEL,
    TeacherRenderer,
    TeacherSemanticError,
    deterministic_fallback,
    validate_teacher_feedback,
)
from scorer.teacher_schemas import (
    TeacherFeedbackEnvelope,
    TeacherFeedbackOutput,
    TeacherFeedbackRequest,
)


EVIDENCE_CODES = (
    "START_TOO_HIGH",
    "START_TOO_LOW",
    "START_TOO_LEFT",
    "START_TOO_RIGHT",
    "END_TOO_HIGH",
    "END_TOO_LOW",
    "STROKE_TOO_LONG",
    "STROKE_TOO_SHORT",
    "STROKE_TOO_VERTICAL",
    "STROKE_TOO_HORIZONTAL",
    "STROKE_ANGLE_MISMATCH",
    "CURVE_TOO_EARLY",
    "CURVE_TOO_LATE",
    "TERMINAL_HOOK_WRONG_DIRECTION",
    "INTER_STROKE_GAP_TOO_SMALL",
    "INTER_STROKE_GAP_TOO_LARGE",
    "START_OFFSET",
    "END_OFFSET",
    "PATH_DEVIATION",
    "CURVE_EARLY",
    "CURVE_LATE",
    "DIRECTION_REVERSED",
    "WRONG_ORDER",
    "EXTRA_STROKE",
    "MISSING_STROKE",
    "TOO_SHORT",
    "TOO_LONG",
    "POSITION_OFFSET",
    "SCALE_MISMATCH",
    "UNCERTAIN_MATCH",
    "CHARACTER_RESEMBLES_COMPETITOR",
)

MUTATIONS = (
    "decision_id",
    "error_code",
    "next_action",
    "invented_score",
    "unsupported_stroke",
    "target_competitor_swap",
    "continued_after_rejection",
    "multiple_actions",
)

EXPECTED_MUTATION_ERRORS = {
    "decision_id": "decision_id_changed",
    "error_code": "error_code_changed",
    "next_action": "next_action_changed",
    "invented_score": "invented_score_or_confidence",
    "unsupported_stroke": "unsupported_stroke_number",
    "target_competitor_swap": "target_competitor_swapped",
    "continued_after_rejection": "continued_after_rejection",
    "multiple_actions": "multiple_actions",
}

INPUT_USD_PER_MILLION = 1.00
CACHED_INPUT_USD_PER_MILLION = 0.10
OUTPUT_USD_PER_MILLION = 6.00


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _profiles(code: str) -> tuple[dict[str, str], dict[str, str]]:
    if "LONG" in code or code == "TOO_LONG":
        return ({"relative_length": "shorter"}, {"relative_length": "long"})
    if "SHORT" in code or code == "TOO_SHORT":
        return ({"relative_length": "longer"}, {"relative_length": "short"})
    if "VERTICAL" in code or "HORIZONTAL" in code or "ANGLE" in code:
        return (
            {"primary_direction": "down_right"},
            {"primary_direction": "mostly_down"},
        )
    if "HIGH" in code or "LOW" in code:
        return ({"start_height": "middle"}, {"start_height": "high"})
    if "LEFT" in code or "RIGHT" in code:
        return ({"start_position": "template"}, {"start_position": "offset"})
    if "CURVE" in code:
        return ({"path_shape": "template_curve"}, {"path_shape": "curve_offset"})
    return ({}, {})


def teacher_payload(index: int, code: str) -> dict[str, Any]:
    target_profile, observed_profile = _profiles(code)
    return {
        "schema_version": "teacher_feedback.v1",
        "locale": "ko",
        "learner": {
            "level": "beginner",
            "attempt_number": index % 20 + 1,
            "same_error_count": index % 4 + 1,
            "preferred_length": "short",
        },
        "task": {
            "target_char": "い",
            "nearest_competitor": "り",
            "mode": "recall" if index % 2 else "trace",
            "critical_stroke": 1,
            "total_strokes": 2,
        },
        "locked_decision": {
            "decision_id": f"synthetic-{index}",
            "error_code": code,
            "evidence_codes": [code],
            "severity": "major" if index % 3 == 0 else "minor",
            "confidence": round(0.7 + (index % 30) / 100, 2),
            "accepted": False,
            "next_action": "RETRY_CRITICAL_STROKE",
        },
        "evidence": {
            "target_margin": -0.56 if code == "CHARACTER_RESEMBLES_COMPETITOR" else None,
            "critical_region": "stroke_2",
            "target_feature_profile": target_profile,
            "observed_feature_profile": observed_profile,
        },
        "teaching_policy": {
            "allowed_strategies": [
                "direct_correction",
                "brief_contrast",
                "micro_drill",
            ],
            "max_sentences": 2,
            "max_characters": 100,
            "must_preserve_locked_fields": True,
            "forbidden": [
                "change_diagnosis",
                "invent_score",
                "invent_evidence",
                "give_multiple_actions",
            ],
        },
    }


def _unsafe_output(
    request: TeacherFeedbackRequest,
    output: TeacherFeedbackOutput,
    mutation: str,
) -> TeacherFeedbackOutput:
    changes: dict[str, str]
    if mutation == "decision_id":
        changes = {"decision_id": "different-decision"}
    elif mutation == "error_code":
        changes = {"error_code": "DIFFERENT_ERROR"}
    elif mutation == "next_action":
        changes = {"next_action": "DRAW_NEXT_STROKE"}
    elif mutation == "invented_score":
        changes = {"primary_text": "점수는 99점입니다."}
    elif mutation == "unsupported_stroke":
        changes = {"primary_text": "9획을 다시 써 보세요."}
    elif mutation == "target_competitor_swap":
        changes = {
            "primary_text": (
                f"{request.task.nearest_competitor}가 "
                f"{request.task.target_char}처럼 보입니다."
            )
        }
    elif mutation == "continued_after_rejection":
        changes = {"secondary_text": "다음 획으로 넘어가세요."}
    else:
        changes = {
            "primary_text": "획을 짧게 써 보세요.",
            "secondary_text": "그리고 시작점을 낮춰 써 보세요.",
        }
    return output.model_copy(update=changes)


class _FailureResponses:
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **_kwargs: Any) -> None:
        self.calls += 1
        if self.calls % 2:
            raise TimeoutError("synthetic timeout")
        raise RuntimeError("synthetic 503")


class _FailureClient:
    def __init__(self, responses: _FailureResponses) -> None:
        self.responses = responses


def run_synthetic(cases: int) -> dict[str, Any]:
    latencies: list[float] = []
    locked_preserved = 0
    safe_fallbacks = 0
    mutation_detected = Counter()
    failure_fallbacks = 0
    failure_reasons = Counter()
    responses = _FailureResponses()
    failure_renderer = TeacherRenderer(
        client_factory=lambda: _FailureClient(responses),
        cache_size=0,
    )

    renderer_logger = logging.getLogger("scorer.teacher_renderer")
    previous_level = renderer_logger.level
    renderer_logger.setLevel(logging.CRITICAL)
    try:
        for index in range(cases):
            code = EVIDENCE_CODES[index % len(EVIDENCE_CODES)]
            request = TeacherFeedbackRequest.model_validate(
                teacher_payload(index, code)
            )
            started = time.perf_counter()
            output = deterministic_fallback(request)
            validate_teacher_feedback(request, output)
            latencies.append((time.perf_counter() - started) * 1000)
            safe_fallbacks += 1
            if (
                output.decision_id == request.locked_decision.decision_id
                and output.error_code == request.locked_decision.error_code
                and output.next_action == request.locked_decision.next_action
            ):
                locked_preserved += 1

            mutation = MUTATIONS[index % len(MUTATIONS)]
            unsafe = _unsafe_output(request, output, mutation)
            try:
                validate_teacher_feedback(request, unsafe)
            except TeacherSemanticError as exc:
                expected = EXPECTED_MUTATION_ERRORS[mutation]
                if expected in exc.errors:
                    mutation_detected[mutation] += 1

            envelope = failure_renderer.render(request)
            expected_reason = "timeout" if index % 2 == 0 else "api_error"
            if (
                envelope.source == "fallback"
                and envelope.fallback_reason == expected_reason
                and envelope.feedback.decision_id
                == request.locked_decision.decision_id
            ):
                failure_fallbacks += 1
            failure_reasons[str(envelope.fallback_reason)] += 1
    finally:
        renderer_logger.setLevel(previous_level)

    mutation_trials = Counter(
        MUTATIONS[index % len(MUTATIONS)] for index in range(cases)
    )
    mutation_rates = {
        name: {
            "detected": mutation_detected[name],
            "trials": mutation_trials[name],
            "rate": (
                mutation_detected[name] / mutation_trials[name]
                if mutation_trials[name]
                else None
            ),
        }
        for name in MUTATIONS
    }
    return {
        "cases": cases,
        "schema_or_fallback": {
            "passed": safe_fallbacks,
            "rate": safe_fallbacks / cases,
        },
        "locked_decision_preserved": {
            "passed": locked_preserved,
            "rate": locked_preserved / cases,
        },
        "unsafe_mutations_rejected": mutation_rates,
        "provider_failure_fallback": {
            "passed": failure_fallbacks,
            "rate": failure_fallbacks / cases,
            "reasons": dict(sorted(failure_reasons.items())),
        },
        "fallback_latency_ms": {
            "p50": round(statistics.median(latencies), 4),
            "p95": round(_percentile(latencies, 0.95), 4),
            "max": round(max(latencies), 4),
        },
    }


def _estimated_cost(usage: dict[str, Any] | None) -> float | None:
    if not usage:
        return None
    input_tokens = int(usage.get("input_tokens", 0))
    cached_tokens = int(usage.get("cached_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens", 0))
    uncached_tokens = max(0, input_tokens - cached_tokens)
    return round(
        (
            uncached_tokens * INPUT_USD_PER_MILLION
            + cached_tokens * CACHED_INPUT_USD_PER_MILLION
            + output_tokens * OUTPUT_USD_PER_MILLION
        )
        / 1_000_000,
        8,
    )


def run_live(runs: int = 1) -> dict[str, Any]:
    # Importing here keeps synthetic validation independent from server globals.
    from fastapi.testclient import TestClient

    from scorer import server

    if runs < 1:
        raise ValueError("runs must be at least 1")
    server._teacher_renderer = TeacherRenderer(cache_size=0)
    client = TestClient(server.app)
    samples: list[dict[str, Any]] = []
    for index in range(runs):
        payload = teacher_payload(
            10_000 + index,
            "CHARACTER_RESEMBLES_COMPETITOR",
        )
        started = time.perf_counter()
        response = client.post("/coach/verbalize", json=payload)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise RuntimeError(
                f"live endpoint returned HTTP {response.status_code}"
            )

        envelope = TeacherFeedbackEnvelope.model_validate(response.json())
        request = TeacherFeedbackRequest.model_validate(payload)
        validate_teacher_feedback(request, envelope.feedback)
        usage = (
            envelope.usage.model_dump(mode="json")
            if envelope.usage
            else None
        )
        samples.append(
            {
                "http_status": response.status_code,
                "model_reported": envelope.model,
                "source": envelope.source,
                "fallback_reason": envelope.fallback_reason,
                "elapsed_ms": elapsed_ms,
                "provider_latency_ms": envelope.latency_ms,
                "usage": usage,
                "estimated_cost_usd": _estimated_cost(usage),
                "feedback": {
                    "primary_text": envelope.feedback.primary_text,
                    "secondary_text": envelope.feedback.secondary_text,
                    "spoken_text": envelope.feedback.spoken_text,
                },
                "locked_fields_preserved": (
                    envelope.feedback.decision_id
                    == request.locked_decision.decision_id
                    and envelope.feedback.error_code
                    == request.locked_decision.error_code
                    and envelope.feedback.next_action
                    == request.locked_decision.next_action
                ),
            }
        )

    luna_samples = [sample for sample in samples if sample["source"] == "luna"]
    if not luna_samples:
        reasons = Counter(
            str(sample["fallback_reason"]) for sample in samples
        )
        raise RuntimeError(
            "all live Luna calls fell back: "
            + ", ".join(f"{key}={value}" for key, value in reasons.items())
        )
    provider_latencies = [
        float(sample["provider_latency_ms"]) for sample in samples
    ]
    endpoint_latencies = [float(sample["elapsed_ms"]) for sample in samples]
    costs = [
        float(sample["estimated_cost_usd"])
        for sample in samples
        if sample["estimated_cost_usd"] is not None
    ]
    example = luna_samples[0]
    return {
        "runs": runs,
        "http_200": sum(sample["http_status"] == 200 for sample in samples),
        "model_requested": DEFAULT_TEACHER_MODEL,
        "model_reported": sorted(
            {str(sample["model_reported"]) for sample in luna_samples}
        ),
        "sources": dict(Counter(str(sample["source"]) for sample in samples)),
        "fallback_reasons": dict(
            Counter(
                str(sample["fallback_reason"])
                for sample in samples
                if sample["fallback_reason"] is not None
            )
        ),
        "endpoint_latency_ms": {
            "p50": round(statistics.median(endpoint_latencies), 2),
            "p95": round(_percentile(endpoint_latencies, 0.95), 2),
        },
        "provider_latency_ms": {
            "p50": round(statistics.median(provider_latencies), 2),
            "p95": round(_percentile(provider_latencies, 0.95), 2),
        },
        "estimated_cost_usd": {
            "total": round(sum(costs), 8),
            "per_call_mean": round(statistics.mean(costs), 8) if costs else None,
        },
        "usage": [sample["usage"] for sample in samples],
        "example_feedback": example["feedback"],
        "locked_fields_preserved": all(
            sample["locked_fields_preserved"] for sample in samples
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=1_000)
    parser.add_argument(
        "--live",
        action="store_true",
        help="call the real /coach/verbalize endpoint with gpt-5.6-luna",
    )
    parser.add_argument(
        "--live-runs",
        type=int,
        default=1,
        help="number of uncached live endpoint calls (default: 1)",
    )
    args = parser.parse_args()
    if args.cases < 1:
        parser.error("--cases must be at least 1")
    if args.live_runs < 1:
        parser.error("--live-runs must be at least 1")

    report: dict[str, Any] = {
        "model": DEFAULT_TEACHER_MODEL,
        "synthetic": run_synthetic(args.cases),
    }
    if args.live:
        report["live"] = run_live(args.live_runs)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
