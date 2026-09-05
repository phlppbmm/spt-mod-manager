"""Tests for the Forge install path of ``sptmod.core.services``.

Covers ``plan_mod_install``, ``install_mod`` (end-to-end with a mocked Forge
client and fake archives served through the download mock route),
``install_from_archive``'s ``forge_match`` catalog lookup, ``import_list``
with ``apply=True`` and the ``content`` parameter, and
``clear_catalog_cache``.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from conftest import forge_trailers
from fixture_types import InstallCommit, JsonDict, MakeArchive
from forge_json import (
    current_version_json,
    mod_json_full,
    owner_json,
    page_envelope,
    recommended_version_json,
    success_envelope,
    updates_payload,
    version_json,
)
from sptmod.cli import app as cli_app
from sptmod.core import services
from sptmod.core.errors import ArchiveError, ConflictError, NotAGameDirError, PartialInstallError
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient
from sptmod.core.forge.download import CACHE_HIT_MESSAGE
from sptmod.core.models import PlanEntry
from sptmod.core.repo import Repo
from sptmod.core.workspace import download_cache_dir, staging_dir

pytestmark = pytest.mark.usefixtures("not_running")

SPT_VERSION = "4.1.0"
SPT_CONSTRAINT = "^4.0.0"

cli_runner = CliRunner()


def _seed_target(
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


def _seed_target_without_fetch_versions(
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


def _echo_dependencies(respx_router: respx.MockRouter, nodes: list[dict[str, Any]]) -> None:
    """Mock ``/mods/dependencies`` echoing the queried pair as the response key."""

    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: nodes}})

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def _dep_node(
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


def _package_archive(make_archive: MakeArchive, folder: str, name: str, version: str) -> Path:
    """Create a server mod archive with package.json and return the archive path.

    The archive contains SPT/user/mods/{folder}/package.json with given name,
    version, and author='Acme'.
    """
    return make_archive(
        {f"SPT/user/mods/{folder}/package.json": json.dumps({"name": name, "version": version, "author": "Acme"})},
        "zip",
    )


def _mock_download(respx_router: respx.MockRouter, link: str, archive: Path) -> int:
    """Mock a GET request for link to serve archive bytes with Content-Length header.

    Returns the archive size (len(archive.read_bytes())).
    """
    body = archive.read_bytes()
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    return len(body)


def _mock_download_route(respx_router: respx.MockRouter, link: str, archive: Path) -> respx.Route:
    """Like ``_mock_download``, but returns the ``Route`` for call counting."""
    body = archive.read_bytes()
    return respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )


def _seed_download_cache_entry(cache_dir: Path, version_id: int, name: str, payload: bytes) -> Path:
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


# --- plan_mod_install -------------------------------------------------------


def test_plan_mod_install_orders_dependency_before_the_requested_mod(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If plan_mod_install's body is `pass`, this fails: returns None instead
    of an InstallPlan with the dependency ordered before the target."""
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=101,
        name="Target Mod",
        version="2.0.0",
        version_id=1010,
        link="https://dl.example/target-2.0.0.zip",
        content_length=1000,
    )
    _echo_dependencies(
        respx_router,
        [
            _dep_node(
                mod_id=102,
                name="Dep Mod",
                version="1.0.0",
                version_id=1020,
                link="https://dl.example/dep-1.0.0.zip",
                content_length=500,
            )
        ],
    )

    plan = services.plan_mod_install(clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client)

    assert plan.spt_version == SPT_VERSION
    assert plan.entries == [
        PlanEntry(
            mod_id=102,
            name="Dep Mod",
            guid=None,
            version="1.0.0",
            version_id=1020,
            link="https://dl.example/dep-1.0.0.zip",
            content_length=500,
            is_dependency=True,
        ),
        PlanEntry(
            mod_id=101,
            name="Target Mod",
            guid="com.example.mod",
            version="2.0.0",
            version_id=1010,
            link="https://dl.example/target-2.0.0.zip",
            content_length=1000,
            is_dependency=False,
        ),
    ]


def test_plan_mod_install_resolves_a_link_missing_from_a_sync_only_catalog(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """``sync`` alone caches only the version *summary* (no link); the plan
    must still carry a usable download link -- the service must call
    ``fetch_versions`` itself for entries whose cached link is ``None``.
    """
    client = _seed_target_without_fetch_versions(
        client_factory,
        respx_router,
        db_path,
        mod_id=901,
        name="Link Mod",
        version="1.0.0",
        version_id=9010,
        link="https://dl.example/link-mod-1.0.0.zip",
        content_length=800,
    )
    _echo_dependencies(respx_router, [])

    plan = services.plan_mod_install(clean_repo.path, "Link Mod", "1.0.0", db_path=db_path, client=client)

    assert plan.entries[0].link == "https://dl.example/link-mod-1.0.0.zip"
    assert plan.entries[0].content_length == 800


def test_install_mod_succeeds_when_the_catalog_was_only_synced(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """install_mod resolves a missing download link via fetch_versions."""
    archive = _package_archive(make_archive, "LinkMod", "Link Mod", "1.0.0")
    link = "https://dl.example/link-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = _seed_target_without_fetch_versions(
        client_factory,
        respx_router,
        db_path,
        mod_id=902,
        name="Link Mod",
        version="1.0.0",
        version_id=9020,
        link=link,
        content_length=content_length,
    )
    _echo_dependencies(respx_router, [])
    _mock_download(respx_router, link, archive)

    result = services.install_mod(clean_repo.path, "Link Mod", "1.0.0", db_path=db_path, client=client)

    assert [entry.mod.name for entry in result.installed] == ["Link Mod"]
    assert (clean_repo.path / "SPT/user/mods/LinkMod/package.json").is_file()


def test_install_mod_raises_archive_error_when_forge_no_longer_lists_the_version(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """The cached version *summary* never carries a link, so _with_link must
    call fetch_versions to resolve one; when Forge's full /mod/{id}/versions
    listing no longer contains the exact cached version id (deleted between
    sync and install), `_with_link`'s `if fetched is None: return entry`
    leaves the entry link-less. If that guard were removed (KeyError/index
    into an empty match) or `_download_entry`'s `if not link: raise
    ArchiveError(...)` guard were removed, this fails: a crash instead of a
    clear ArchiveError, or an attempted empty download."""
    mod = mod_json_full(
        id=910,
        name="GhostLink",
        slug="ghostlink",
        owner=owner_json(id=9100, name="Alice"),
        detail_url=f"{DEFAULT_BASE_URL}/mods/ghostlink",
        versions=[
            {
                "id": 9100,
                "version": "1.0.0",
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
    empty_versions = page_envelope([], current_page=1, last_page=1, total=0, next_url=None)
    respx_router.get("/mod/910/versions").mock(return_value=httpx.Response(200, json=empty_versions))
    _echo_dependencies(respx_router, [])

    with pytest.raises(ArchiveError):
        services.install_mod(clean_repo.path, "GhostLink", "1.0.0", db_path=db_path, client=client)


def test_install_mod_succeeds_when_the_download_link_ends_in_a_bare_version(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """Regression for the WTT - Content Backport bug: a Forge download link
    whose final path segment is a bare version number -- no file extension,
    and (as here) no Content-Disposition header to override it -- becomes the
    local file name via download()'s URL-path fallback. Installation must
    still succeed: extract() detects the archive format from magic bytes
    rather than trusting that name.
    """
    archive = _package_archive(make_archive, "BareVersionMod", "Bare Version Mod", "2.0.1")
    link = "https://dl.example/2.0.1"
    content_length = len(archive.read_bytes())
    client = _seed_target_without_fetch_versions(
        client_factory,
        respx_router,
        db_path,
        mod_id=903,
        name="Bare Version Mod",
        version="2.0.1",
        version_id=9030,
        link=link,
        content_length=content_length,
    )
    _echo_dependencies(respx_router, [])
    _mock_download(respx_router, link, archive)

    result = services.install_mod(clean_repo.path, "Bare Version Mod", "2.0.1", db_path=db_path, client=client)

    assert [entry.mod.name for entry in result.installed] == ["Bare Version Mod"]
    assert (clean_repo.path / "SPT/user/mods/BareVersionMod/package.json").is_file()


def test_plan_mod_install_unknown_mod_raises_no_compatible_version_error(
    clean_repo: Repo, client_factory: Callable[..., ForgeClient], db_path: Path
) -> None:
    """If plan_mod_install's body is `pass`, this fails: no exception raised
    for a mod absent from the (empty) catalog."""
    client = client_factory()

    with pytest.raises(NoCompatibleVersionError):
        services.plan_mod_install(clean_repo.path, "Nonexistent Mod", db_path=db_path, client=client)


def test_plan_mod_install_raises_not_a_game_dir_when_spt_version_unknown(
    clean_repo: Repo, client_factory: Callable[..., ForgeClient], db_path: Path
) -> None:
    """If plan_mod_install's body is `pass`, this fails: no exception raised
    when the game dir's core.json carries no readable SPT version."""
    (clean_repo.path / "SPT/SPT_Data/configs/core.json").write_text("not json", encoding="utf-8")
    client = client_factory()

    with pytest.raises(NotAGameDirError):
        services.plan_mod_install(clean_repo.path, "Target Mod", db_path=db_path, client=client)


# --- install_mod --------------------------------------------------------


def test_install_mod_installs_missing_dependency_as_its_own_commit_before_the_target(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """If install_mod's body is `pass`, this fails: no commits are created and
    the returned result is None instead of an InstallModResult."""
    target_archive = _package_archive(make_archive, "Target Mod", "Target Mod", "2.0.0")
    dep_archive = _package_archive(make_archive, "Dep Mod", "Dep Mod", "1.0.0")
    target_length = len(target_archive.read_bytes())
    dep_length = len(dep_archive.read_bytes())
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=201,
        name="Target Mod",
        version="2.0.0",
        version_id=2010,
        link="https://dl.example/target-mod-2.0.0.zip",
        content_length=target_length,
    )
    _echo_dependencies(
        respx_router,
        [
            _dep_node(
                mod_id=202,
                name="Dep Mod",
                version="1.0.0",
                version_id=2020,
                link="https://dl.example/dep-mod-1.0.0.zip",
                content_length=dep_length,
            )
        ],
    )
    _mock_download(respx_router, "https://dl.example/target-mod-2.0.0.zip", target_archive)
    _mock_download(respx_router, "https://dl.example/dep-mod-1.0.0.zip", dep_archive)

    result = services.install_mod(clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client)

    assert [entry.name for entry in result.plan.entries] == ["Dep Mod", "Target Mod"]
    assert [item.mod.name for item in result.installed] == ["Dep Mod", "Target Mod"]
    dep_result, target_result = result.installed
    assert dep_result.mod.forge_id == "202"
    assert dep_result.mod.forge_version_id == "2020"
    assert dep_result.mod.source == "https://dl.example/dep-mod-1.0.0.zip"
    assert target_result.mod.forge_id == "201"
    assert target_result.mod.forge_version_id == "2010"
    assert target_result.mod.source == "https://dl.example/target-mod-2.0.0.zip"

    commits = Repo(clean_repo.path).log()
    subjects = [commit.subject for commit in commits]
    assert subjects[:2] == ["mod: Target Mod 2.0.0", "mod: Dep Mod 1.0.0"]
    assert commits[0].trailers["Mod-Forge-Id"] == "201"
    assert commits[0].trailers["Mod-Source"] == "https://dl.example/target-mod-2.0.0.zip"
    assert commits[1].trailers["Mod-Forge-Id"] == "202"
    assert commits[1].trailers["Mod-Source"] == "https://dl.example/dep-mod-1.0.0.zip"


def test_install_mod_writes_the_plan_version_when_the_archive_carries_none(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """A bare-DLL client archive carries no embedded version
    (``meta_from_dll`` leaves it empty). The commit's ``Mod-Version`` must
    still be the version Forge resolved for the plan entry, not empty --
    otherwise the mod becomes uncheckable for updates afterwards (see
    ``test_check_updates_after_a_metadata_less_install_queries_the_plan_version``).
    """
    archive = make_archive({"BepInEx/plugins/VersionlessMod.dll": b"dll-bytes"}, "zip")
    body = archive.read_bytes()
    link = "https://dl.example/versionless-mod-3.2.1.zip"
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=903,
        name="Versionless Mod",
        version="3.2.1",
        version_id=9030,
        link=link,
        content_length=len(body),
    )
    _echo_dependencies(respx_router, [])
    _mock_download(respx_router, link, archive)

    result = services.install_mod(clean_repo.path, "Versionless Mod", "3.2.1", db_path=db_path, client=client)

    assert result.installed[0].mod.version == "3.2.1"
    commit = Repo(clean_repo.path).log()[0]
    assert commit.trailers["Mod-Version"] == "3.2.1"


def test_check_updates_after_a_metadata_less_install_queries_the_plan_version(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """check_updates sends "904:3.2.1" for a bare-DLL mod with no package.json."""
    archive = make_archive({"BepInEx/plugins/VersionlessMod.dll": b"dll-bytes"}, "zip")
    body = archive.read_bytes()
    link = "https://dl.example/versionless-mod-3.2.1.zip"
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=904,
        name="Versionless Mod",
        version="3.2.1",
        version_id=9040,
        link=link,
        content_length=len(body),
    )
    _echo_dependencies(respx_router, [])
    _mock_download(respx_router, link, archive)
    services.install_mod(clean_repo.path, "Versionless Mod", "3.2.1", db_path=db_path, client=client)

    captured: dict[str, str] = {}

    def _updates_handler(request: httpx.Request) -> httpx.Response:
        captured["mods"] = request.url.params["mods"]
        return httpx.Response(
            200,
            json=success_envelope(
                {
                    "updates": [],
                    "blocked_updates": [],
                    "up_to_date": [],
                    "incompatible_with_spt": [],
                }
            ),
        )

    respx_router.get("/mods/updates").mock(side_effect=_updates_handler)

    services.check_updates(clean_repo.path, db_path=db_path, client=client)

    assert captured["mods"] == "904:3.2.1"


def test_install_mod_aborts_on_a_file_conflict_without_force(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    install_commit: InstallCommit,
) -> None:
    """If install_mod's body is `pass`, this fails: no ConflictError is raised
    and the file-conflict check is never reached."""
    archive = _package_archive(make_archive, "Solo Mod", "Solo Mod", "1.0.0")
    length = _mock_download(respx_router, "https://dl.example/solo-mod-1.0.0.zip", archive)
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=301,
        name="Solo Mod",
        version="1.0.0",
        version_id=3010,
        link="https://dl.example/solo-mod-1.0.0.zip",
        content_length=length,
    )
    _echo_dependencies(respx_router, [])
    install_commit(
        clean_repo,
        "Blocker",
        "1.0.0",
        mod_type="server",
        files={"SPT/user/mods/Solo Mod/package.json": "existing content"},
    )
    commits_before = len(clean_repo.log())

    with pytest.raises(ConflictError):
        services.install_mod(clean_repo.path, "Solo Mod", "1.0.0", db_path=db_path, client=client)

    assert len(clean_repo.log()) == commits_before


def test_install_mod_force_downgrades_the_conflict_and_installs(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    install_commit: InstallCommit,
) -> None:
    """If install_mod does not forward force= to install_from_archive, this
    fails: the conflicting file blocks the install even with force=True."""
    archive = _package_archive(make_archive, "Solo Mod", "Solo Mod", "1.0.0")
    length = _mock_download(respx_router, "https://dl.example/solo-mod-1.0.0.zip", archive)
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=302,
        name="Solo Mod",
        version="1.0.0",
        version_id=3020,
        link="https://dl.example/solo-mod-1.0.0.zip",
        content_length=length,
    )
    _echo_dependencies(respx_router, [])
    install_commit(
        clean_repo,
        "Blocker",
        "1.0.0",
        mod_type="server",
        files={"SPT/user/mods/Solo Mod/package.json": "existing content"},
    )

    result = services.install_mod(clean_repo.path, "Solo Mod", "1.0.0", force=True, db_path=db_path, client=client)

    assert result.installed[0].mod.name == "Solo Mod"
    warning_kinds = {warning.kind for warning in result.installed[0].warnings}
    assert "tracked-file" in warning_kinds


def _partial_failure_scenario(
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    *,
    mod_id: int,
    dep_id: int,
) -> tuple[Any, str, str]:
    """Seed a target with one dependency, but declare a wrong content_length
    for the target so its download fails a size check after the dependency
    already installed successfully. Returns (client, dep_link, target_link).
    """
    target_archive = _package_archive(make_archive, "Target Mod", "Target Mod", "2.0.0")
    dep_archive = _package_archive(make_archive, "Dep Mod", "Dep Mod", "1.0.0")
    target_length = len(target_archive.read_bytes())
    dep_length = len(dep_archive.read_bytes())
    target_link = f"https://dl.example/target-mod-2.0.0-{mod_id}.zip"
    dep_link = f"https://dl.example/dep-mod-1.0.0-{dep_id}.zip"
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=mod_id,
        name="Target Mod",
        version="2.0.0",
        version_id=mod_id * 10,
        link=target_link,
        content_length=target_length + 1,  # deliberately wrong: forces ArchiveError
    )
    _echo_dependencies(
        respx_router,
        [
            _dep_node(
                mod_id=dep_id,
                name="Dep Mod",
                version="1.0.0",
                version_id=dep_id * 10,
                link=dep_link,
                content_length=dep_length,
            )
        ],
    )
    _mock_download(respx_router, dep_link, dep_archive)
    _mock_download(respx_router, target_link, target_archive)
    return client, dep_link, target_link


def test_install_mod_partial_failure_raises_partial_install_error_keeping_dep_commit(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """If install_mod's partial-failure path is unimplemented, this fails:
    either no PartialInstallError is raised, or it lacks .installed/.cause,
    or the dependency's commit is missing from the repo."""
    client, _dep_link, _target_link = _partial_failure_scenario(
        client_factory, respx_router, db_path, make_archive, mod_id=221, dep_id=222
    )
    commits_before = len(clean_repo.log())

    with pytest.raises(PartialInstallError) as excinfo:
        services.install_mod(clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client)

    error = excinfo.value
    assert [item.mod.name for item in error.installed] == ["Dep Mod"]
    assert isinstance(error.cause, ArchiveError)

    commits = clean_repo.log()
    assert len(commits) == commits_before + 1
    assert commits[0].subject == "mod: Dep Mod 1.0.0"


def test_cli_install_prints_mods_installed_before_a_partial_failure(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguishes a deliberate partial-install summary from ordinary
    progress echo: ``_print_install``'s ``(N files) as <hash>`` suffix is
    never produced by ``_report_progress``, so this fails until the CLI
    explicitly reports the mods installed before a PartialInstallError."""
    _partial_failure_scenario(
        client_factory,
        respx_router,
        isolated_default_db_path,
        make_archive,
        mod_id=231,
        dep_id=232,
    )
    monkeypatch.chdir(clean_repo.path)

    result = cli_runner.invoke(cli_app, ["install", "Target Mod", "--version", "2.0.0", "--yes"])

    assert result.exit_code != 0
    assert re.search(r"installed Dep Mod 1\.0\.0 \(\d+ files\) as \w+", result.stdout)


# --- install_from_archive: forge_match --------------------------------------


def _seed_simple_catalog(
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    mods: list[JsonDict],
) -> None:
    """Mock /mods endpoint and sync mods into the catalog as a single page.

    Mocks respx_router.get("/mods") to return mods in a single page envelope
    (current_page=1, last_page=1, total=len(mods)), then syncs via Catalog.sync(full=True).
    """
    page = page_envelope(mods, current_page=1, last_page=1, total=len(mods), next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)


def test_install_from_archive_forge_match_sets_ids_on_unambiguous_match(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """install_from_archive with forge_match=True sets forge IDs on unambiguous name+version.

    When CoolMod 1.2.3 matches exactly one cached mod (id=401, version_id=4011),
    the result has forge_id='401' and forge_version_id='4011'.
    """
    _seed_simple_catalog(
        client_factory,
        respx_router,
        db_path,
        [
            mod_json_full(
                id=401,
                name="CoolMod",
                slug="coolmod",
                owner=owner_json(id=4010, name="Alice"),
                versions=[
                    {
                        "id": 4011,
                        "version": "1.2.3",
                        "spt_version_constraint": SPT_CONSTRAINT,
                        "downloads": 1,
                        "published_at": "2025-01-02T00:00:00Z",
                    }
                ],
            )
        ],
    )
    archive = _package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

    result = services.install_from_archive(clean_repo.path, archive, forge_match=True, db_path=db_path)

    assert result.mod.forge_id == "401"
    assert result.mod.forge_version_id == "4011"


def test_install_from_archive_forge_match_leaves_ids_empty_when_the_version_is_unknown(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """One mod matches the name unambiguously, but the installed archive's
    version has no matching cached version at all: `_catalog_match`'s `if
    len(versions) != 1: return "", ""` guard, distinct from the len(mods)
    ambiguity above. If that guard were removed (IndexError on
    versions[0]), this fails."""
    _seed_simple_catalog(
        client_factory,
        respx_router,
        db_path,
        [
            mod_json_full(
                id=403,
                name="CoolMod",
                slug="coolmod",
                owner=owner_json(id=4030, name="Alice"),
                versions=[
                    {
                        "id": 4031,
                        "version": "1.2.3",
                        "spt_version_constraint": SPT_CONSTRAINT,
                        "downloads": 1,
                        "published_at": "2025-01-02T00:00:00Z",
                    }
                ],
            )
        ],
    )
    archive = _package_archive(make_archive, "CoolMod", "CoolMod", "9.9.9")

    result = services.install_from_archive(clean_repo.path, archive, forge_match=True, db_path=db_path)

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


def test_install_from_archive_forge_match_leaves_ids_empty_on_ambiguous_match(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """install_from_archive with forge_match=True leaves forge IDs empty on ambiguous match.

    When CoolMod 1.2.3 matches multiple cached mods (ids 501 and 502, both named CoolMod),
    the result has forge_id='' and forge_version_id=''.
    """
    _seed_simple_catalog(
        client_factory,
        respx_router,
        db_path,
        [
            mod_json_full(
                id=501,
                name="CoolMod",
                slug="coolmod-a",
                owner=owner_json(id=5010, name="Alice"),
                versions=[
                    {
                        "id": 5011,
                        "version": "1.2.3",
                        "spt_version_constraint": SPT_CONSTRAINT,
                        "downloads": 1,
                        "published_at": "2025-01-02T00:00:00Z",
                    }
                ],
            ),
            mod_json_full(
                id=502,
                name="CoolMod",
                slug="coolmod-b",
                owner=owner_json(id=5020, name="Bob"),
                versions=[
                    {
                        "id": 5021,
                        "version": "1.2.3",
                        "spt_version_constraint": SPT_CONSTRAINT,
                        "downloads": 1,
                        "published_at": "2025-01-02T00:00:00Z",
                    }
                ],
            ),
        ],
    )
    archive = _package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

    result = services.install_from_archive(clean_repo.path, archive, forge_match=True, db_path=db_path)

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


def test_install_from_archive_forge_match_leaves_ids_empty_when_no_match(
    clean_repo: Repo, db_path: Path, make_archive: MakeArchive
) -> None:
    """When db_path points to a non-existent catalog file, forge_match still
    returns empty Forge IDs without raising an exception (no-op lookup).
    """
    # db_path points at a file that has never been created: the catalog cache
    # does not exist yet, which install_from_archive's docstring says makes
    # the lookup a no-op.
    archive = _package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

    result = services.install_from_archive(clean_repo.path, archive, forge_match=True, db_path=db_path)

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


# --- import_list: apply and content ------------------------------------


def test_import_list_apply_installs_exactly_the_missing_mods(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    tmp_path: Path,
) -> None:
    """If import_list's apply path is `pass`/unimplemented, this fails: the
    missing mod is never installed and diff.installed stays empty."""
    archive = _package_archive(make_archive, "Import Target", "Import Target", "3.0.0")
    length = _mock_download(respx_router, "https://dl.example/import-target-3.0.0.zip", archive)
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=601,
        name="Import Target",
        version="3.0.0",
        version_id=6010,
        link="https://dl.example/import-target-3.0.0.zip",
        content_length=length,
    )
    _echo_dependencies(respx_router, [])
    list_file = tmp_path / "mods.txt"
    list_file.write_text("Import Target 3.0.0\n", encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file=list_file, apply=True, db_path=db_path, client=client)

    assert diff.missing == [services.ImportEntry(name="Import Target", wanted_version="3.0.0")]
    assert not diff.different
    assert not diff.extra
    assert len(diff.installed) == 1
    assert [entry.name for entry in diff.installed[0].plan.entries] == ["Import Target"]
    assert diff.installed[0].installed[0].mod.name == "Import Target"
    assert diff.installed[0].installed[0].mod.version == "3.0.0"

    installed_names = [mod.name for mod in services.list_mods(clean_repo.path)]
    assert installed_names == ["Import Target"]


def test_import_list_content_parameter_yields_the_same_diff_as_a_file(clean_repo: Repo, tmp_path: Path) -> None:
    """import_list with content= and list_file= parameters produce identical results.

    Parsing "SomeMod 1.0.0\\n" via content= produces the same ImportDiff as parsing
    from a file; both report missing=[ImportEntry(name='SomeMod', wanted_version='1.0.0')].
    """
    text = "SomeMod 1.0.0\n"
    list_file = tmp_path / "mods.txt"
    list_file.write_text(text, encoding="utf-8")

    from_file = services.import_list(clean_repo.path, list_file=list_file)
    from_content = services.import_list(clean_repo.path, content=text)

    assert from_content == from_file
    assert from_content.missing == [services.ImportEntry(name="SomeMod", wanted_version="1.0.0")]


# --- clear_catalog_cache --------------------------------------------------


def test_clear_catalog_cache_removes_the_db_file_and_reports_removed(
    tmp_path: Path,
) -> None:
    """If clear_catalog_cache's body is `pass`, this fails: the file survives
    and the return value is None instead of ClearCacheResult(removed=True)."""
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    assert db.exists()

    result = services.clear_catalog_cache(db_path=db)

    assert result == services.ClearCacheResult(db_path=db, removed=True)
    assert not db.exists()


def test_clear_catalog_cache_second_call_reports_not_removed(tmp_path: Path) -> None:
    """clear_catalog_cache on already-deleted cache returns ClearCacheResult(removed=False).

    After the first clear_catalog_cache call deletes db_path, calling it again
    returns removed=False (no file to remove).
    """
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    services.clear_catalog_cache(db_path=db)

    result = services.clear_catalog_cache(db_path=db)

    assert result == services.ClearCacheResult(db_path=db, removed=False)


def test_clear_catalog_cache_with_downloads_clears_cached_archives_and_reports_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``downloads=True`` still behaved like the default, this fails: the
    cached archive would survive and ``downloads_freed_bytes`` would stay 0.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    cache_dir = download_cache_dir()
    payload = b"cached archive bytes"
    entry = _seed_download_cache_entry(cache_dir, 555, "mod.zip", payload)
    meta_size = (entry / "meta.json").stat().st_size
    expected_freed = len(payload) + meta_size

    result = services.clear_catalog_cache(db_path=db, downloads=True)

    assert result.db_path == db
    assert result.removed is True
    assert result.downloads_path == cache_dir
    assert result.downloads_freed_bytes == expected_freed
    assert not entry.exists()


def test_clear_catalog_cache_without_downloads_leaves_cached_archives_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``downloads=False`` default must not touch the archive cache at
    all -- if it did, the seeded archive below would disappear and
    ``downloads_freed_bytes`` would report the freed bytes instead of 0.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    cache_dir = download_cache_dir()
    entry = _seed_download_cache_entry(cache_dir, 556, "mod.zip", b"cached bytes")

    result = services.clear_catalog_cache(db_path=db)

    assert result.downloads_path is None
    assert result.downloads_freed_bytes == 0
    assert (entry / "mod.zip").is_file()


# --- download cache: install_mod / update_mod reuse and staging cleanup ----


def test_reinstalling_a_removed_mod_reuses_the_cached_archive_and_skips_the_download(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If ``_download_entry`` re-downloaded even when a matching archive is
    already cached, this fails: the download route would be hit twice
    instead of once, and no progress message would report the cache hit.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = _package_archive(make_archive, "CacheMod", "Cache Mod", "1.0.0")
    link = "https://dl.example/cache-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=701,
        name="Cache Mod",
        version="1.0.0",
        version_id=7010,
        link=link,
        content_length=content_length,
    )
    _echo_dependencies(respx_router, [])
    route = _mock_download_route(respx_router, link, archive)

    first = services.install_mod(clean_repo.path, "Cache Mod", "1.0.0", db_path=db_path, client=client)
    assert route.call_count == 1
    services.remove_mod(clean_repo.path, "Cache Mod")

    messages: list[str] = []
    second = services.install_mod(
        clean_repo.path,
        "Cache Mod",
        "1.0.0",
        db_path=db_path,
        client=client,
        progress=lambda step, percent, message: messages.append(message),
    )

    assert route.call_count == 1
    assert any(message.endswith(CACHE_HIT_MESSAGE) for message in messages)
    assert [entry.mod.name for entry in second.installed] == ["Cache Mod"]
    assert second.installed[0].mod.version == first.installed[0].mod.version
    assert (clean_repo.path / "SPT/user/mods/CacheMod/package.json").is_file()


def test_install_mod_redownloads_when_the_cached_archive_size_does_not_match(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cache entry whose stored size differs from the plan's
    ``content_length`` must not be trusted: install_mod must still fetch a
    fresh copy over HTTP -- exactly once -- rather than installing the stale
    bytes, and the install must still succeed.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = _package_archive(make_archive, "StaleCacheMod", "Stale Cache Mod", "1.0.0")
    link = "https://dl.example/stale-cache-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=702,
        name="Stale Cache Mod",
        version="1.0.0",
        version_id=7020,
        link=link,
        content_length=content_length,
    )
    _echo_dependencies(respx_router, [])
    route = _mock_download_route(respx_router, link, archive)
    stale_payload = b"not the real archive; deliberately the wrong size"
    assert len(stale_payload) != content_length
    _seed_download_cache_entry(download_cache_dir(), 7020, "stale.zip", stale_payload)

    result = services.install_mod(clean_repo.path, "Stale Cache Mod", "1.0.0", db_path=db_path, client=client)

    assert route.call_count == 1
    assert result.installed[0].mod.name == "Stale Cache Mod"
    assert (clean_repo.path / "SPT/user/mods/StaleCacheMod/package.json").is_file()


def _mock_updates_single(
    respx_router: respx.MockRouter,
    *,
    current_id: int,
    mod_id: int,
    name: str,
    slug: str,
    current_version: str,
    new_version_id: int,
    new_version: str,
    link: str,
    content_length: int,
) -> None:
    """Mock a Forge update check response with a single update available."""
    payload = updates_payload(
        SPT_VERSION,
        updates=[
            {
                "current_version": current_version_json(
                    version_id=current_id,
                    mod_id=mod_id,
                    name=name,
                    version=current_version,
                    slug=slug,
                ),
                "recommended_version": recommended_version_json(
                    version_id=new_version_id,
                    version=new_version,
                    link=link,
                    content_length=content_length,
                    spt_version=SPT_VERSION,
                ),
                "update_reason": "newer version available",
            }
        ],
    )
    respx_router.get("/mods/updates").mock(return_value=httpx.Response(200, json=success_envelope(payload)))


def test_update_mod_to_a_cached_version_skips_the_download(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If update_mod always downloaded the recommended version, this fails:
    the download route would be called even though a matching archive is
    already cached for the recommended version id.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    install_commit(
        clean_repo,
        "UpdateCacheMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/UpdateCacheMod/package.json": json.dumps(
                {"name": "UpdateCacheMod", "version": "1.0.0", "author": "Acme"}
            )
        },
        **forge_trailers("10", "100"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/UpdateCacheMod/package.json": json.dumps(
                {"name": "UpdateCacheMod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/update-cache-mod-2.0.0.zip"
    _mock_updates_single(
        respx_router,
        current_id=100,
        mod_id=10,
        name="UpdateCacheMod",
        slug="update-cache-mod",
        current_version="1.0.0",
        new_version_id=200,
        new_version="2.0.0",
        link=link,
        content_length=len(body),
    )
    route = _mock_download_route(respx_router, link, new_archive)
    _seed_download_cache_entry(download_cache_dir(), 200, "cached.zip", body)
    client = client_factory()

    result = services.update_mod(clean_repo.path, "UpdateCacheMod", db_path=db_path, client=client)

    assert route.call_count == 0
    assert result.version == "2.0.0"
    assert result.notice == ""


def test_update_mod_to_an_uncached_version_downloads_once_and_caches_it(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If update_mod's download were not cached afterwards, this fails: no
    archive would be left under the recommended version id in the download
    cache directory.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    install_commit(
        clean_repo,
        "FreshCacheMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/FreshCacheMod/package.json": json.dumps(
                {"name": "FreshCacheMod", "version": "1.0.0", "author": "Acme"}
            )
        },
        **forge_trailers("11", "110"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/FreshCacheMod/package.json": json.dumps(
                {"name": "FreshCacheMod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/fresh-cache-mod-2.0.0.zip"
    _mock_updates_single(
        respx_router,
        current_id=110,
        mod_id=11,
        name="FreshCacheMod",
        slug="fresh-cache-mod",
        current_version="1.0.0",
        new_version_id=210,
        new_version="2.0.0",
        link=link,
        content_length=len(body),
    )
    route = _mock_download_route(respx_router, link, new_archive)
    client = client_factory()

    result = services.update_mod(clean_repo.path, "FreshCacheMod", db_path=db_path, client=client)

    assert route.call_count == 1
    assert result.version == "2.0.0"
    cached_entry = download_cache_dir() / "210"
    cached_files = [path for path in cached_entry.iterdir() if path.name != "meta.json"]
    assert len(cached_files) == 1
    assert cached_files[0].read_bytes() == body


def test_install_mod_leaves_no_leftover_staging_directories(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If ``install_mod`` no longer cleaned up its per-entry temp directory
    (dropped when the plan-level wrapper was replaced with a per-entry one),
    this fails: a ``sptmod-*`` directory would be left behind in the shared
    staging area.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = _package_archive(make_archive, "StagingMod", "Staging Mod", "1.0.0")
    link = "https://dl.example/staging-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=703,
        name="Staging Mod",
        version="1.0.0",
        version_id=7030,
        link=link,
        content_length=content_length,
    )
    _echo_dependencies(respx_router, [])
    _mock_download(respx_router, link, archive)

    services.install_mod(clean_repo.path, "Staging Mod", "1.0.0", db_path=db_path, client=client)

    assert not list(staging_dir().glob("sptmod-*"))


def test_no_download_progress_message_during_a_cache_hit_mentions_git_vocabulary(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The download-cache path must speak about files, not about the git
    internals it happens to sit next to: a cache hit is not a "commit" and
    the cache is not a "repository". Scoped to the ``download`` step's own
    messages, which is what the caching feature actually touched -- the
    unrelated ``commit`` install step ("committing X Y") is untouched by
    this feature and out of scope here.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = _package_archive(make_archive, "PlainWordsMod", "Plain Words Mod", "1.0.0")
    link = "https://dl.example/plain-words-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = _seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=704,
        name="Plain Words Mod",
        version="1.0.0",
        version_id=7040,
        link=link,
        content_length=content_length,
    )
    _echo_dependencies(respx_router, [])
    _mock_download(respx_router, link, archive)
    services.install_mod(clean_repo.path, "Plain Words Mod", "1.0.0", db_path=db_path, client=client)
    services.remove_mod(clean_repo.path, "Plain Words Mod")

    download_messages: list[str] = []

    def _progress(step: str, percent: int, message: str) -> None:
        # The parameter names are part of the Progress protocol.
        del percent
        if step == "download":
            download_messages.append(message)

    services.install_mod(
        clean_repo.path,
        "Plain Words Mod",
        "1.0.0",
        db_path=db_path,
        client=client,
        progress=_progress,
    )

    assert download_messages  # sanity: the cache-hit path did report progress
    forbidden = ("git", "commit", "repository", "revert", "cherry")
    for message in download_messages:
        lowered = message.lower()
        for word in forbidden:
            assert word not in lowered, f"{word!r} leaked into {message!r}"
