"""Tests for ``check_updates`` and ``update_mod`` of ``sptmod.core.services``.

Covers the mapping of the four ``/mods/updates`` lists into ``UpdatesReport``,
skipping active mods neither a ``Mod-Forge-Id`` nor the catalog identifies,
the single-commit file replacement of ``update_mod`` (old files removed, new
files added, ``BepInEx/config/`` untouched, ``Mod-Previous-Version`` trailer),
and its no-op behaviour for blocked, incompatible or absent updates.
"""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from conftest import forge_trailers
from fixture_types import InstallCommit, JsonDict, MakeArchive
from forge_json import current_version_json, success_envelope
from sptmod.cli import app as cli_app
from sptmod.core import services
from sptmod.core.errors import (
    AmbiguousModError,
    ConflictError,
    DependencyError,
    DirtyTreeError,
    ModNotFoundError,
)
from sptmod.core.forge.client import ForgeClient
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")

SPT_VERSION = "4.1.0"

cli_runner = CliRunner()


def _mock_updates(respx_router: respx.MockRouter, data: JsonDict) -> None:
    """Mock the Forge /mods/updates endpoint to return a given payload."""
    respx_router.get("/mods/updates").mock(return_value=httpx.Response(200, json=success_envelope(data)))


# --- check_updates -----------------------------------------------------


def test_check_updates_reports_all_four_lists_and_skips_unmatched_active_mods(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
) -> None:
    """If check_updates's body is `pass`, this fails: returns None instead of
    an UpdatesReport with the mods sorted into their four buckets."""
    install_commit(
        clean_repo,
        "Available Mod",
        "1.0.0",
        mod_type="server",
        **forge_trailers("10"),
    )
    install_commit(
        clean_repo,
        "Blocked Mod",
        "1.5.0",
        mod_type="server",
        minute=2,
        **forge_trailers("20"),
    )
    install_commit(
        clean_repo,
        "Uptodate Mod",
        "2.0.0",
        mod_type="server",
        minute=3,
        **forge_trailers("40"),
    )
    install_commit(
        clean_repo,
        "Incompat Mod",
        "0.9.0",
        mod_type="server",
        minute=4,
        **forge_trailers("30"),
    )
    install_commit(
        clean_repo,
        "Ghost Mod",
        "1.0.0",
        mod_type="server",
        minute=5,
        **forge_trailers(""),
    )
    _mock_updates(
        respx_router,
        {
            "spt_version": SPT_VERSION,
            "updates": [
                {
                    "current_version": current_version_json(
                        version_id=100,
                        mod_id=10,
                        name="Available Mod",
                        version="1.0.0",
                        slug="available-mod",
                    ),
                    "recommended_version": {
                        "id": 200,
                        "version": "2.0.0",
                        "link": "https://dl.example/available-2.0.0.zip",
                        "content_length": 12345,
                        "fika_compatibility": "unknown",
                        "spt_versions": [SPT_VERSION],
                    },
                    "update_reason": "newer version available",
                }
            ],
            "blocked_updates": [
                {
                    "current_version": current_version_json(
                        version_id=101,
                        mod_id=20,
                        name="Blocked Mod",
                        version="1.5.0",
                    ),
                    "latest_version": {
                        "id": 201,
                        "version": "2.0.0",
                        "spt_versions": [SPT_VERSION],
                    },
                    "block_reason": "Conflicting mod dependency.",
                    "blocking_mods": [
                        {
                            "mod_id": 99,
                            "mod_guid": None,
                            "mod_name": "Other Mod",
                            "current_version": "1.0.0",
                            "constraint": "<2.0.0",
                            "incompatible_with": "2.0.0",
                        }
                    ],
                }
            ],
            "up_to_date": [
                {
                    "id": 102,
                    "mod_id": 40,
                    "guid": None,
                    "name": "Uptodate Mod",
                    "version": "2.0.0",
                    "spt_versions": [SPT_VERSION],
                }
            ],
            "incompatible_with_spt": [
                {
                    "id": 103,
                    "mod_id": 30,
                    "guid": None,
                    "name": "Incompat Mod",
                    "version": "0.9.0",
                    "reason": "No version for this SPT release.",
                    "latest_compatible_version": "1.0.0",
                }
            ],
        },
    )
    client = client_factory()
    commits_before = len(clean_repo.log())

    report = services.check_updates(clean_repo.path, db_path=db_path, client=client)

    assert report.spt_version == SPT_VERSION
    assert report.available == [
        services.UpdateCandidate(
            name="Available Mod",
            installed_version="1.0.0",
            new_version="2.0.0",
            version_id=200,
            link="https://dl.example/available-2.0.0.zip",
            content_length=12345,
            reason="newer version available",
        )
    ]
    assert report.blocked == [
        services.BlockedUpdate(
            name="Blocked Mod",
            installed_version="1.5.0",
            new_version="2.0.0",
            reason="Conflicting mod dependency.",
            blocking_mods=["Other Mod"],
        )
    ]
    assert report.up_to_date == ["Uptodate Mod"]
    assert report.incompatible == [
        services.IncompatibleMod(
            name="Incompat Mod",
            installed_version="0.9.0",
            reason="No version for this SPT release.",
            latest_compatible_version="1.0.0",
        )
    ]
    assert report.skipped == ["Ghost Mod"]
    # Read-only: nothing is downloaded or written.
    assert len(clean_repo.log()) == commits_before


def test_check_updates_unknown_name_raises_mod_not_found(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    db_path: Path,
    install_commit: InstallCommit,
) -> None:
    """check_updates raises ModNotFoundError when queried by a name not installed.

    Calling check_updates with names=['Missing Mod'] (when only CoolMod is installed)
    raises ModNotFoundError instead of returning an empty report.
    """
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server")
    client = client_factory()

    with pytest.raises(ModNotFoundError):
        services.check_updates(clean_repo.path, names=["Missing Mod"], db_path=db_path, client=client)


def test_check_updates_ambiguous_name_raises_ambiguous_mod(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    db_path: Path,
    install_commit: InstallCommit,
) -> None:
    """check_updates raises AmbiguousModError for "coolmod" vs "CoolMod".

    When case-different variants of the same mod name are installed (e.g.,
    CoolMod and coolmod), a query by name must fail to disambiguate rather
    than silently picking one.
    """
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server", **forge_trailers("1"))
    install_commit(
        clean_repo,
        "coolmod",
        "2.0.0",
        mod_type="server",
        minute=2,
        **forge_trailers("2"),
    )
    client = client_factory()

    with pytest.raises(AmbiguousModError):
        services.check_updates(clean_repo.path, names=["coolmod"], db_path=db_path, client=client)


# --- update_mod ----------------------------------------------------------


def test_update_mod_replaces_files_in_one_commit_and_keeps_config_untouched(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
) -> None:
    """If update_mod's body is `pass`, this fails: no commit is created and
    the returned result is None instead of an UpdateResult."""
    install_commit(
        clean_repo,
        "CoolMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "Acme"}
            ),
            "SPT/user/mods/CoolMod/old_only.txt": "old text",
            "BepInEx/config/CoolMod.cfg": "user settings",
        },
        **forge_trailers("10", "100"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "2.0.0", "author": "Acme"}
            ),
            "SPT/user/mods/CoolMod/new_only.txt": "new text",
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/coolmod-2.0.0.zip"
    _mock_updates(
        respx_router,
        {
            "spt_version": SPT_VERSION,
            "updates": [
                {
                    "current_version": current_version_json(
                        version_id=100,
                        mod_id=10,
                        name="CoolMod",
                        version="1.0.0",
                        slug="coolmod",
                    ),
                    "recommended_version": {
                        "id": 200,
                        "version": "2.0.0",
                        "link": link,
                        "content_length": len(body),
                        "fika_compatibility": "unknown",
                        "spt_versions": [SPT_VERSION],
                    },
                    "update_reason": "newer version available",
                }
            ],
            "blocked_updates": [],
            "up_to_date": [],
            "incompatible_with_spt": [],
        },
    )
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    client = client_factory()
    commits_before = len(clean_repo.log())

    result = services.update_mod(clean_repo.path, "CoolMod", db_path=db_path, client=client)

    assert result.name == "CoolMod"
    assert result.previous_version == "1.0.0"
    assert result.version == "2.0.0"
    assert result.commit != ""
    assert result.notice == ""

    commits = clean_repo.log()
    assert len(commits) == commits_before + 1
    top = commits[0]
    assert top.hash == result.commit
    assert top.subject == "mod: CoolMod 1.0.0 → 2.0.0"
    assert top.trailers["Mod-Previous-Version"] == "1.0.0"
    assert top.trailers["Mod-State"] == "updated"
    assert top.trailers["Mod-Forge-Id"] == "10"
    assert top.trailers["Mod-Forge-Version-Id"] == "200"
    assert top.trailers["Mod-Source"] == link
    assert top.trailers["Mod-Version"] == "2.0.0"

    root = clean_repo.path
    assert not (root / "SPT/user/mods/CoolMod/old_only.txt").exists()
    assert (root / "SPT/user/mods/CoolMod/new_only.txt").read_text() == "new text"
    assert (root / "BepInEx/config/CoolMod.cfg").read_text() == "user settings"
    package = json.loads((root / "SPT/user/mods/CoolMod/package.json").read_text())
    assert package["version"] == "2.0.0"

    changes = {file.path.as_posix(): file.change for file in result.files}
    assert changes.get("SPT/user/mods/CoolMod/old_only.txt") == "deleted"
    assert changes.get("SPT/user/mods/CoolMod/new_only.txt") == "added"
    assert "BepInEx/config/CoolMod.cfg" not in changes


def test_update_mod_with_a_foreign_untracked_mod_folder_raises_dirty_tree_error(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    db_path: Path,
    install_commit: InstallCommit,
) -> None:
    """§4b requires a clean tree before update, same as install: a manually
    copied mod folder nothing manages must abort with DirtyTreeError."""
    install_commit(
        clean_repo,
        "CoolMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0", "author": "Acme"})
        },
        **forge_trailers("10", "100"),
    )
    foreign = clean_repo.path / "SPT/user/mods/Foreign/x.txt"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("manually placed", encoding="utf-8")
    client = client_factory()
    commits_before = len(clean_repo.log())

    with pytest.raises(DirtyTreeError):
        services.update_mod(clean_repo.path, "CoolMod", db_path=db_path, client=client)

    assert foreign.read_text() == "manually placed"
    assert len(clean_repo.log()) == commits_before


def test_update_mod_keeps_the_runtime_data_the_mod_wrote_into_its_own_folder(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
) -> None:
    """A mod writing its own settings file into its folder must not turn its
    update into a dirty-tree error, and the file must survive untouched."""
    install_commit(
        clean_repo,
        "CoolMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "Acme"}
            ),
            "SPT/user/mods/CoolMod/old_only.txt": "old text",
        },
        **forge_trailers("10", "100"),
    )
    runtime_file = clean_repo.path / "SPT/user/mods/CoolMod/new_file.txt"
    runtime_file.write_text("written while playing", encoding="utf-8")
    new_archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "2.0.0", "author": "Acme"}
            ),
            "SPT/user/mods/CoolMod/new_only.txt": "new text",
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/coolmod-2.0.0.zip"
    _mock_updates(
        respx_router,
        {
            "spt_version": SPT_VERSION,
            "updates": [
                {
                    "current_version": current_version_json(
                        version_id=100,
                        mod_id=10,
                        name="CoolMod",
                        version="1.0.0",
                        slug="coolmod",
                    ),
                    "recommended_version": {
                        "id": 200,
                        "version": "2.0.0",
                        "link": link,
                        "content_length": len(body),
                        "fika_compatibility": "unknown",
                        "spt_versions": [SPT_VERSION],
                    },
                    "update_reason": "newer version available",
                }
            ],
            "blocked_updates": [],
            "up_to_date": [],
            "incompatible_with_spt": [],
        },
    )
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    client = client_factory()

    result = services.update_mod(clean_repo.path, "CoolMod", db_path=db_path, client=client)

    assert result.version == "2.0.0"
    assert runtime_file.read_text() == "written while playing"
    assert (clean_repo.path / "SPT/user/mods/CoolMod/new_only.txt").exists()
    assert "SPT/user/mods/CoolMod/new_file.txt" not in {file.path.as_posix() for file in result.files}


@pytest.mark.parametrize(
    ("mod_version", "payload"),
    [
        pytest.param(
            "1.0.0",
            {
                "spt_version": SPT_VERSION,
                "updates": [],
                "blocked_updates": [],
                "up_to_date": [
                    {
                        "id": 100,
                        "mod_id": 10,
                        "guid": None,
                        "name": "CoolMod",
                        "version": "1.0.0",
                        "spt_versions": [SPT_VERSION],
                    }
                ],
                "incompatible_with_spt": [],
            },
            id="up_to_date",
        ),
        pytest.param(
            "1.0.0",
            {
                "spt_version": SPT_VERSION,
                "updates": [],
                "blocked_updates": [
                    {
                        "current_version": current_version_json(
                            version_id=100,
                            mod_id=10,
                            name="CoolMod",
                            version="1.0.0",
                        ),
                        "latest_version": {
                            "id": 200,
                            "version": "2.0.0",
                            "spt_versions": [SPT_VERSION],
                        },
                        "block_reason": "Conflicting mod dependency.",
                        "blocking_mods": [],
                    }
                ],
                "up_to_date": [],
                "incompatible_with_spt": [],
            },
            id="blocked",
        ),
        pytest.param(
            "0.9.0",
            {
                "spt_version": SPT_VERSION,
                "updates": [],
                "blocked_updates": [],
                "up_to_date": [],
                "incompatible_with_spt": [
                    {
                        "id": 100,
                        "mod_id": 10,
                        "guid": None,
                        "name": "CoolMod",
                        "version": "0.9.0",
                        "reason": "No version for this SPT release.",
                        "latest_compatible_version": "1.0.0",
                    }
                ],
            },
            id="incompatible",
        ),
    ],
)
def test_update_mod_without_an_applicable_update_is_a_noop_with_notice(
    mod_version: str,
    payload: dict[str, object],
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
) -> None:
    """update_mod returns version="" and commit="" with a notice, adding no commit.

    When Forge reports a mod as up-to-date, blocked by a dependency, or
    incompatible with the SPT version, update_mod must return an empty result
    (no commit, no version change) but provide a descriptive notice explaining
    why the update did not proceed.
    """
    install_commit(clean_repo, "CoolMod", mod_version, mod_type="server", **forge_trailers("10"))
    _mock_updates(respx_router, payload)
    client = client_factory()
    commits_before = len(clean_repo.log())

    result = services.update_mod(clean_repo.path, "CoolMod", db_path=db_path, client=client)

    assert result.version == ""
    assert result.commit == ""
    assert result.previous_version == mod_version
    assert result.notice != ""
    assert len(clean_repo.log()) == commits_before


def test_update_mod_unknown_name_raises_mod_not_found(
    clean_repo: Repo, client_factory: Callable[..., ForgeClient], db_path: Path
) -> None:
    """update_mod on a name not installed raises ModNotFoundError before any changes.

    Calling update_mod(path, 'Nonexistent', ...) when the mod was never installed
    raises ModNotFoundError without modifying the repository.
    """
    client = client_factory()

    with pytest.raises(ModNotFoundError):
        services.update_mod(clean_repo.path, "Nonexistent", db_path=db_path, client=client)


def test_update_mod_absent_from_every_report_list_is_a_noop_with_generic_notice(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
) -> None:
    """Forge's four update-report lists can all omit a queried mod (an odd
    but possible server response); update_mod must still no-op instead of
    crashing, with the generic fallback notice. If _no_update_notice's final
    `return` were changed to return "", this fails."""
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server", **forge_trailers("10"))
    _mock_updates(
        respx_router,
        {
            "spt_version": SPT_VERSION,
            "updates": [],
            "blocked_updates": [],
            "up_to_date": [],
            "incompatible_with_spt": [],
        },
    )
    client = client_factory()

    result = services.update_mod(clean_repo.path, "CoolMod", db_path=db_path, client=client)

    assert result.notice == "Forge reports no update for CoolMod 1.0.0"


def _mock_update_recommendation(respx_router: respx.MockRouter, link: str, content_length: int) -> None:
    """Mock a single available update for ModA in /mods/updates response."""
    _mock_updates(
        respx_router,
        {
            "spt_version": SPT_VERSION,
            "updates": [
                {
                    "current_version": current_version_json(version_id=100, mod_id=10, name="ModA", version="1.0.0"),
                    "recommended_version": {
                        "id": 200,
                        "version": "2.0.0",
                        "link": link,
                        "content_length": content_length,
                        "fika_compatibility": "unknown",
                        "spt_versions": [SPT_VERSION],
                    },
                    "update_reason": "newer version available",
                }
            ],
            "blocked_updates": [],
            "up_to_date": [],
            "incompatible_with_spt": [],
        },
    )


def test_update_mod_tracked_file_conflict_aborts_and_names_owning_mod(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
) -> None:
    """Mirrors install_from_archive's tracked-file conflict, but for a file
    the *new* version of the mod being updated wants to write. If `if files:
    raise ConflictError(...)` in _check_update_conflicts were removed, this
    fails: the write over ModB's file would succeed and commit instead."""
    install_commit(
        clean_repo,
        "ModB",
        "1.0.0",
        mod_type="server",
        files={"SPT/user/mods/Shared/data.txt": "modb-content"},
        minute=1,
        **forge_trailers("20"),
    )
    install_commit(
        clean_repo,
        "ModA",
        "1.0.0",
        mod_type="server",
        files={"SPT/user/mods/ModA/package.json": json.dumps({"name": "ModA", "version": "1.0.0"})},
        minute=2,
        **forge_trailers("10", "100"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/ModA/package.json": json.dumps({"name": "ModA", "version": "2.0.0"}),
            "SPT/user/mods/Shared/data.txt": "new-content",
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/moda-conflict-2.0.0.zip"
    _mock_update_recommendation(respx_router, link, len(body))
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    client = client_factory()
    commits_before = len(clean_repo.log())

    with pytest.raises(ConflictError) as excinfo:
        services.update_mod(clean_repo.path, "ModA", db_path=db_path, client=client)

    assert "ModB" in str(excinfo.value)
    assert any(finding.mod == "ModB" for finding in excinfo.value.conflicts)
    assert (clean_repo.path / "SPT/user/mods/Shared/data.txt").read_text() == "modb-content"
    assert len(clean_repo.log()) == commits_before


def test_update_mod_missing_dependency_aborts_without_committing(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
) -> None:
    """If `if blocking: raise DependencyError(...)` in _check_update_conflicts
    were removed, this fails: the update with an unmet dependency would
    proceed and commit instead."""
    install_commit(
        clean_repo,
        "ModA",
        "1.0.0",
        mod_type="server",
        files={"SPT/user/mods/ModA/package.json": json.dumps({"name": "ModA", "version": "1.0.0"})},
        **forge_trailers("10", "100"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/ModA/package.json": json.dumps(
                {
                    "name": "ModA",
                    "version": "2.0.0",
                    "dependencies": {"MissingDep": ">=1.0.0"},
                }
            )
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/moda-dependency-2.0.0.zip"
    _mock_update_recommendation(respx_router, link, len(body))
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    client = client_factory()
    commits_before = len(clean_repo.log())

    with pytest.raises(DependencyError) as excinfo:
        services.update_mod(clean_repo.path, "ModA", db_path=db_path, client=client)

    assert {finding.mod for finding in excinfo.value.conflicts} == {"MissingDep"}
    assert len(clean_repo.log()) == commits_before
    package = json.loads((clean_repo.path / "SPT/user/mods/ModA/package.json").read_text())
    assert package["version"] == "1.0.0"


# --- CLI: update confirmation --------------------------------------------


def _available_update_data(link: str, content_length: int) -> JsonDict:
    """Build a /mods/updates payload reporting an available update for CoolMod."""
    return {
        "spt_version": SPT_VERSION,
        "updates": [
            {
                "current_version": current_version_json(
                    version_id=100,
                    mod_id=10,
                    name="CoolMod",
                    version="1.0.0",
                    slug="coolmod",
                ),
                "recommended_version": {
                    "id": 200,
                    "version": "2.0.0",
                    "link": link,
                    "content_length": content_length,
                    "fika_compatibility": "unknown",
                    "spt_versions": [SPT_VERSION],
                },
                "update_reason": "newer version available",
            }
        ],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }


def test_cli_update_check_never_prompts(
    clean_repo: Repo,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    install_commit: InstallCommit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sptmod update --check` prints the updates report without prompting for confirmation.

    With empty stdin, `sptmod update --check` exits 0 with the report (e.g., CoolMod)
    printed to stdout; if it prompted, CliRunner would hit EOF and exit with error.
    """
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server", **forge_trailers("10"))
    _mock_updates(respx_router, _available_update_data("https://dl.example/coolmod-2.0.0.zip", 1))
    monkeypatch.chdir(clean_repo.path)

    # Empty stdin: if --check tried to prompt, CliRunner would hit EOF and
    # the command would abort instead of exiting 0 with the report printed.
    result = cli_runner.invoke(cli_app, ["update", "--check"], input="")

    assert result.exit_code == 0
    assert "CoolMod" in result.stdout


def test_cli_update_without_yes_prompts_and_cancels_on_no(
    clean_repo: Repo,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    install_commit: InstallCommit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sptmod update` prompts and cancels when the user answers 'no' (no new commit).

    With input='n\\n', `sptmod update` exits 0 and the repo's commit count remains
    unchanged; the update is not applied.
    """
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server", **forge_trailers("10"))
    _mock_updates(respx_router, _available_update_data("https://dl.example/coolmod-2.0.0.zip", 1))
    commits_before = len(clean_repo.log())
    monkeypatch.chdir(clean_repo.path)

    result = cli_runner.invoke(cli_app, ["update"], input="n\n")

    assert result.exit_code == 0
    assert len(clean_repo.log()) == commits_before


def test_cli_update_without_yes_prompts_and_applies_on_yes(
    clean_repo: Repo,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sptmod update` prompts and applies the update when the user answers 'yes'.

    With input='y\\n', `sptmod update` exits 0 and a new commit is added (mod updated
    from 1.0.0 to 2.0.0).
    """
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server", **forge_trailers("10"))
    new_archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "2.0.0", "author": "Acme"})},
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/coolmod-2.0.0-yes.zip"
    _mock_updates(respx_router, _available_update_data(link, len(body)))
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    commits_before = len(clean_repo.log())
    monkeypatch.chdir(clean_repo.path)

    result = cli_runner.invoke(cli_app, ["update"], input="y\n")

    assert result.exit_code == 0
    assert len(clean_repo.log()) == commits_before + 1


def test_cli_update_yes_flag_applies_without_prompting(
    clean_repo: Repo,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If --yes does not exist yet or does not skip the prompt, this fails:
    either Typer rejects the unknown option, or empty stdin makes the prompt
    hit EOF and abort instead of applying."""
    install_commit(clean_repo, "CoolMod", "1.0.0", mod_type="server", **forge_trailers("10"))
    new_archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "2.0.0", "author": "Acme"})},
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/coolmod-2.0.0-flag.zip"
    _mock_updates(respx_router, _available_update_data(link, len(body)))
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    commits_before = len(clean_repo.log())
    monkeypatch.chdir(clean_repo.path)

    result = cli_runner.invoke(cli_app, ["update", "--yes"], input="")

    assert result.exit_code == 0
    assert len(clean_repo.log()) == commits_before + 1
