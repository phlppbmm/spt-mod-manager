"""Tests for ``list_mods``, ``mod_files`` and ``history``.

Builds git history directly via the ``install_commit`` fixture (bypassing
the stubbed ``install_from_archive``) so these read-only services can be
exercised against a repository with several mods in different states.
"""

import json
from pathlib import Path

import pytest

from fixture_types import InstallCommit, MakeArchive
from sptmod.core import game_dir, services
from sptmod.core.errors import AmbiguousModError, ModNotFoundError
from sptmod.core.models import ACTIVE_STATES
from sptmod.core.repo import Repo


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock the game process detector to prevent false failures during list/history tests."""
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def test_list_mods_on_a_fresh_repo_returns_no_mods(clean_repo: Repo) -> None:
    """list_mods on a repository with only the baseline commit returns an empty list.

    A repository that has been initialized but has no mod install commits
    returns [].
    """
    assert services.list_mods(clean_repo.path) == []


def test_install_revert_reinstall_sequence_is_reflected_by_list_and_history(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """list_mods and history track install, remove, and reinstall sequences correctly.

    After installing v1.0.0, removing, and reinstalling v2.0.0, list_mods
    shows the active mod as v2.0.0, and history shows three commits in
    reverse chronological order.
    """
    first_archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0"})},
        "zip",
    )
    first = services.install_from_archive(clean_repo.path, first_archive)

    services.remove_mod(clean_repo.path, "CoolMod")

    second_archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "2.0.0"})},
        "zip",
    )
    second = services.install_from_archive(clean_repo.path, second_archive)

    active = services.list_mods(clean_repo.path)
    assert len(active) == 1
    assert active[0].name == "CoolMod"
    assert active[0].version == "2.0.0"
    assert active[0].state == "installed"

    commits = services.history(clean_repo.path, "CoolMod")
    assert [commit.hash for commit in commits][0] == second.mod.commit
    assert [commit.hash for commit in commits][-1] == first.mod.commit
    assert len(commits) == 3


def test_list_mods_default_returns_only_active_mods(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """list_mods by default excludes mods in disabled or removed states.

    After installing ModA, disabling ModB, and installing ModC, the default
    list_mods() returns only ModA and ModC.
    """
    install_commit(clean_repo, "ModA", minute=1)
    disable_target = install_commit(clean_repo, "ModB", minute=2)
    install_commit(clean_repo, "ModC", minute=3)
    clean_repo.revert(disable_target, extra_trailers={"Mod": "ModB", "Mod-State": "disabled"})

    result = services.list_mods(clean_repo.path)

    names = {mod.name for mod in result}
    assert names == {"ModA", "ModC"}
    for mod in result:
        assert mod.state in ACTIVE_STATES


def test_list_mods_include_all_also_returns_disabled_and_removed(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """list_mods with include_all=True returns all mods regardless of state.

    Calling list_mods(include_all=True) returns ModA (installed), ModB
    (disabled), and ModD (removed) with their respective state fields set.
    """
    install_commit(clean_repo, "ModA", minute=1)
    disable_target = install_commit(clean_repo, "ModB", minute=2)
    remove_target = install_commit(clean_repo, "ModD", minute=3)
    clean_repo.revert(disable_target, extra_trailers={"Mod": "ModB", "Mod-State": "disabled"})
    clean_repo.revert(remove_target, extra_trailers={"Mod": "ModD", "Mod-State": "removed"})

    result = services.list_mods(clean_repo.path, include_all=True)

    by_name = {mod.name: mod for mod in result}
    assert by_name.keys() == {"ModA", "ModB", "ModD"}
    assert by_name["ModB"].state == "disabled"
    assert by_name["ModD"].state == "removed"


def test_list_mods_excludes_a_mod_trailer_with_no_descriptive_commit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """A ``Mod`` trailer only ever exists with the full trailer set on a real
    install commit; a history written by hand can carry a bare ``Mod``
    trailer without one (no descriptive commit ever existed for that name).
    Such a "ghost" mod must not surface through list_mods at all. If the
    `if detail is None: continue` guard in _current_mods were removed, this
    fails: AttributeError building a ModInfo from field-less trailers, or a
    ghost entry with empty/garbage fields."""
    install_commit(clean_repo, "ModA", minute=1)
    ghost_file = clean_repo.path / "BepInEx/plugins/GhostMod/Ghost.dll"
    ghost_file.parent.mkdir(parents=True)
    ghost_file.write_bytes(b"ghost-bytes")
    clean_repo.commit(
        "chore: hand-written history",
        trailers={"Mod": "GhostMod"},
        paths=[Path("BepInEx/plugins/GhostMod/Ghost.dll")],
    )

    result = services.list_mods(clean_repo.path, include_all=True)

    assert {mod.name for mod in result} == {"ModA"}


def test_list_mods_reflects_the_youngest_commit_of_each_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """list_mods reflects a mod's most recent commit state, including version and status."""
    install_commit(clean_repo, "ModA", version="1.0.0", minute=1)
    install_commit(clean_repo, "ModA", version="1.1.0", state="updated", minute=2)

    result = services.list_mods(clean_repo.path)

    assert len(result) == 1
    assert result[0].version == "1.1.0"
    assert result[0].state == "updated"


def test_mod_files_returns_the_files_of_the_active_mods_install_commit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """mod_files returns the files from the mod's most recent install commit.

    After installing ModA with files {ModA.dll, ModA.cfg}, mod_files("ModA")
    returns both files with change='added'.
    """
    install_commit(
        clean_repo,
        "ModA",
        files={
            "BepInEx/plugins/ModA.dll": "a-content",
            "BepInEx/plugins/ModA.cfg": "cfg-content",
        },
    )

    result = services.mod_files(clean_repo.path, "ModA")

    assert {mod_file.path for mod_file in result} == {
        Path("BepInEx/plugins/ModA.dll"),
        Path("BepInEx/plugins/ModA.cfg"),
    }
    assert all(mod_file.change == "added" for mod_file in result)


def test_mod_files_matches_case_insensitively(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """mod_files performs case-insensitive name matching.

    Calling mod_files(path, "moda") after installing ModA returns the same
    files as mod_files(path, "ModA").
    """
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "a-content"})

    result = services.mod_files(clean_repo.path, "moda")

    assert {mod_file.path for mod_file in result} == {Path("BepInEx/plugins/ModA.dll")}


def test_mod_files_raises_mod_not_found_for_unknown_name(clean_repo: Repo) -> None:
    """mod_files raises ModNotFoundError for a non-existent mod name.

    Calling mod_files(path, "Ghost") raises ModNotFoundError.
    """
    with pytest.raises(ModNotFoundError):
        services.mod_files(clean_repo.path, "Ghost")


def test_mod_files_raises_ambiguous_mod_error_for_case_variant_names(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """mod_files raises AmbiguousModError when multiple case variants exist.

    After installing both CoolMod and coolmod, calling mod_files(path, "COOLMOD")
    raises AmbiguousModError with candidates={'CoolMod', 'coolmod'}.
    """
    install_commit(clean_repo, "CoolMod", minute=1)
    install_commit(clean_repo, "coolmod", minute=2)

    with pytest.raises(AmbiguousModError) as exc_info:
        services.mod_files(clean_repo.path, "COOLMOD")

    assert set(exc_info.value.candidates) == {"CoolMod", "coolmod"}


def test_history_without_name_returns_all_commits_newest_first(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """history without a name returns all commits in reverse chronological order.

    Calling history(path) on a repo with [baseline, ModA, ModB] commits
    returns them as [ModB, ModA, baseline].
    """
    baseline_hash = clean_repo.log()[0].hash
    first_hash = install_commit(clean_repo, "ModA", minute=1)
    second_hash = install_commit(clean_repo, "ModB", minute=2)

    result = services.history(clean_repo.path)

    assert [commit.hash for commit in result] == [
        second_hash,
        first_hash,
        baseline_hash,
    ]


def test_history_filtered_by_name_returns_only_that_mods_commits(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """history with a name returns only commits touching that mod, newest first.

    After installing ModA v1.0.0, then ModB, then ModA v1.1.0, calling
    history(path, "ModA") returns only the two ModA commits in reverse order.
    """
    first_hash = install_commit(clean_repo, "ModA", version="1.0.0", minute=1)
    install_commit(clean_repo, "ModB", minute=2)
    second_hash = install_commit(clean_repo, "ModA", version="1.1.0", state="updated", minute=3)

    result = services.history(clean_repo.path, "ModA")

    assert [commit.hash for commit in result] == [second_hash, first_hash]


def test_history_name_filter_matches_case_insensitively(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """history name filtering performs case-insensitive matching.

    After installing ModA, calling history(path, "moda") returns the same
    commit as history(path, "ModA").
    """
    commit_hash = install_commit(clean_repo, "ModA", minute=1)

    result = services.history(clean_repo.path, "moda")

    assert [commit.hash for commit in result] == [commit_hash]


def test_history_raises_mod_not_found_for_unknown_name(clean_repo: Repo) -> None:
    """history raises ModNotFoundError for a non-existent mod name.

    Calling history(path, "Ghost") raises ModNotFoundError.
    """
    with pytest.raises(ModNotFoundError):
        services.history(clean_repo.path, "Ghost")


def test_history_raises_ambiguous_mod_error_for_case_variant_names(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """history raises AmbiguousModError when multiple case variants exist.

    After installing both CoolMod and coolmod, calling history(path, "COOLMOD")
    raises AmbiguousModError with candidates={'CoolMod', 'coolmod'}.
    """
    install_commit(clean_repo, "CoolMod", minute=1)
    install_commit(clean_repo, "coolmod", minute=2)

    with pytest.raises(AmbiguousModError) as exc_info:
        services.history(clean_repo.path, "COOLMOD")

    assert set(exc_info.value.candidates) == {"CoolMod", "coolmod"}
