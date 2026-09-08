"""Tests for ``install_mod`` and for ``install_from_archive``'s ``forge_match`` catalog lookup.

Covers the end-to-end install against a mocked Forge client and fake archives
served through the download mock route: a missing dependency as its own
commit, the plan version written when the archive carries none, the update
check afterwards, a file conflict with and without ``force``, a partial
failure and what the CLI prints for it, and the Forge ids
``install_from_archive`` sets on an unambiguous match and leaves empty
otherwise.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from fixture_types import InstallCommit, JsonDict, MakeArchive
from forge_install_env import (
    SPT_CONSTRAINT,
    dep_node,
    echo_dependencies,
    mock_download,
    package_archive,
    seed_target,
)
from forge_json import mod_json_full, owner_json, page_envelope, success_envelope
from sptmod.cli import app as cli_app
from sptmod.core import services
from sptmod.core.errors import ArchiveError, ConflictError, PartialInstallError
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient
from sptmod.core.repo import Repo

cli_runner = CliRunner()


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
    target_archive = package_archive(make_archive, "Target Mod", "Target Mod", "2.0.0")
    dep_archive = package_archive(make_archive, "Dep Mod", "Dep Mod", "1.0.0")
    target_length = len(target_archive.read_bytes())
    dep_length = len(dep_archive.read_bytes())
    client = seed_target(
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
    echo_dependencies(
        respx_router,
        [
            dep_node(
                mod_id=202,
                name="Dep Mod",
                version="1.0.0",
                version_id=2020,
                link="https://dl.example/dep-mod-1.0.0.zip",
                content_length=dep_length,
            )
        ],
    )
    mock_download(respx_router, "https://dl.example/target-mod-2.0.0.zip", target_archive)
    mock_download(respx_router, "https://dl.example/dep-mod-1.0.0.zip", dep_archive)

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
    client = seed_target(
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
    echo_dependencies(respx_router, [])
    mock_download(respx_router, link, archive)

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
    client = seed_target(
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
    echo_dependencies(respx_router, [])
    mock_download(respx_router, link, archive)
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
    archive = package_archive(make_archive, "Solo Mod", "Solo Mod", "1.0.0")
    length = mock_download(respx_router, "https://dl.example/solo-mod-1.0.0.zip", archive)
    client = seed_target(
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
    echo_dependencies(respx_router, [])
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
    archive = package_archive(make_archive, "Solo Mod", "Solo Mod", "1.0.0")
    length = mock_download(respx_router, "https://dl.example/solo-mod-1.0.0.zip", archive)
    client = seed_target(
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
    echo_dependencies(respx_router, [])
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
    target_archive = package_archive(make_archive, "Target Mod", "Target Mod", "2.0.0")
    dep_archive = package_archive(make_archive, "Dep Mod", "Dep Mod", "1.0.0")
    target_length = len(target_archive.read_bytes())
    dep_length = len(dep_archive.read_bytes())
    target_link = f"https://dl.example/target-mod-2.0.0-{mod_id}.zip"
    dep_link = f"https://dl.example/dep-mod-1.0.0-{dep_id}.zip"
    client = seed_target(
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
    echo_dependencies(
        respx_router,
        [
            dep_node(
                mod_id=dep_id,
                name="Dep Mod",
                version="1.0.0",
                version_id=dep_id * 10,
                link=dep_link,
                content_length=dep_length,
            )
        ],
    )
    mock_download(respx_router, dep_link, dep_archive)
    mock_download(respx_router, target_link, target_archive)
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
    archive = package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

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
    archive = package_archive(make_archive, "CoolMod", "CoolMod", "9.9.9")

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
    archive = package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

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
    archive = package_archive(make_archive, "CoolMod", "CoolMod", "1.2.3")

    result = services.install_from_archive(clean_repo.path, archive, forge_match=True, db_path=db_path)

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""
