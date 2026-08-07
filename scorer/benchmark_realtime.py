"""Warm-process latency benchmark for the realtime stroke coach.

Examples:
    python -m scorer.benchmark_realtime --engine geometry-only
    python -m scorer.benchmark_realtime --engine geometry+stroke-model
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from scorer.hybrid import load_stroke_scorer
from scorer.realtime import FastCoachEngine
from scorer.schemas import CoachStrokeRequest

ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("at least one latency sample is required")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def load_case(path: Path, char: str) -> tuple[list, list]:
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    try:
        case = fixtures["characters"][char]
        return case["template"], case["cases"]["perfect"][0]
    except KeyError as exc:
        raise SystemExit(f"fixture does not contain {char!r}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--char", default="永")
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument(
        "--engine",
        choices=("geometry-only", "geometry+stroke-model"),
        default="geometry-only",
    )
    parser.add_argument("--with-model", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=ROOT / "checkpoints" / "stroke_scorer.pt",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "realtime-strokes.json",
    )
    args = parser.parse_args()
    if args.samples < 1 or args.warmup < 0:
        parser.error("samples must be positive and warmup must be non-negative")

    template, stroke = load_case(args.fixture, args.char)
    model = None
    if args.engine == "geometry+stroke-model" or args.with_model:
        if not args.checkpoint.exists():
            parser.error(f"checkpoint does not exist: {args.checkpoint}")
        model = load_stroke_scorer(args.checkpoint, device="cpu")
    engine = FastCoachEngine(lambda _char: template, stroke_model=model)
    request = CoachStrokeRequest(
        request_id="benchmark-request",
        session_id="benchmark-session",
        attempt_id="benchmark-attempt",
        attempt_revision=0,
        char=args.char,
        mode="trace",
        current_stroke=stroke,
        expected_template_index=0,
    )

    for _ in range(args.warmup):
        engine.coach(request)
    samples = []
    for _ in range(args.samples):
        started = time.perf_counter()
        response = engine.coach(request)
        samples.append((time.perf_counter() - started) * 1000.0)
        if response.request_id != request.request_id:
            raise RuntimeError("coach response identity mismatch")

    p95 = percentile(samples, 0.95)
    print(json.dumps({
        "engine": engine.mode,
        "character": args.char,
        "warmup": args.warmup,
        "samples": len(samples),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(p95, 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "target_p95_ms": 400,
        "target_met": p95 <= 400,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
