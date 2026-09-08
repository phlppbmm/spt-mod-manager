"""Tests for ``services.plan_fika_install``: the two halves of Fika as one plan.

Fika is published as a server mod and a client plugin with their own versions
and dependencies. The catalog is seeded through a mocked ``/mods`` sync, the
dependency trees come from a mocked ``/mods/dependencies``; nothing is
downloaded and no request leaves the process.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from forge_json import mod_json_full, owner_json, page_envelope, version_json
from sptmod.core import services
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient
from sptmod.core.models import PlanEntry
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")

SPT_VERSION = "4.1.0"
SPT_CONSTRAINT = "^4.0.0"

SERVER_ID = 2357
SERVER_SLUG = "project-fika-server"
SERVER_NAME = "Project Fika - Server"
SERVER_GUID = "fika"
SERVER_VERSION = "2.4.0"
SERVER_VERSION_ID = 23570
SERVER_LINK = "https://dl.example/fika-server-2.4.0.zip"

CLIENT_ID = 2326
CLIENT_SLUG = "project-fika"
CLIENT_NAME = "Project Fika"
CLIENT_GUID = "com.fika.core"
CLIENT_VERSION = "2.4.2"
CLIENT_VERSION_ID = 23260
CLIENT_LINK = "https://dl.example/fika-2.4.2.zip"


def _half_json(*, mod_id: int, name: str, slug: str, guid: str, version: str, version_id: int) -> dict[str, Any]:
    """Build the ``/mods`` entry of one half of Fika."""
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=slug,
        guid=guid,
        owner=owner_json(id=mod_id * 10, name="Lacyway"),
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


def _versions_page(*, version: str, version_id: int, link: str, content_length: int) -> dict[str, Any]:
    """Build the ``/mod/{id}/versions`` page carrying the download link."""
    return page_envelope(
        [
            version_json(
                id=version_id,
                version=version,
                link=link,
                content_length=content_length,
                spt_version_constraint=SPT_CONSTRAINT,
            )
        ],
        total=1,
    )


def _seed_catalog(
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    *,
    halves: list[dict[str, Any]],
) -> ForgeClient:
    """Sync ``halves`` into the catalog and fill in their download links."""
    page = page_envelope(halves, total=len(halves))
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    with Catalog(db_path) as catalog:
        catalog.sync(client, full=True)
        for half in halves:
            summary = half["versions"][0]
            respx_router.get(f"/mod/{half['id']}/versions").mock(
                return_value=httpx.Response(
                    200,
                    json=_versions_page(
                        version=summary["version"],
                        version_id=summary["id"],
                        link=SERVER_LINK if half["id"] == SERVER_ID else CLIENT_LINK,
                        content_length=1000 if half["id"] == SERVER_ID else 2000,
                    ),
                )
            )
            catalog.fetch_versions(client, half["id"])
    return client


def _seed_both_halves(
    client_factory: Callable[..., ForgeClient], respx_router: respx.MockRouter, db_path: Path
) -> ForgeClient:
    """Sync the server mod and the client plugin, each at its own version."""
    return _seed_catalog(
        client_factory,
        respx_router,
        db_path,
        halves=[
            _half_json(
                mod_id=SERVER_ID,
                name=SERVER_NAME,
                slug=SERVER_SLUG,
                guid=SERVER_GUID,
                version=SERVER_VERSION,
                version_id=SERVER_VERSION_ID,
            ),
            _half_json(
                mod_id=CLIENT_ID,
                name=CLIENT_NAME,
                slug=CLIENT_SLUG,
                guid=CLIENT_GUID,
                version=CLIENT_VERSION,
                version_id=CLIENT_VERSION_ID,
            ),
        ],
    )


def _dep_node(
    *, mod_id: int, name: str, version: str, version_id: int, link: str, content_length: int
) -> dict[str, Any]:
    """Build one node of a ``/mods/dependencies`` tree."""
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


def _dependencies(respx_router: respx.MockRouter, trees: dict[str, list[dict[str, Any]]]) -> None:
    """Answer ``/mods/dependencies`` with the tree stored for the queried pair."""

    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: trees.get(pair, [])}})

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def _shared_lib() -> dict[str, Any]:
    """A dependency node both halves of Fika name."""
    return _dep_node(
        mod_id=500,
        name="SharedLib",
        version="1.2.3",
        version_id=5000,
        link="https://dl.example/sharedlib-1.2.3.zip",
        content_length=300,
    )


def _server_entry() -> PlanEntry:
    """The plan entry of the server mod as the seeded catalog describes it."""
    return PlanEntry(
        mod_id=SERVER_ID,
        name=SERVER_NAME,
        guid=SERVER_GUID,
        version=SERVER_VERSION,
        version_id=SERVER_VERSION_ID,
        link=SERVER_LINK,
        content_length=1000,
        is_dependency=False,
    )


def _client_entry() -> PlanEntry:
    """The plan entry of the client plugin as the seeded catalog describes it."""
    return PlanEntry(
        mod_id=CLIENT_ID,
        name=CLIENT_NAME,
        guid=CLIENT_GUID,
        version=CLIENT_VERSION,
        version_id=CLIENT_VERSION_ID,
        link=CLIENT_LINK,
        content_length=2000,
        is_dependency=False,
    )


def _entry_of(node: dict[str, Any]) -> PlanEntry:
    """The plan entry a dependency node is planned as."""
    version = node["latest_compatible_version"]
    return PlanEntry(
        mod_id=node["id"],
        name=node["name"],
        guid=None,
        version=version["version"],
        version_id=version["id"],
        link=version["link"],
        content_length=version["content_length"],
        is_dependency=True,
    )


# --- the combined plan ------------------------------------------------------


def test_the_plan_installs_the_server_half_with_its_dependencies_first(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If the body is `pass`, this fails: no plan is returned at all. The two
    halves are published separately, so each keeps its own version (2.4.0 and
    2.4.2) and its own dependency, and the server mod and everything it needs
    come before the client plugin -- a client plugin without its server is
    what a player must never end up with."""
    client = _seed_both_halves(client_factory, respx_router, db_path)
    server_dep = _dep_node(
        mod_id=600, name="ServerLib", version="2.0.0", version_id=6000,
        link="https://dl.example/serverlib-2.0.0.zip", content_length=400,
    )
    client_dep = _dep_node(
        mod_id=700, name="ClientLib", version="3.0.0", version_id=7000,
        link="https://dl.example/clientlib-3.0.0.zip", content_length=500,
    )
    _dependencies(
        respx_router,
        {
            f"{SERVER_ID}:{SERVER_VERSION}": [server_dep],
            f"{CLIENT_ID}:{CLIENT_VERSION}": [client_dep],
        },
    )

    plan = services.plan_fika_install(clean_repo.path, db_path=db_path, client=client)

    assert plan.server_mod_id == SERVER_ID
    assert plan.client_mod_id == CLIENT_ID
    assert plan.plan.spt_version == SPT_VERSION
    assert plan.plan.entries == [
        _entry_of(server_dep),
        _server_entry(),
        _entry_of(client_dep),
        _client_entry(),
    ]


def test_a_mod_both_halves_need_is_planned_once(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If the two plans were only concatenated, this fails: the mod both
    halves depend on would appear twice and be downloaded and installed
    twice, the second time over the first."""
    client = _seed_both_halves(client_factory, respx_router, db_path)
    shared = _shared_lib()
    _dependencies(
        respx_router,
        {
            f"{SERVER_ID}:{SERVER_VERSION}": [shared],
            f"{CLIENT_ID}:{CLIENT_VERSION}": [shared],
        },
    )

    plan = services.plan_fika_install(clean_repo.path, db_path=db_path, client=client)

    assert plan.plan.entries == [_entry_of(shared), _server_entry(), _client_entry()]


def test_the_plan_is_resolved_with_a_forge_client_of_its_own_when_none_is_given(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If the branch building its own client were dropped, this fails: the
    call raises instead of resolving the same plan. The app and the CLI both
    call this function without handing in a client."""
    _seed_both_halves(client_factory, respx_router, db_path)
    _dependencies(respx_router, {})

    plan = services.plan_fika_install(clean_repo.path, db_path=db_path)

    assert plan.plan.entries == [_server_entry(), _client_entry()]


# --- a half the catalog does not know ---------------------------------------


def test_an_empty_catalog_names_the_server_mod_that_is_missing(
    clean_repo: Repo, client_factory: Callable[..., ForgeClient], db_path: Path
) -> None:
    """If the lookup failure were passed on unnamed, this fails: the message
    would not say which half is missing, and a player would not know that
    syncing the catalog is what fixes it."""
    with pytest.raises(NoCompatibleVersionError, match=r"^Project Fika - Server is not in the mod catalog\."):
        services.plan_fika_install(clean_repo.path, db_path=db_path, client=client_factory())


def test_a_catalog_holding_only_the_server_half_names_the_client_plugin(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If both halves were looked up under one name, this fails: the message
    would name the server mod, which is in the catalog, instead of the client
    plugin that is missing."""
    client = _seed_catalog(
        client_factory,
        respx_router,
        db_path,
        halves=[
            _half_json(
                mod_id=SERVER_ID,
                name=SERVER_NAME,
                slug=SERVER_SLUG,
                guid=SERVER_GUID,
                version=SERVER_VERSION,
                version_id=SERVER_VERSION_ID,
            )
        ],
    )

    with pytest.raises(NoCompatibleVersionError, match=r"^Project Fika is not in the mod catalog\."):
        services.plan_fika_install(clean_repo.path, db_path=db_path, client=client)
