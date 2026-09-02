"""``create_app`` serving the built frontend as static files.

Contract, per the ``SPTMOD_STATIC_DIR`` environment variable: when it names a
directory holding the built frontend (an ``index.html`` plus other assets),
``create_app()`` serves ``index.html`` at ``GET /``, falls back to it for any
other non-``/api`` ``GET`` (SPA client-side routing), and serves the other
files of that directory verbatim. ``/api/*`` is unaffected either way (health,
a sample POST, and an unknown ``/api`` route staying a JSON 404). Without the
variable set, ``GET /`` stays a plain 404.

Every test builds a fresh app via ``create_app()`` (never the module-level
``sptmod.server.app.app`` singleton, which is built once at import time), so
setting/clearing the env var cannot leak into the rest of the suite.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sptmod.server.app import create_app


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "frontend-build"
    root.mkdir()
    (root / "index.html").write_text(
        "<html><body>sptmod ui</body></html>", encoding="utf-8"
    )
    (root / "app.js").write_text("console.log('asset');", encoding="utf-8")
    return root


@pytest.fixture
def static_client(
    static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("SPTMOD_STATIC_DIR", str(static_dir))
    return TestClient(create_app())


def test_get_root_serves_index_html_when_static_dir_configured(
    static_client: TestClient, static_dir: Path
) -> None:
    response = static_client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (static_dir / "index.html").read_text(encoding="utf-8")


def test_unknown_client_route_falls_back_to_index_html(
    static_client: TestClient, static_dir: Path
) -> None:
    response = static_client.get("/some-client-route")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (static_dir / "index.html").read_text(encoding="utf-8")


def test_asset_file_is_served_verbatim(static_client: TestClient) -> None:
    response = static_client.get("/app.js")

    assert response.status_code == 200
    assert response.text == "console.log('asset');"


def test_health_endpoint_still_works_with_static_dir_configured(
    static_client: TestClient,
) -> None:
    response = static_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_sample_post_endpoint_still_works_with_static_dir_configured(
    static_client: TestClient, tmp_path: Path
) -> None:
    missing = tmp_path / "not-a-game-dir"
    missing.mkdir()

    response = static_client.post(
        "/api/init_game_dir", json={"path": str(missing)}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "NOT_A_GAME_DIR"


def test_unknown_api_route_still_returns_404_json_with_static_dir_configured(
    static_client: TestClient,
) -> None:
    response = static_client.get("/api/xyz")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_get_root_returns_the_plain_404_without_a_static_dir_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SPTMOD_STATIC_DIR", raising=False)
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 404
