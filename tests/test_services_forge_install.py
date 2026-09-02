"""Tests for the Forge install path of ``sptmod.core.services``.

Covers ``plan_mod_install``, ``install_mod`` (end-to-end with a mocked Forge
client and fake archives served through the download mock route),
``install_from_archive``'s ``forge_match`` catalog lookup, ``import_list``
with ``apply=True`` and the ``content`` parameter, and
``clear_catalog_cache``.
"""

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from sptmod.cli import app as cli_app
from sptmod.core import services
from sptmod.core.errors import ArchiveError, ConflictError, NotAGameDirError
from sptmod.core.forge.catalog import DB_FILE_NAME, Catalog, NoCompatibleVersionError
from sptmod.core.forge.client import DEFAULT_BASE_URL
from sptmod.core.models import PlanEntry
from sptmod.core.repo import Repo
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
    success_envelope,
    version_json,
)

__all__ = [
    "client_factory",
    "db_path",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]

SPT_VERSION = "4.1.0"
SPT_CONSTRAINT = "^4.0.0"

cli_runner = CliRunner()


@pytest.fixture
def isolated_default_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``default_db_path()`` at an isolated location for CLI tests.

    The CLI never passes ``db_path`` explicitly, so it resolves through
    ``forge.catalog.default_db_path()``, which must not touch the developer's
    real config directory.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        "sptmod.core.forge.catalog.platformdirs.user_config_path",
        lambda *args, **kwargs: config_dir,
    )
    return config_dir / DB_FILE_NAME


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
):
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
        respx_router.get(f"/mod/{mod_id}/versions").mock(
            return_value=httpx.Response(200, json=full_versions_page)
        )
        cat.fetch_versions(client, mod_id)
    return client


def _seed_target_without_fetch_versions(
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
):
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
    respx_router.get(f"/mod/{mod_id}/versions").mock(
        return_value=httpx.Response(200, json=full_versions_page)
    )
    return client


def _echo_dependencies(
    respx_router: respx.MockRouter, nodes: list[dict[str, Any]]
) -> None:
    """Mock ``/mods/dependencies`` echoing the queried pair as the response key."""

    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: nodes}})

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def _dep_node(
    *,
    id: int,
    name: str,
    version: str,
    version_id: int,
    link: str,
    content_length: int,
) -> dict[str, Any]:
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


def _package_archive(make_archive, folder: str, name: str, version: str) -> Path:
    return make_archive(
        {
            f"SPT/user/mods/{folder}/package.json": json.dumps(
                {"name": name, "version": version, "author": "Acme"}
            )
        },
        "zip",
    )


def _mock_download(respx_router: respx.MockRouter, link: str, archive: Path) -> int:
    body = archive.read_bytes()
    respx_router.get(link).mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Length": str(len(body))}
        )
    )
    return len(body)


# --- plan_mod_install -------------------------------------------------------


def test_plan_mod_install_orders_dependency_before_the_requested_mod(
    clean_repo: Repo, client_factory, respx_router: respx.MockRouter, db_path: Path
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
                id=102,
                name="Dep Mod",
                version="1.0.0",
                version_id=1020,
                link="https://dl.example/dep-1.0.0.zip",
                content_length=500,
            )
        ],
    )

    plan = services.plan_mod_install(
        clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client
    )

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
    clean_repo: Repo, client_factory, respx_router: respx.MockRouter, db_path: Path
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

    plan = services.plan_mod_install(
        clean_repo.path, "Link Mod", "1.0.0", db_path=db_path, client=client
    )

    assert plan.entries[0].link == "https://dl.example/link-mod-1.0.0.zip"
    assert plan.entries[0].content_length == 800


def test_install_mod_succeeds_when_the_catalog_was_only_synced(
    clean_repo: Repo,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
) -> None:
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

    result = services.install_mod(
        clean_repo.path, "Link Mod", "1.0.0", db_path=db_path, client=client
    )

    assert [entry.mod.name for entry in result.installed] == ["Link Mod"]
    assert (clean_repo.path / "SPT/user/mods/LinkMod/package.json").is_file()


def test_plan_mod_install_unknown_mod_raises_no_compatible_version_error(
    clean_repo: Repo, client_factory, db_path: Path
) -> None:
    """If plan_mod_install's body is `pass`, this fails: no exception raised
    for a mod absent from the (empty) catalog."""
    client = client_factory()

    with pytest.raises(NoCompatibleVersionError):
        services.plan_mod_install(
            clean_repo.path, "Nonexistent Mod", db_path=db_path, client=client
        )


def test_plan_mod_install_raises_not_a_game_dir_when_spt_version_unknown(
    clean_repo: Repo, client_factory, db_path: Path
) -> None:
    """If plan_mod_install's body is `pass`, this fails: no exception raised
    when the game dir's core.json carries no readable SPT version."""
    (clean_repo.path / "SPT/SPT_Data/configs/core.json").write_text(
        "not json", encoding="utf-8"
    )
    client = client_factory()

    with pytest.raises(NotAGameDirError):
        services.plan_mod_install(
            clean_repo.path, "Target Mod", db_path=db_path, client=client
        )


# --- install_mod --------------------------------------------------------


def test_install_mod_installs_missing_dependency_as_its_own_commit_before_the_target(
    clean_repo: Repo,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
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
                id=202,
                name="Dep Mod",
                version="1.0.0",
                version_id=2020,
                link="https://dl.example/dep-mod-1.0.0.zip",
                content_length=dep_length,
            )
        ],
    )
    _mock_download(
        respx_router, "https://dl.example/target-mod-2.0.0.zip", target_archive
    )
    _mock_download(respx_router, "https://dl.example/dep-mod-1.0.0.zip", dep_archive)

    result = services.install_mod(
        clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client
    )

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
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
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

    result = services.install_mod(
        clean_repo.path, "Versionless Mod", "3.2.1", db_path=db_path, client=client
    )

    assert result.installed[0].mod.version == "3.2.1"
    commit = Repo(clean_repo.path).log()[0]
    assert commit.trailers["Mod-Version"] == "3.2.1"


def test_check_updates_after_a_metadata_less_install_queries_the_plan_version(
    clean_repo: Repo,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
) -> None:
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
    services.install_mod(
        clean_repo.path, "Versionless Mod", "3.2.1", db_path=db_path, client=client
    )

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
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
    install_commit,
) -> None:
    """If install_mod's body is `pass`, this fails: no ConflictError is raised
    and the file-conflict check is never reached."""
    archive = _package_archive(make_archive, "Solo Mod", "Solo Mod", "1.0.0")
    length = _mock_download(
        respx_router, "https://dl.example/solo-mod-1.0.0.zip", archive
    )
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
        services.install_mod(
            clean_repo.path, "Solo Mod", "1.0.0", db_path=db_path, client=client
        )

    assert len(clean_repo.log()) == commits_before


def test_install_mod_force_downgrades_the_conflict_and_installs(
    clean_repo: Repo,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
    install_commit,
) -> None:
    """If install_mod does not forward force= to install_from_archive, this
    fails: the conflicting file blocks the install even with force=True."""
    archive = _package_archive(make_archive, "Solo Mod", "Solo Mod", "1.0.0")
    length = _mock_download(
        respx_router, "https://dl.example/solo-mod-1.0.0.zip", archive
    )
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

    result = services.install_mod(
        clean_repo.path, "Solo Mod", "1.0.0", force=True, db_path=db_path, client=client
    )

    assert result.installed[0].mod.name == "Solo Mod"
    warning_kinds = {warning.kind for warning in result.installed[0].warnings}
    assert "tracked-file" in warning_kinds


def _partial_failure_scenario(
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
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
                id=dep_id,
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
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
) -> None:
    """If install_mod's partial-failure path is unimplemented, this fails:
    either no PartialInstallError is raised, or it lacks .installed/.cause,
    or the dependency's commit is missing from the repo."""
    from sptmod.core.errors import PartialInstallError

    client, _dep_link, _target_link = _partial_failure_scenario(
        client_factory, respx_router, db_path, make_archive, mod_id=221, dep_id=222
    )
    commits_before = len(clean_repo.log())

    with pytest.raises(PartialInstallError) as excinfo:
        services.install_mod(
            clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client
        )

    error = excinfo.value
    assert [item.mod.name for item in error.installed] == ["Dep Mod"]
    assert isinstance(error.cause, ArchiveError)

    commits = clean_repo.log()
    assert len(commits) == commits_before + 1
    assert commits[0].subject == "mod: Dep Mod 1.0.0"


def test_cli_install_prints_mods_installed_before_a_partial_failure(
    clean_repo: Repo,
    client_factory,
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
    make_archive,
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

    result = cli_runner.invoke(
        cli_app, ["install", "Target Mod", "--version", "2.0.0", "--yes"]
    )

    assert result.exit_code != 0
    assert re.search(r"installed Dep Mod 1\.0\.0 \(\d+ files\) as \w+", result.stdout)


# --- install_from_archive: forge_match --------------------------------------


def _seed_simple_catalog(
    client_factory, respx_router: respx.MockRouter, db_path: Path, mods: list[dict]
) -> None:
    page = page_envelope(
        mods, current_page=1, last_page=1, total=len(mods), next_url=None
    )
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)


def test_install_from_archive_forge_match_sets_ids_on_unambiguous_match(
    clean_repo: Repo, client_factory, respx_router: respx.MockRouter, db_path: Path,
    make_archive,
) -> None:
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

    result = services.install_from_archive(
        clean_repo.path, archive, forge_match=True, db_path=db_path
    )

    assert result.mod.forge_id == "401"
    assert result.mod.forge_version_id == "4011"


def test_install_from_archive_forge_match_leaves_ids_empty_on_ambiguous_match(
    clean_repo: Repo, client_factory, respx_router: respx.MockRouter, db_path: Path,
    make_archive,
) -> None:
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

    result = services.install_from_archive(
        clean_repo.path, archive, forge_match=True, db_path=db_path
    )

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


def test_install_from_archive_forge_match_leaves_ids_empty_when_no_match(
    clean_repo: Repo, db_path: Path, make_archive
) -> None:
    # db_path points at a file that has never been created: the catalog cache
    # does not exist yet, which install_from_archive's docstring says makes
    # the lookup a no-op.
    archive = _package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

    result = services.install_from_archive(
        clean_repo.path, archive, forge_match=True, db_path=db_path
    )

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


# --- import_list: apply and content ------------------------------------


def test_import_list_apply_installs_exactly_the_missing_mods(
    clean_repo: Repo,
    client_factory,
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive,
    tmp_path: Path,
) -> None:
    """If import_list's apply path is `pass`/unimplemented, this fails: the
    missing mod is never installed and diff.installed stays empty."""
    archive = _package_archive(make_archive, "Import Target", "Import Target", "3.0.0")
    length = _mock_download(
        respx_router, "https://dl.example/import-target-3.0.0.zip", archive
    )
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

    diff = services.import_list(
        clean_repo.path, list_file=list_file, apply=True, db_path=db_path, client=client
    )

    assert diff.missing == [
        services.ImportEntry(name="Import Target", wanted_version="3.0.0")
    ]
    assert diff.different == []
    assert diff.extra == []
    assert len(diff.installed) == 1
    assert [entry.name for entry in diff.installed[0].plan.entries] == [
        "Import Target"
    ]
    assert diff.installed[0].installed[0].mod.name == "Import Target"
    assert diff.installed[0].installed[0].mod.version == "3.0.0"

    installed_names = [mod.name for mod in services.list_mods(clean_repo.path)]
    assert installed_names == ["Import Target"]


def test_import_list_content_parameter_yields_the_same_diff_as_a_file(
    clean_repo: Repo, tmp_path: Path
) -> None:
    text = "SomeMod 1.0.0\n"
    list_file = tmp_path / "mods.txt"
    list_file.write_text(text, encoding="utf-8")

    from_file = services.import_list(clean_repo.path, list_file=list_file)
    from_content = services.import_list(clean_repo.path, content=text)

    assert from_content == from_file
    assert from_content.missing == [
        services.ImportEntry(name="SomeMod", wanted_version="1.0.0")
    ]


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
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    services.clear_catalog_cache(db_path=db)

    result = services.clear_catalog_cache(db_path=db)

    assert result == services.ClearCacheResult(db_path=db, removed=False)
