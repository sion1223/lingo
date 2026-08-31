import hashlib

from fastapi.testclient import TestClient

from scorer import server


def test_web_assets_are_versioned_and_not_cached():
    client = TestClient(server.app)
    expected_version = hashlib.sha256(
        server.BUILD_SHA.encode("utf-8")
    ).hexdigest()[:12]

    index_response = client.get("/")

    assert index_response.status_code == 200
    assert (
        f'src="/web/app.js?v={expected_version}"'
        in index_response.text
    )
    assert index_response.headers["cache-control"] == "no-store"

    asset_response = client.get("/web/app.js")

    assert asset_response.status_code == 200
    assert asset_response.headers["cache-control"] == "no-store"
