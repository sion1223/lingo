"""Static deployment contract for the Supabase realtime coach proxy."""

from pathlib import Path

EDGE_SOURCE = (
    Path(__file__).resolve().parents[1] / "web" / "edge-score.ts"
).read_text(encoding="utf-8")
APP_EDGE_SOURCE = (
    Path(__file__).resolve().parents[1] / "web" / "edge-app.ts"
).read_text(encoding="utf-8")
SERVE_SOURCE = (
    Path(__file__).resolve().parents[1] / "serve.sh"
).read_text(encoding="utf-8")


def test_runpod_origin_comes_from_environment_only():
    assert 'Deno.env.get("RUNPOD_BASE_URL")' in EDGE_SOURCE
    assert "proxy.runpod.net" not in EDGE_SOURCE


def test_coach_route_has_its_own_short_unlogged_proxy_path():
    start = EDGE_SOURCE.index('if (body.action === "coach")')
    end = EDGE_SOURCE.index('if (body.action === "attempt")', start)
    coach_branch = EDGE_SOURCE[start:end]

    assert '"/coach/stroke"' in coach_branch
    assert "2500" in coach_branch
    assert "submissions" not in coach_branch
    assert "createClient" not in coach_branch


def test_attempt_route_batches_rich_points_into_dedicated_storage():
    start = EDGE_SOURCE.index('if (body.action === "attempt")')
    end = EDGE_SOURCE.index('if (body.action === "verbalize"', start)
    attempt_branch = EDGE_SOURCE[start:end]

    assert 'from("writing_attempts").upsert' in attempt_branch
    assert 'onConflict: "attempt_id,attempt_revision"' in attempt_branch
    assert "body.strokes" in EDGE_SOURCE
    assert "body.stroke_results" in EDGE_SOURCE


def test_edge_errors_do_not_echo_internal_exception_text():
    assert "String(e)" not in EDGE_SOURCE
    assert "String(error)" not in EDGE_SOURCE


def test_app_edge_has_no_duplicated_html_or_embedded_token():
    assert 'Deno.env.get("LINGO_STATIC_APP_URL")' in APP_EDGE_SOURCE
    assert "const HTML" not in APP_EDGE_SOURCE
    assert "eyJhbGci" not in APP_EDGE_SOURCE
    assert "proxy.runpod.net" not in APP_EDGE_SOURCE


def test_server_startup_injects_the_checked_out_build_sha():
    assert 'export BUILD_SHA="$(git rev-parse HEAD' in SERVE_SOURCE
