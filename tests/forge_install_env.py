"""Builders for the Forge install tests: seeded catalogs, dependency nodes and download mocks.

Imported directly by the modules covering the Forge install path
(``test_services_forge_install``, ``test_services_forge_plan`` and
``test_services_forge_cache``), the way :mod:`forge_json` and
:mod:`pe_builder` are; it is not a pytest plugin and registers no fixtures.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import respx

from fixture_types import MakeArchive
from forge_json import mod_json_full, owner_json, page_envelope, version_json
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient

SPT_VERSION = "4.1.0"
SPT_CONSTRAINT = "^4.0.0"


def seed_target(
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    *,
    mod_id: int,
    name: str,
    version: str,
    version_id: int,
    link: str,
    content_length: int,
) -> ForgeClient:
    """Sync one mod into the catalog and fill in its version's link/content_length.

    Mirrors ``_seed_target`` of ``test_forge_resolve.py``: a plain ``sync``
    only caches the version *summary* (no link/content_length), so a
    ``fetch_versions`` call is required for those fields to be present.
    """
    slug = name.lower().replace(" ", "-")
    mod = mod_json_full(
        id=mod_id,
        name=name,
        slug=slug,
        owner=owner_json(id=mod_id * 10, name="Alice"),
        detail_url=f"{DEFAULT_BASE_URL}/mods/{slug}",
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
    client = client_factory()
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)
        full_versions_page = page_envelope(
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
        respx_router.get(f"/mod/{mod_id}/versions").mock(return_value=httpx.Response(200, json=full_versions_page))
        cat.fetch_versions(client, mod_id)
    return client


def seed_target_without_fetch_versions(
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    *,
    mod_id: int,
    name: str,
    version: str,
    version_id: int,
    link: str,
    content_length: int,
) -> ForgeClient:
    """Sync one mod but stop short of ``fetch_versions``.

    The cached version stays a summary with ``link=None`` and no
    ``content_length``, mirroring what a live ``sync_catalog`` (without a
    later, separate ``fetch_versions`` call) leaves behind. The
    ``/mod/{id}/versions`` route is still registered so a service that fixes
    the gap by calling ``fetch_versions`` itself can resolve the real link.
    """
    slug = name.lower().replace(" ", "-")
    mod = mod_json_full(
        id=mod_id,
        name=name,
        slug=slug,
        owner=owner_json(id=mod_id * 10, name="Alice"),
        detail_url=f"{DEFAULT_BASE_URL}/mods/{slug}",
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
    client = client_factory()
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)

    full_versions_page = page_envelope(
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
    respx_router.get(f"/mod/{mod_id}/versions").mock(return_value=httpx.Response(200, json=full_versions_page))
    return client


def echo_dependencies(respx_router: respx.MockRouter, nodes: list[dict[str, Any]]) -> None:
    """Mock ``/mods/dependencies`` echoing the queried pair as the response key."""

    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: nodes}})

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def dep_node(
    *,
    mod_id: int,
    name: str,
    version: str,
    version_id: int,
    link: str,
    content_length: int,
) -> dict[str, Any]:
    """Build a dependency node dict for the /mods/dependencies API response.

    Returns a dict with latest_compatible_version containing link and content_length,
    conflict=False, and empty dependencies list.
    """
    return {
        "id": mod_id,
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


def package_archive(make_archive: MakeArchive, folder: str, name: str, version: str) -> Path:
    """Create a server mod archive with package.json and return the archive path.

    The archive contains SPT/user/mods/{folder}/package.json with given name,
    version, and author='Acme'.
    """
    return make_archive(
        {f"SPT/user/mods/{folder}/package.json": json.dumps({"name": name, "version": version, "author": "Acme"})},
        "zip",
    )


def mock_download(respx_router: respx.MockRouter, link: str, archive: Path) -> int:
    """Mock a GET request for link to serve archive bytes with Content-Length header.

    Returns the archive size (len(archive.read_bytes())).
    """
    body = archive.read_bytes()
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    return len(body)


def mock_download_route(respx_router: respx.MockRouter, link: str, archive: Path) -> respx.Route:
    """Like ``mock_download``, but returns the ``Route`` for call counting."""
    body = archive.read_bytes()
    return respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )


def seed_download_cache_entry(cache_dir: Path, version_id: int, name: str, payload: bytes) -> Path:
    """Write a complete archive directly into the download cache's on-disk
    layout (``<key>/meta.json`` + ``<key>/<name>``, per
    ``sptmod.core.forge.cache``'s module docstring), bypassing any real
    download so tests can arrange a pre-existing cache hit or a stale entry.
    """
    entry = cache_dir / str(version_id)
    entry.mkdir(parents=True)
    (entry / name).write_bytes(payload)
    (entry / "meta.json").write_text(
        json.dumps({"name": name, "size": len(payload), "url": "seeded-for-test"}),
        encoding="utf-8",
    )
    return entry
