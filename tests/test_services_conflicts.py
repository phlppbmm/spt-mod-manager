"""Integration tests for the conflict/dependency checks wired into
``sptmod.core.services.install_from_archive``.

Covers: a tracked-file conflict aborts the install without ``--force`` and
names the owning mod; ``--force`` downgrades it to a warning and lets the
install proceed; a DLL duplicate in another mod folder is a warning that
never blocks the install; a missing dependency and an out-of-range
dependency both raise ``DependencyError`` listing every offender, and
``--force`` proceeds; an ``sptVersion`` mismatch is a warning only.

The ``untracked-file`` finding of ``check_file_conflicts`` is exercised at
the unit level in ``tests/test_conflicts.py`` instead of here: any
pre-existing untracked file below ``SPT/user/mods`` or ``BepInEx/plugins``
already makes ``Repo.is_clean()`` return ``False`` (both trees are fully
un-ignored by ``GITIGNORE_WHITELIST``), so ``install_from_archive`` always
raises ``DirtyTreeError`` at its upfront check before the conflict step is
ever reached; the ``untracked-file`` branch is therefore unreachable through
this wiring.
"""

import json

import pytest

from sptmod.core import game_dir, services
from sptmod.core.errors import ConflictError, DependencyError
from sptmod.core.repo import Repo

_BLOCKING_ERRORS = (ConflictError, DependencyError)


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def test_tracked_file_conflict_aborts_install_and_names_owning_mod(
    clean_repo: Repo, install_commit, make_archive
) -> None:
    install_commit(
        clean_repo,
        "ModA",
        version="1.0.0",
        files={"BepInEx/plugins/ModA.dll": "original-content"},
    )
    commits_before = len(clean_repo.log())
    archive = make_archive({"BepInEx/plugins/ModA.dll": "new-content"}, "zip")

    with pytest.raises(ConflictError) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    assert "ModA" in str(excinfo.value)
    assert any(finding.mod == "ModA" for finding in excinfo.value.conflicts)
    assert (
        clean_repo.path / "BepInEx/plugins/ModA.dll"
    ).read_text() == "original-content"
    assert len(clean_repo.log()) == commits_before


def test_tracked_file_conflict_with_force_proceeds_and_reports_warning(
    clean_repo: Repo, install_commit, make_archive
) -> None:
    install_commit(
        clean_repo,
        "ModA",
        version="1.0.0",
        files={"BepInEx/plugins/ModA.dll": "original-content"},
    )
    archive = make_archive({"BepInEx/plugins/ModA.dll": "new-content"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive, force=True)

    assert any(
        finding.kind == "tracked-file" and finding.severity == "warning"
        for finding in result.warnings
    )
    assert (
        clean_repo.path / "BepInEx/plugins/ModA.dll"
    ).read_text() == "new-content"


def test_dll_duplicate_across_mod_folders_is_a_warning_and_install_proceeds(
    clean_repo: Repo, install_commit, make_archive
) -> None:
    install_commit(
        clean_repo,
        "ModA",
        files={"BepInEx/plugins/ModA/Shared.dll": "shared-lib-v1"},
    )
    archive = make_archive(
        {"BepInEx/plugins/ModB/Shared.dll": "shared-lib-v2"}, "zip"
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert any(
        finding.kind == "dll-duplicate" and finding.severity == "warning"
        for finding in result.warnings
    )
    assert (clean_repo.path / "BepInEx/plugins/ModB/Shared.dll").read_text() == (
        "shared-lib-v2"
    )


def test_missing_and_out_of_range_dependency_abort_listing_all_offenders(
    clean_repo: Repo, install_commit, make_archive
) -> None:
    install_commit(
        clean_repo,
        "OldDep",
        version="0.1.0",
        mod_type="server",
        files={
            "SPT/user/mods/OldDep/package.json": json.dumps(
                {"name": "OldDep", "version": "0.1.0"}
            )
        },
    )
    commits_before = len(clean_repo.log())
    archive = make_archive(
        {
            "SPT/user/mods/NewMod/package.json": json.dumps(
                {
                    "name": "NewMod",
                    "version": "1.0.0",
                    "dependencies": {
                        "MissingDep": ">=1.0.0",
                        "OldDep": ">=1.0.0",
                    },
                }
            )
        },
        "zip",
    )

    with pytest.raises(DependencyError) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    offender_mods = {finding.mod for finding in excinfo.value.conflicts}
    assert offender_mods == {"MissingDep", "OldDep"}
    assert not (clean_repo.path / "SPT/user/mods/NewMod").exists()
    assert len(clean_repo.log()) == commits_before


def test_missing_and_out_of_range_dependency_with_force_proceeds(
    clean_repo: Repo, install_commit, make_archive
) -> None:
    install_commit(
        clean_repo,
        "OldDep",
        version="0.1.0",
        mod_type="server",
        files={
            "SPT/user/mods/OldDep/package.json": json.dumps(
                {"name": "OldDep", "version": "0.1.0"}
            )
        },
    )
    archive = make_archive(
        {
            "SPT/user/mods/NewMod/package.json": json.dumps(
                {
                    "name": "NewMod",
                    "version": "1.0.0",
                    "dependencies": {
                        "MissingDep": ">=1.0.0",
                        "OldDep": ">=1.0.0",
                    },
                }
            )
        },
        "zip",
    )

    result = services.install_from_archive(clean_repo.path, archive, force=True)

    warning_mods = {
        finding.mod for finding in result.warnings if finding.severity == "warning"
    }
    assert {"MissingDep", "OldDep"}.issubset(warning_mods)
    assert (clean_repo.path / "SPT/user/mods/NewMod/package.json").is_file()


def test_tracked_file_and_missing_dependency_both_block_and_are_both_reported(
    clean_repo: Repo, install_commit, make_archive
) -> None:
    install_commit(
        clean_repo,
        "ModA",
        version="1.0.0",
        files={"BepInEx/plugins/ModA.dll": "original-content"},
    )
    archive = make_archive(
        {
            "BepInEx/plugins/ModA.dll": "new-content",
            "SPT/user/mods/NewMod/package.json": json.dumps(
                {
                    "name": "NewMod",
                    "version": "1.0.0",
                    "dependencies": {"MissingDep": ">=1.0.0"},
                }
            ),
        },
        "zip",
    )

    with pytest.raises(_BLOCKING_ERRORS) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    message = str(excinfo.value)
    assert "ModA" in message
    assert "MissingDep" in message
    kinds = {finding.kind for finding in excinfo.value.conflicts}
    assert "tracked-file" in kinds
    assert "missing-dependency" in kinds


def test_spt_version_mismatch_is_a_warning_and_install_proceeds(
    clean_repo: Repo, make_archive
) -> None:
    # fake_game_dir's SPT/SPT_Data/configs/core.json reports sptVersion 4.1.0.
    archive = make_archive(
        {
            "SPT/user/mods/NewMod/package.json": json.dumps(
                {
                    "name": "NewMod",
                    "version": "1.0.0",
                    "sptVersion": "~4.0.0",
                }
            )
        },
        "zip",
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert any(
        finding.kind == "spt-version" and finding.severity == "warning"
        for finding in result.warnings
    )
    assert (clean_repo.path / "SPT/user/mods/NewMod/package.json").is_file()
