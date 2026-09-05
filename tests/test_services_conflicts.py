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

from fixture_types import InstallCommit, MakeArchive
from sptmod.core import services
from sptmod.core.errors import ConflictError, DependencyError
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")

_BLOCKING_ERRORS: tuple[type[ConflictError | DependencyError], ...] = (
    ConflictError,
    DependencyError,
)


def test_tracked_file_conflict_aborts_install_and_names_owning_mod(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """install_from_archive on a tracked-file conflict raises ConflictError before any commit.

    An archive containing a modified version of a file tracked in an earlier install commit
    causes install_from_archive to raise ConflictError with a Conflict.mod field naming
    ModA; the working tree file remains unchanged and no new commit is added.
    """
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
    assert (clean_repo.path / "BepInEx/plugins/ModA.dll").read_text() == "original-content"
    assert len(clean_repo.log()) == commits_before


def test_tracked_file_conflict_with_force_proceeds_and_reports_warning(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """With force=True, a tracked-file conflict downgrades to warning and the install proceeds.

    An archive with a modified BepInEx/plugins/ModA.dll (tracked in earlier commit) installs
    successfully when called with force=True, reports a warning with kind='tracked-file',
    and the new content replaces the old file on disk.
    """
    install_commit(
        clean_repo,
        "ModA",
        version="1.0.0",
        files={"BepInEx/plugins/ModA.dll": "original-content"},
    )
    archive = make_archive({"BepInEx/plugins/ModA.dll": "new-content"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive, force=True)

    assert any(finding.kind == "tracked-file" and finding.severity == "warning" for finding in result.warnings)
    assert (clean_repo.path / "BepInEx/plugins/ModA.dll").read_text() == "new-content"


def test_dll_duplicate_across_mod_folders_is_a_warning_and_install_proceeds(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """A DLL with the same name in different mod folders reports as warning and install proceeds.

    An archive for ModB containing BepInEx/plugins/ModB/Shared.dll (same base name as an
    existing BepInEx/plugins/ModA/Shared.dll from ModA) reports a warning with
    kind='dll-duplicate' but does not block the install.
    """
    install_commit(
        clean_repo,
        "ModA",
        files={"BepInEx/plugins/ModA/Shared.dll": "shared-lib-v1"},
    )
    archive = make_archive({"BepInEx/plugins/ModB/Shared.dll": "shared-lib-v2"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    assert any(finding.kind == "dll-duplicate" and finding.severity == "warning" for finding in result.warnings)
    assert (clean_repo.path / "BepInEx/plugins/ModB/Shared.dll").read_text() == ("shared-lib-v2")


def test_missing_and_out_of_range_dependency_abort_listing_all_offenders(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """An archive with missing and out-of-range dependencies raises DependencyError listing all.

    NewMod's dependencies on MissingDep (not installed) and OldDep (version 0.1.0, needs
    >=1.0.0) cause DependencyError with conflict findings for both mods; no new directory
    is created and no commit is added.
    """
    install_commit(
        clean_repo,
        "OldDep",
        version="0.1.0",
        mod_type="server",
        files={"SPT/user/mods/OldDep/package.json": json.dumps({"name": "OldDep", "version": "0.1.0"})},
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
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """With force=True, dependency violations downgrade to warnings and the install proceeds.

    NewMod with missing MissingDep and out-of-range OldDep dependencies installs when called
    with force=True; the mod's directory is created and the violation mods appear in the
    result.warnings list.
    """
    install_commit(
        clean_repo,
        "OldDep",
        version="0.1.0",
        mod_type="server",
        files={"SPT/user/mods/OldDep/package.json": json.dumps({"name": "OldDep", "version": "0.1.0"})},
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

    warning_mods = {finding.mod for finding in result.warnings if finding.severity == "warning"}
    assert {"MissingDep", "OldDep"}.issubset(warning_mods)
    assert (clean_repo.path / "SPT/user/mods/NewMod/package.json").is_file()


def test_tracked_file_and_missing_dependency_both_block_and_are_both_reported(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """Multiple conflict types (tracked-file and missing dependency) block together in one error.

    An archive with both a tracked-file conflict on ModA.dll and a missing dependency on
    MissingDep raises one exception (ConflictError or DependencyError) that includes all
    Conflict findings; error message names both ModA and MissingDep.
    """
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


def test_spt_version_mismatch_is_a_warning_and_install_proceeds(clean_repo: Repo, make_archive: MakeArchive) -> None:
    """An sptVersion mismatch (~4.0.0 vs game dir 4.1.0) reports warning and install proceeds.

    NewMod requiring sptVersion ~4.0.0 installs successfully in a repo with SPT 4.1.0;
    result.warnings includes a finding with kind='spt-version' and severity='warning'.
    """
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

    assert any(finding.kind == "spt-version" and finding.severity == "warning" for finding in result.warnings)
    assert (clean_repo.path / "SPT/user/mods/NewMod/package.json").is_file()
