"""Measure end-to-end latency of the existing POST /score endpoint.

No deployment URL or key is embedded. Configure them with CLI flags or:

    LINGO_SCORE_URL=http://127.0.0.1:8000/score
    LINGO_API_KEY=...                    # only for an authenticated edge endpoint
    LINGO_SCORE_MODE=direct|edge
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


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


def load_payload(fixture_path: Path, char: str, mode: str) -> dict:
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    try:
        strokes = fixtures["characters"][char]["cases"]["perfect"]
    except KeyError as exc:
        raise SystemExit(f"fixture does not contain {char!r}") from exc
    payload = {"char": char, "strokes": strokes}
    if mode == "edge":
        payload["action"] = "score"
    return payload


def request_once(url: str, payload: dict, timeout: float, api_key: str | None) -> float:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers.update({"Authorization": f"Bearer {api_key}", "apikey": api_key})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {body}")
        if "score" not in body:
            raise RuntimeError("response did not contain score")
    return (time.perf_counter() - started) * 1000.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=os.environ.get("LINGO_SCORE_URL", "http://127.0.0.1:8000/score"),
    )
    parser.add_argument(
        "--mode",
        choices=("direct", "edge"),
        default=os.environ.get("LINGO_SCORE_MODE", "direct"),
    )
    parser.add_argument("--char", default="永")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "realtime-strokes.json",
    )
    args = parser.parse_args()
    if args.samples < 1 or args.warmup < 0:
        parser.error("samples must be positive and warmup must be non-negative")

    payload = load_payload(args.fixture, args.char, args.mode)
    key = os.environ.get("LINGO_API_KEY")
    try:
        for _ in range(args.warmup):
            request_once(args.url, payload, args.timeout, key)
        samples = [
            request_once(args.url, payload, args.timeout, key)
            for _ in range(args.samples)
        ]
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        raise SystemExit(f"score benchmark failed: {exc}") from exc

    print(json.dumps({
        "endpoint": args.url,
        "mode": args.mode,
        "character": args.char,
        "samples": len(samples),
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

