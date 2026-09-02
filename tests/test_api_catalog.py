"""Happy-path tests of the Forge catalog endpoints of the FastAPI adapter.

``sync_catalog`` is exercised against a respx-mocked Forge API; ``db_path``
always points into ``tmp_path`` so no test ever touches the real, per-user
catalog database. ``search_catalog``/``browse_catalog`` then read that same
cache back through the API.
"""

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from sptmod.core import game_dir
from sptmod.core.forge.client import DEFAULT_BASE_URL
from sptmod.server.app import app
from test_forge_client_fixtures import mod_json_minimal, page_envelope

client = TestClient(app)


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


@pytest.fixture
def game_dir_path(fake_game_dir: Path) -> Path:
    response = client.post("/api/init_game_dir", json={"path": str(fake_game_dir)})
    assert response.status_code == 200
    return fake_game_dir


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "forge-catalog.db"


def test_sync_catalog_upserts_the_mocked_forge_listing(
    game_dir_path: Path, db_path: Path
) -> None:
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").mock(return_value=httpx.Response(200, json=envelope))

        response = client.post(
            "/api/sync_catalog",
            json={"path": str(game_dir_path), "db_path": str(db_path)},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["new_mods"] == 1
    assert body["updated_mods"] == 0
    assert body["pages"] == 1
    assert body["operation_id"] != ""


def test_search_catalog_finds_a_synced_mod_by_name(
    game_dir_path: Path, db_path: Path
) -> None:
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").mock(return_value=httpx.Response(200, json=envelope))
        client.post(
            "/api/sync_catalog",
            json={"path": str(game_dir_path), "db_path": str(db_path)},
        )

    response = client.post(
        "/api/search_catalog",
        json={
            "path": str(game_dir_path),
            "query": "Example",
            "db_path": str(db_path),
        },
    )

    assert response.status_code == 200
    entries = response.json()
    assert [entry["mod"]["name"] for entry in entries] == ["Example Mod"]
    assert entries[0]["state"] == "available"
    assert entries[0]["installed_version"] is None


def test_search_catalog_finds_nothing_for_a_non_matching_query(
    game_dir_path: Path, db_path: Path
) -> None:
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").mock(return_value=httpx.Response(200, json=envelope))
        client.post(
            "/api/sync_catalog",
            json={"path": str(game_dir_path), "db_path": str(db_path)},
        )

    response = client.post(
        "/api/search_catalog",
        json={
            "path": str(game_dir_path),
            "query": "NoSuchMod",
            "db_path": str(db_path),
        },
    )

    assert response.status_code == 200
    assert response.json() == []


def test_browse_catalog_available_filter_keeps_the_uninstalled_synced_mod(
    game_dir_path: Path, db_path: Path
) -> None:
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").mock(return_value=httpx.Response(200, json=envelope))
        client.post(
            "/api/sync_catalog",
            json={"path": str(game_dir_path), "db_path": str(db_path)},
        )

    available = client.post(
        "/api/browse_catalog",
        json={
            "path": str(game_dir_path),
            "filter": "available",
            "db_path": str(db_path),
        },
    )
    installed = client.post(
        "/api/browse_catalog",
        json={
            "path": str(game_dir_path),
            "filter": "installed",
            "db_path": str(db_path),
        },
    )

    assert [entry["mod"]["name"] for entry in available.json()] == ["Example Mod"]
    assert installed.json() == []
