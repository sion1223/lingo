"""HTTP smoke tests for the modular browser entry point."""

from fastapi.testclient import TestClient

from scorer.server import app


def test_root_uses_native_modules_without_embedded_deployment_credentials():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert '<script type="module" src="/web/app.js"></script>' in response.text
    assert "supabase.co" not in response.text
    assert "eyJhbGci" not in response.text


def test_frontend_module_and_styles_are_served():
    client = TestClient(app)

    app_response = client.get("/web/app.js")
    styles_response = client.get("/web/styles.css")

    assert app_response.status_code == 200
    assert app_response.headers["content-type"].startswith("text/javascript")
    assert "LocalCoachController" in app_response.text
    assert styles_response.status_code == 200
    assert styles_response.headers["content-type"].startswith("text/css")

