"""API-level smoke tests for the Forge install endpoints of the FastAPI adapter.

Drives ``/api/plan_mod_install``, ``/api/install_mod`` and
``/api/clear_catalog_cache`` through ``TestClient`` against a mocked Forge
client (the app builds its own ``ForgeClient()`` since the request schemas
carry no client injection; respx patches httpx process-wide for the duration
of the test).
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from sptmod.core import game_dir
from sptmod.core.forge.catalog import Catalog
from sptmod.server.app import app
from test_forge_catalog_fixtures import (
    client_factory,
    db_path,
    fake_clock,
    recording_sleep,
    respx_router,
)
from test_forge_client_fixtures import (
    mod_json_full,
    owner_json,
    page_envelope,
    version_json,
)

__all__ = ["client_factory", "db_path", "fake_clock", "recording_sleep", "respx_router"]

client = TestClient(app)

SPT_CONSTRAINT = "^4.0.0"


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


@pytest.fixture
def game_dir_path(fake_game_dir: Path) -> Path:
    response = client.post("/api/init_game_dir", json={"path": str(fake_game_dir)})
    assert response.status_code == 200
    return fake_game_dir


def _seed_target(
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    *,
    mod_id: int,
    name: str,
    version: str,
    version_id: int,
    link: str,
    content_length: int,
) -> None:
    slug = name.lower().replace(" ", "-")
    mod = mod_json_full(
        id=mod_id,
        name=name,
        slug=slug,
        owner=owner_json(id=mod_id * 10, name="Alice"),
        versions=[
            {
                "id": version_id,
                "version": version,
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    catalog_client = client_factory()
    with Catalog(db_path) as cat:
        cat.sync(catalog_client, full=True)
        versions_page = page_envelope(
            [
                version_json(
                    id=version_id,
                    version=version,
                    link=link,
                    content_length=content_length,
                    spt_version_constraint=SPT_CONSTRAINT,
                )
            ],
            current_page=1,
            last_page=1,
            total=1,
            next_url=None,
        )
        respx_router.get(f"/mod/{mod_id}/versions").mock(
            return_value=httpx.Response(200, json=versions_page)
        )
        cat.fetch_versions(catalog_client, mod_id)


def _echo_empty_dependencies(respx_router: respx.MockRouter) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def _echo_dependencies(respx_router: respx.MockRouter, nodes: list[dict]) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: nodes}})

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def _dep_node(
    *, id: int, name: str, version: str, version_id: int, link: str, content_length: int
) -> dict:
    return {
        "id": id,
        "guid": None,
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "latest_compatible_version": {
            "id": version_id,
            "version": version,
            "link": link,
            "content_length": content_length,
            "fika_compatibility": "unknown",
        },
        "conflict": False,
        "dependencies": [],
    }


def test_plan_mod_install_endpoint_returns_the_plan_shape(
    game_dir_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=901,
        name="Target Mod",
        version="2.0.0",
        version_id=9010,
        link="https://dl.example/target-mod-2.0.0.zip",
        content_length=1000,
    )
    _echo_empty_dependencies(respx_router)

    response = client.post(
        "/api/plan_mod_install",
        json={
            "path": str(game_dir_path),
            "mod_id_or_name": "Target Mod",
            "version": "2.0.0",
            "db_path": str(db_path),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["spt_version"] == "4.1.0"
    assert body["entries"] == [
        {
            "mod_id": 901,
            "name": "Target Mod",
            "guid": "com.example.mod",
            "version": "2.0.0",
            "version_id": 9010,
            "link": "https://dl.example/target-mod-2.0.0.zip",
            "content_length": 1000,
            "is_dependency": False,
        }
    ]


def test_install_mod_endpoint_echoes_operation_id_and_returns_the_installed_mod(
    game_dir_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
) -> None:
    archive = make_archive(
        {
            "SPT/user/mods/Target Mod/package.json": json.dumps(
                {"name": "Target Mod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = archive.read_bytes()
    link = "https://dl.example/target-mod-2.0.0.zip"
    _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=902,
        name="Target Mod",
        version="2.0.0",
        version_id=9020,
        link=link,
        content_length=len(body),
    )
    _echo_empty_dependencies(respx_router)
    respx_router.get(link).mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Length": str(len(body))}
        )
    )

    response = client.post(
        "/api/install_mod",
        json={
            "path": str(game_dir_path),
            "mod_id_or_name": "Target Mod",
            "version": "2.0.0",
            "db_path": str(db_path),
            "operation_id": "caller-chosen-id",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["operation_id"] == "caller-chosen-id"
    assert result["plan"]["entries"][0]["name"] == "Target Mod"
    assert len(result["installed"]) == 1
    installed_mod = result["installed"][0]["mod"]
    assert installed_mod["name"] == "Target Mod"
    assert installed_mod["version"] == "2.0.0"
    assert installed_mod["forge_id"] == "902"
    assert installed_mod["forge_version_id"] == "9020"
    assert installed_mod["source"] == link


def test_install_mod_endpoint_maps_partial_install_error_to_409_with_installed_names(
    game_dir_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
) -> None:
    """If PartialInstallError has no error mapping yet, this fails: either the
    status is not 409 or details.installed is missing 'Dep Mod'."""
    target_archive = make_archive(
        {
            "SPT/user/mods/Target Mod/package.json": json.dumps(
                {"name": "Target Mod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    dep_archive = make_archive(
        {
            "SPT/user/mods/Dep Mod/package.json": json.dumps(
                {"name": "Dep Mod", "version": "1.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    target_body = target_archive.read_bytes()
    dep_body = dep_archive.read_bytes()
    target_link = "https://dl.example/target-mod-2.0.0-partial.zip"
    dep_link = "https://dl.example/dep-mod-1.0.0-partial.zip"
    _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=911,
        name="Target Mod",
        version="2.0.0",
        version_id=9110,
        link=target_link,
        content_length=len(target_body) + 1,  # deliberately wrong: size mismatch
    )
    _echo_dependencies(
        respx_router,
        [
            _dep_node(
                id=912,
                name="Dep Mod",
                version="1.0.0",
                version_id=9120,
                link=dep_link,
                content_length=len(dep_body),
            )
        ],
    )
    respx_router.get(dep_link).mock(
        return_value=httpx.Response(
            200, content=dep_body, headers={"Content-Length": str(len(dep_body))}
        )
    )
    respx_router.get(target_link).mock(
        return_value=httpx.Response(
            200, content=target_body, headers={"Content-Length": str(len(target_body))}
        )
    )

    response = client.post(
        "/api/install_mod",
        json={
            "path": str(game_dir_path),
            "mod_id_or_name": "Target Mod",
            "version": "2.0.0",
            "db_path": str(db_path),
        },
    )

    assert response.status_code == 409
    assert response.json()["details"]["installed"] == ["Dep Mod"]


# --------------------------------------------------------------------------
# check_updates / update_mod
# --------------------------------------------------------------------------


def test_check_updates_endpoint_reports_an_available_update(
    game_dir_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit,
) -> None:
    from sptmod.core.repo import Repo

    repo = Repo(game_dir_path)
    install_commit(
        repo, "CoolMod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "10"}
    )
    data = {
        "spt_version": "4.1.0",
        "updates": [
            {
                "current_version": {
                    "id": 100,
                    "mod_id": 10,
                    "guid": None,
                    "name": "CoolMod",
                    "version": "1.0.0",
                    "slug": "coolmod",
                },
                "recommended_version": {
                    "id": 200,
                    "version": "2.0.0",
                    "link": "https://dl.example/coolmod-2.0.0.zip",
                    "content_length": 4242,
                    "fika_compatibility": "unknown",
                    "spt_versions": ["4.1.0"],
                },
                "update_reason": "newer version available",
            }
        ],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(200, json={"success": True, "data": data})
    )

    response = client.post(
        "/api/check_updates", json={"path": str(game_dir_path), "db_path": str(db_path)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] == [
        {
            "name": "CoolMod",
            "installed_version": "1.0.0",
            "new_version": "2.0.0",
            "version_id": 200,
            "link": "https://dl.example/coolmod-2.0.0.zip",
            "content_length": 4242,
            "reason": "newer version available",
        }
    ]


def test_update_mod_endpoint_applies_the_recommended_version(
    game_dir_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit,
    make_archive,
) -> None:
    from sptmod.core.repo import Repo

    repo = Repo(game_dir_path)
    install_commit(
        repo, "CoolMod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "10"}
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/coolmod-2.0.0.zip"
    data = {
        "spt_version": "4.1.0",
        "updates": [
            {
                "current_version": {
                    "id": 100,
                    "mod_id": 10,
                    "guid": None,
                    "name": "CoolMod",
                    "version": "1.0.0",
                    "slug": "coolmod",
                },
                "recommended_version": {
                    "id": 200,
                    "version": "2.0.0",
                    "link": link,
                    "content_length": len(body),
                    "fika_compatibility": "unknown",
                    "spt_versions": ["4.1.0"],
                },
                "update_reason": "newer version available",
            }
        ],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(200, json={"success": True, "data": data})
    )
    respx_router.get(link).mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Length": str(len(body))}
        )
    )

    response = client.post(
        "/api/update_mod",
        json={
            "path": str(game_dir_path),
            "name": "CoolMod",
            "db_path": str(db_path),
            "operation_id": "update-op-id",
        },
    )

    assert response.status_code == 200
    body_json = response.json()
    assert body_json["operation_id"] == "update-op-id"
    assert body_json["name"] == "CoolMod"
    assert body_json["previous_version"] == "1.0.0"
    assert body_json["version"] == "2.0.0"
    assert body_json["commit"] != ""
    assert body_json["notice"] == ""


# --------------------------------------------------------------------------
# clear_catalog_cache
# --------------------------------------------------------------------------


def test_clear_catalog_cache_endpoint_deletes_the_db_file(tmp_path: Path) -> None:
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    assert db.exists()

    response = client.post("/api/clear_catalog_cache", json={"db_path": str(db)})

    assert response.status_code == 200
    assert response.json() == {"db_path": str(db), "removed": True}
    assert not db.exists()

    second = client.post("/api/clear_catalog_cache", json={"db_path": str(db)})
    assert second.json()["removed"] is False
