"""Validate a pushed Lingo build through direct RunPod and Supabase Edge APIs.

The report intentionally stores only aggregate timings, status counts, and
non-secret deployment metadata. Set ``LINGO_API_KEY`` when the Edge Function
requires authorization.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COACH_CASES = ("perfect", "start_offset", "direction_reversed", "path_deviation")
SCORE_KEYS = {
    "char",
    "score",
    "base_model_score",
    "elapsed",
    "template",
    "user",
    "strokes",
    "missing",
    "extra",
    "match",
    "corrections",
}


@dataclass(frozen=True)
class HttpResult:
    status: int | None
    body: Any
    elapsed_ms: float
    timed_out: bool = False
    network_error: str | None = None


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _json_body(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float,
    api_key: str | None = None,
) -> HttpResult:
    headers = {"Accept": "application/json", "User-Agent": "lingo-runpod-validator/1"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, allow_nan=True).encode("utf-8")
    if api_key:
        headers.update({"Authorization": f"Bearer {api_key}", "apikey": api_key})
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResult(
                status=response.status,
                body=_json_body(response.read()),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
    except urllib.error.HTTPError as exc:
        return HttpResult(
            status=exc.code,
            body=_json_body(exc.read()),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
    except TimeoutError as exc:
        return HttpResult(
            status=None,
            body=None,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            timed_out=True,
            network_error=type(exc).__name__,
        )
    except urllib.error.URLError as exc:
        reason = exc.reason
        timed_out = isinstance(reason, (TimeoutError, socket.timeout))
        return HttpResult(
            status=None,
            body=None,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            timed_out=timed_out,
            network_error=type(reason).__name__,
        )


def rich_stroke(stroke: list) -> list[dict]:
    return [
        {"x": point[0], "y": point[1], "t": index * 8, "pressure": 0.5}
        for index, point in enumerate(stroke)
    ]


def coach_payload(
    fixtures: dict,
    char: str,
    case_name: str,
    request_id: str,
    *,
    rich: bool,
) -> dict:
    stroke = fixtures["characters"][char]["cases"][case_name][0]
    return {
        "protocol_version": 1,
        "request_id": request_id,
        "session_id": "validation-session",
        "attempt_id": "validation-attempt",
        "attempt_revision": 0,
        "char": char,
        "mode": "trace",
        "accepted_strokes": [],
        "current_stroke": rich_stroke(stroke) if rich else stroke,
        "expected_template_index": 0,
    }


def latency_summary(results: list[HttpResult]) -> dict:
    successful = [result.elapsed_ms for result in results if result.status == 200]
    statuses = Counter(
        "timeout" if result.timed_out
        else "network_error" if result.status is None
        else str(result.status)
        for result in results
    )
    return {
        "requests": len(results),
        "successful": len(successful),
        "status_counts": dict(sorted(statuses.items())),
        "timeouts": sum(result.timed_out for result in results),
        "p50_ms": round(statistics.median(successful), 3) if successful else None,
        "p95_ms": (
            round(value, 3) if (value := percentile(successful, 0.95)) is not None
            else None
        ),
        "max_ms": round(max(successful), 3) if successful else None,
    }


def run_benchmark(
    request_count: int,
    warmup_count: int,
    callback: Callable[[int], HttpResult],
) -> tuple[dict, list[HttpResult]]:
    for index in range(warmup_count):
        callback(-index - 1)
    results = [callback(index) for index in range(request_count)]
    return latency_summary(results), results


def safe_health(body: Any) -> dict:
    if not isinstance(body, dict):
        return {}
    keys = (
        "protocol_version",
        "build_sha",
        "coach_ready",
        "coach_engine",
        "deep_score_ready",
        "deep_model_loading",
        "model_kind",
        "cuda",
        "device",
    )
    return {key: body.get(key) for key in keys}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--edge-url", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--chars", default="永,水,木,日,語")
    parser.add_argument("--coach-requests", type=int, default=50)
    parser.add_argument("--score-requests", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--expected-engine",
        choices=("auto", "geometry-only", "geometry+stroke-model"),
        default="auto",
    )
    parser.add_argument("--coach-timeout", type=float, default=3.0)
    parser.add_argument("--score-timeout", type=float, default=180.0)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "realtime-strokes.json",
    )
    args = parser.parse_args()
    if args.coach_requests < 1 or args.score_requests < 1:
        parser.error("request counts must be positive")
    if args.coach_timeout <= 0 or args.score_timeout <= 0:
        parser.error("timeouts must be positive")

    base_url = args.base_url.rstrip("/")
    edge_url = args.edge_url.rstrip("/")
    chars = [char.strip() for char in args.chars.split(",") if char.strip()]
    fixtures = json.loads(args.fixture.read_text(encoding="utf-8"))
    missing = [char for char in chars if char not in fixtures.get("characters", {})]
    if missing:
        parser.error(f"fixture is missing characters: {','.join(missing)}")
    edge_key = os.environ.get("LINGO_API_KEY")
    test_run_id = (
        f"phase2-{args.expected_sha[:12]}-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    checks: list[dict] = []
    failures: list[str] = []

    def check(name: str, passed: bool, **evidence) -> None:
        checks.append({"name": name, "passed": passed, **evidence})
        if not passed:
            failures.append(name)

    direct_health = request_json(
        f"{base_url}/health", timeout=args.coach_timeout
    )
    direct_health_body = safe_health(direct_health.body)
    check("direct health status", direct_health.status == 200, status=direct_health.status)
    check(
        "direct build sha",
        direct_health_body.get("build_sha") == args.expected_sha,
        actual=direct_health_body.get("build_sha"),
    )
    check(
        "direct coach ready",
        direct_health_body.get("coach_ready") is True,
        engine=direct_health_body.get("coach_engine"),
    )
    if args.expected_engine != "auto":
        check(
            "direct expected engine",
            direct_health_body.get("coach_engine") == args.expected_engine,
            actual=direct_health_body.get("coach_engine"),
        )

    edge_health = request_json(
        edge_url,
        method="POST",
        payload={"action": "health"},
        timeout=args.coach_timeout,
        api_key=edge_key,
    )
    edge_health_body = safe_health(edge_health.body)
    check("edge health status", edge_health.status == 200, status=edge_health.status)
    check(
        "edge build sha",
        edge_health_body.get("build_sha") == args.expected_sha,
        actual=edge_health_body.get("build_sha"),
    )

    for char in chars:
        direct_template = request_json(
            f"{base_url}/template/{urllib.parse.quote(char)}",
            timeout=args.coach_timeout,
        )
        template_ok = (
            direct_template.status == 200
            and isinstance(direct_template.body, dict)
            and bool(direct_template.body.get("strokes"))
        )
        check(f"direct template {char}", template_ok, status=direct_template.status)

        edge_template = request_json(
            edge_url,
            method="POST",
            payload={"action": "template", "char": char},
            timeout=args.coach_timeout,
            api_key=edge_key,
        )
        edge_template_ok = (
            edge_template.status == 200
            and isinstance(edge_template.body, dict)
            and bool(edge_template.body.get("strokes"))
        )
        check(f"edge template {char}", edge_template_ok, status=edge_template.status)

    legacy = coach_payload(
        fixtures, chars[0], "perfect", "validation-legacy", rich=False
    )
    rich = coach_payload(
        fixtures, chars[0], "perfect", "validation-rich", rich=True
    )
    for label, payload in (("legacy", legacy), ("rich", rich)):
        result = request_json(
            f"{base_url}/coach/stroke",
            method="POST",
            payload=payload,
            timeout=args.coach_timeout,
        )
        body = result.body if isinstance(result.body, dict) else {}
        check(
            f"direct {label} coach contract",
            result.status == 200
            and body.get("request_id") == payload["request_id"]
            and body.get("engine") in {"geometry-only", "geometry+stroke-model"},
            status=result.status,
            engine=body.get("engine"),
        )

    invalid_payloads = {
        "empty stroke": {**legacy, "request_id": "invalid-empty", "current_stroke": []},
        "non-finite": {
            **legacy,
            "request_id": "invalid-non-finite",
            "current_stroke": [{"x": 0.2, "y": math.inf, "t": 0}],
        },
        "too many points": {
            **legacy,
            "request_id": "invalid-too-many",
            "current_stroke": [[0.2, 0.2]] * 4097,
        },
    }
    for label, payload in invalid_payloads.items():
        result = request_json(
            f"{base_url}/coach/stroke",
            method="POST",
            payload=payload,
            timeout=args.coach_timeout,
        )
        check(
            f"direct invalid {label}",
            result.status is not None and 400 <= result.status < 500,
            status=result.status,
        )
    edge_invalid = request_json(
        edge_url,
        method="POST",
        payload={"action": "coach", **invalid_payloads["empty stroke"]},
        timeout=args.coach_timeout,
        api_key=edge_key,
    )
    check(
        "edge invalid coach input",
        edge_invalid.status is not None and 400 <= edge_invalid.status < 500,
        status=edge_invalid.status,
    )

    def selected_case(index: int) -> tuple[str, str]:
        safe_index = max(index, 0)
        return chars[safe_index % len(chars)], COACH_CASES[safe_index % len(COACH_CASES)]

    def direct_coach(index: int) -> HttpResult:
        char, case_name = selected_case(index)
        payload = coach_payload(
            fixtures,
            char,
            case_name,
            f"direct-coach-{index}",
            rich=index % 2 == 0,
        )
        return request_json(
            f"{base_url}/coach/stroke",
            method="POST",
            payload=payload,
            timeout=args.coach_timeout,
        )

    def edge_coach(index: int) -> HttpResult:
        char, case_name = selected_case(index)
        payload = coach_payload(
            fixtures,
            char,
            case_name,
            f"edge-coach-{index}",
            rich=index % 2 == 0,
        )
        return request_json(
            edge_url,
            method="POST",
            payload={"action": "coach", **payload},
            timeout=args.coach_timeout,
            api_key=edge_key,
        )

    direct_coach_summary, direct_coach_results = run_benchmark(
        args.coach_requests, 5, direct_coach
    )
    edge_coach_summary, edge_coach_results = run_benchmark(
        args.coach_requests, 5, edge_coach
    )
    direct_coach_summary.update({
        "target_p95_ms": 400,
        "target_met": (
            direct_coach_summary["p95_ms"] is not None
            and direct_coach_summary["p95_ms"] <= 400
        ),
    })
    edge_coach_summary.update({
        "target_p95_ms": 700,
        "target_met": (
            edge_coach_summary["p95_ms"] is not None
            and edge_coach_summary["p95_ms"] <= 700
        ),
    })
    check(
        "direct coach benchmark",
        all(result.status == 200 for result in direct_coach_results),
        **direct_coach_summary,
    )
    check(
        "edge coach benchmark",
        all(result.status == 200 for result in edge_coach_results),
        **edge_coach_summary,
    )

    def score_payload(index: int) -> dict:
        char = chars[max(index, 0) % len(chars)]
        return {
            "char": char,
            "strokes": fixtures["characters"][char]["cases"]["perfect"],
        }

    def direct_score(index: int) -> HttpResult:
        return request_json(
            f"{base_url}/score",
            method="POST",
            payload=score_payload(index),
            timeout=args.score_timeout,
        )

    def edge_score(index: int) -> HttpResult:
        return request_json(
            edge_url,
            method="POST",
            payload={
                "action": "score",
                "test_run_id": test_run_id,
                **score_payload(index),
            },
            timeout=args.score_timeout,
            api_key=edge_key,
        )

    direct_score_summary, direct_score_results = run_benchmark(
        args.score_requests, 1, direct_score
    )
    edge_score_summary, edge_score_results = run_benchmark(
        args.score_requests, 1, edge_score
    )
    direct_score_contract = all(
        result.status == 200
        and isinstance(result.body, dict)
        and SCORE_KEYS <= result.body.keys()
        for result in direct_score_results
    )
    edge_score_contract = all(
        result.status == 200
        and isinstance(result.body, dict)
        and SCORE_KEYS <= result.body.keys()
        for result in edge_score_results
    )
    check("direct score contract", direct_score_contract, **direct_score_summary)
    check("edge score contract", edge_score_contract, **edge_score_summary)

    report = {
        "protocol_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "target": {
            "expected_sha": args.expected_sha,
            "characters": chars,
            "expected_engine": args.expected_engine,
            "edge_auth_configured": bool(edge_key),
            "test_run_id": test_run_id,
        },
        "health": {
            "direct": direct_health_body,
            "edge": edge_health_body,
        },
        "benchmarks": {
            "direct_coach": direct_coach_summary,
            "edge_coach": edge_coach_summary,
            "direct_score": direct_score_summary,
            "edge_score": edge_score_summary,
        },
        "checks": checks,
        "conclusion": "PASS" if not failures else "FAIL",
        "failed_checks": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
