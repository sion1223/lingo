import hashlib
import json
import re

from fastapi.testclient import TestClient

from scorer import server


def test_web_assets_are_versioned_and_not_cached():
    client = TestClient(server.app)
    expected_version = hashlib.sha256(
        server.BUILD_SHA.encode("utf-8")
    ).hexdigest()[:12]

    index_response = client.get("/")

    assert index_response.status_code == 200
    import_map_match = re.search(
        r'<script type="importmap">\s*(.*?)\s*</script>',
        index_response.text,
        re.DOTALL,
    )
    assert import_map_match is not None
    import_map = json.loads(import_map_match.group(1))
    expected_imports = {
        f"/web/{path.relative_to(server.WEB_DIR).as_posix()}": (
            f"/web/{path.relative_to(server.WEB_DIR).as_posix()}"
            f"?v={expected_version}"
        )
        for path in server.WEB_DIR.rglob("*.js")
    }
    assert import_map == {"imports": expected_imports}
    assert '<script type="module">import "/web/app.js";</script>' in index_response.text
    assert index_response.headers["cache-control"] == "no-store"

    asset_response = client.get("/web/app.js")

    assert asset_response.status_code == 200
    assert asset_response.headers["cache-control"] == "no-store"
