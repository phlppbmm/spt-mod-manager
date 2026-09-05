"""Tests for the "mod runtime data" carve-out of ``sptmod.core.services``.

An active mod's own directory (a top-level directory under
``<runtime>/user/mods/``, ``BepInEx/plugins/`` or ``BepInEx/patchers/`` that
the mod's recorded files live in) may contain untracked files the mod itself
wrote while the game ran. Those files are "runtime data": ``status()`` leaves
them out of the report entirely, they never block install/remove/disable/
enable, are never snapshotted, and are deleted together with the mod on
``remove_mod``/``disable_mod``. A directory no active mod owns is still a
manual-mod blocker; a modified *tracked* file inside an owned directory still
blocks.
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from fixture_types import InstallCommit, MakeArchive
from sptmod.core import game_dir, services
from sptmod.core.errors import DirtyTreeError
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(detect_layout(path))
    repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    return repo


# --- status: runtime data is left out of the report entirely ----------------


def test_status_ignores_runtime_data_in_a_managed_client_plugin_dir(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/EditorDefaults.json"
    runtime_file.write_text("generated while playing", encoding="utf-8")
    nested = clean_repo.path / "BepInEx/plugins/ClientMod/cache/nested.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested runtime data", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report == services.StatusReport()


@pytest.mark.parametrize("game_dir_fixture", ["fake_game_dir", "fake_game_dir_runtime"])
def test_status_ignores_runtime_data_in_a_managed_server_mod_dir(
    game_dir_fixture: str, request: pytest.FixtureRequest, install_commit: InstallCommit
) -> None:
    game_dir_path: Path = request.getfixturevalue(game_dir_fixture)
    repo = _clean_repo(game_dir_path)
    mods_dir = detect_layout(game_dir_path).mods_dir.as_posix()
    install_commit(
        repo,
        "ServerMod",
        mod_type="server",
        files={f"{mods_dir}/ServerMod/package.json": '{"name": "ServerMod"}'},
    )
    runtime_file = game_dir_path / mods_dir / "ServerMod" / "CoreOverrides.json"
    runtime_file.write_text("generated while playing", encoding="utf-8")
    nested = game_dir_path / mods_dir / "ServerMod" / "cache" / "nested.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested runtime data", encoding="utf-8")

    report = services.status(game_dir_path)

    assert report.manual_mods == []
    assert report.other_untracked == []


def test_status_tolerates_runtime_data_in_a_managed_patcher_dir(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    install_commit(
        clean_repo,
        "PatchMod",
        files={"BepInEx/patchers/PatchMod/PatchMod.dll": "v1"},
    )
    runtime_file = clean_repo.path / "BepInEx/patchers/PatchMod/cache.bin"
    runtime_file.write_bytes(b"binary cache data")

    report = services.status(clean_repo.path)

    assert report.manual_mods == []
    assert report.other_untracked == []


# --- install: runtime data never blocks, is never snapshotted ---------------


def test_install_of_another_mod_is_not_blocked_by_runtime_data(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/EditorDefaults.json"
    runtime_file.write_text("generated while playing", encoding="utf-8")
    commits_before = len(clean_repo.log())
    archive = make_archive({"BepInEx/plugins/NewMod.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "NewMod"
    subjects = [commit.subject for commit in clean_repo.log()]
    assert len(subjects) == commits_before + 1
    assert not any(subject.startswith("snapshot:") for subject in subjects)
    assert runtime_file.read_text(encoding="utf-8") == "generated while playing"


def test_install_still_blocks_on_a_foreign_directory_and_omits_owned_runtime_paths(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/EditorDefaults.json"
    runtime_file.write_text("generated while playing", encoding="utf-8")
    foreign = clean_repo.path / "SPT/user/mods/Foreign/x.txt"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("manually placed", encoding="utf-8")
    archive = make_archive({"BepInEx/plugins/NewMod.dll": b"dll-bytes"}, "zip")

    with pytest.raises(DirtyTreeError) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    message = str(excinfo.value)
    assert "adopt" in message
    assert "SPT/user/mods/Foreign" in message
    assert "EditorDefaults.json" not in message
    assert foreign.read_text(encoding="utf-8") == "manually placed"


def test_install_with_profile_changes_and_runtime_data_snapshots_exactly_once(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """Runtime data next to a dirty profile must not confuse the snapshot path.

    ``_ensure_installable`` re-checks the tree after auto-snapshotting; the
    untracked runtime data must be ignored on both passes so exactly one
    ``snapshot:`` commit is made, not zero (rejected as unmanaged) or more
    than one.
    """
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/EditorDefaults.json"
    runtime_file.write_text("generated while playing", encoding="utf-8")
    profile = clean_repo.path / "SPT/user/profiles/abc.json"
    profile.write_text(profile.read_text() + "\n")
    commits_before = len(clean_repo.log())
    archive = make_archive({"BepInEx/plugins/NewMod.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "NewMod"
    subjects = [commit.subject for commit in clean_repo.log()]
    assert len(subjects) == commits_before + 2
    assert sum(subject.startswith("snapshot:") for subject in subjects) == 1
    assert runtime_file.read_text(encoding="utf-8") == "generated while playing"


def test_install_still_blocks_on_a_modified_tracked_file_inside_an_owned_dir(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    (clean_repo.path / "BepInEx/plugins/ClientMod/ClientMod.dll").write_text(
        "tampered", encoding="utf-8"
    )
    archive = make_archive({"BepInEx/plugins/NewMod.dll": b"dll-bytes"}, "zip")

    with pytest.raises(DirtyTreeError) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    assert "BepInEx/plugins/ClientMod/ClientMod.dll" in str(excinfo.value)


# --- remove/disable: runtime data is deleted with the mod --------------------


@pytest.mark.parametrize(
    "action",
    [services.remove_mod, services.disable_mod],
    ids=["remove_mod", "disable_mod"],
)
def test_removing_or_disabling_deletes_runtime_data_and_the_now_empty_directory(
    action: Callable[[Path, str], object],
    clean_repo: Repo,
    install_commit: InstallCommit,
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={
            "BepInEx/plugins/ClientMod/ClientMod.dll": "v1",
            "BepInEx/plugins/ClientMod/sub/tracked.json": "tracked content",
        },
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/Editor.json"
    runtime_file.write_text("generated", encoding="utf-8")
    nested_runtime = (
        clean_repo.path / "BepInEx/plugins/ClientMod/sub/other/leftover.txt"
    )
    nested_runtime.parent.mkdir(parents=True)
    nested_runtime.write_text("nested leftover", encoding="utf-8")

    action(clean_repo.path, "ClientMod")

    assert not (clean_repo.path / "BepInEx/plugins/ClientMod").exists()
    assert (clean_repo.path / "BepInEx/plugins").is_dir()


@pytest.mark.parametrize(
    "action",
    [services.remove_mod, services.disable_mod],
    ids=["remove_mod", "disable_mod"],
)
def test_removing_or_disabling_clears_a_patcher_dir_but_keeps_the_patchers_root(
    action: Callable[[Path, str], object],
    clean_repo: Repo,
    install_commit: InstallCommit,
) -> None:
    install_commit(
        clean_repo,
        "PatchMod",
        files={"BepInEx/patchers/PatchMod/PatchMod.dll": "v1"},
    )
    runtime_file = clean_repo.path / "BepInEx/patchers/PatchMod/cache.bin"
    runtime_file.write_bytes(b"binary cache data")
    nested_runtime = clean_repo.path / "BepInEx/patchers/PatchMod/sub/nested.bin"
    nested_runtime.parent.mkdir(parents=True)
    nested_runtime.write_bytes(b"nested cache data")

    action(clean_repo.path, "PatchMod")

    assert not (clean_repo.path / "BepInEx/patchers/PatchMod").exists()
    assert (clean_repo.path / "BepInEx/patchers").is_dir()


def test_enable_mod_after_disable_restores_only_the_recorded_files(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "original-bytes"},
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/Editor.json"
    runtime_file.write_text("generated", encoding="utf-8")
    services.disable_mod(clean_repo.path, "ClientMod")

    services.enable_mod(clean_repo.path, "ClientMod")

    assert (clean_repo.path / "BepInEx/plugins/ClientMod/ClientMod.dll").read_text(
        encoding="utf-8"
    ) == "original-bytes"
    assert not runtime_file.exists()


def test_remove_mod_keeps_a_second_active_mods_files_in_a_shared_directory(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    install_commit(
        clean_repo,
        "ModA",
        files={"BepInEx/plugins/SharedDir/a.txt": "a-content"},
        minute=1,
    )
    install_commit(
        clean_repo,
        "ModB",
        files={"BepInEx/plugins/SharedDir/b.txt": "b-content"},
        minute=2,
    )
    runtime_file = clean_repo.path / "BepInEx/plugins/SharedDir/runtime.json"
    runtime_file.write_text("generated while playing", encoding="utf-8")

    services.remove_mod(clean_repo.path, "ModA")

    shared_dir = clean_repo.path / "BepInEx/plugins/SharedDir"
    assert not (shared_dir / "a.txt").exists()
    assert (shared_dir / "b.txt").read_text(encoding="utf-8") == "b-content"
    assert not runtime_file.exists()
    assert shared_dir.is_dir()


# --- a directory no *active* mod owns is still a manual-mod blocker ----------


def test_a_directory_owned_only_by_a_disabled_mod_resurfaces_as_manual(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    services.disable_mod(clean_repo.path, "ClientMod")
    leftover = clean_repo.path / "BepInEx/plugins/ClientMod/ReAdded.dll"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_bytes(b"leftover-bytes")

    report = services.status(clean_repo.path)

    assert len(report.manual_mods) == 1
    manual = report.manual_mods[0]
    assert manual.path == Path("BepInEx/plugins/ClientMod")
    assert manual.files == [Path("BepInEx/plugins/ClientMod/ReAdded.dll")]


# --- runtime-data cleanup is best-effort under filesystem permission errors --


def test_remove_mod_leaves_an_unremovable_leftover_in_place(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """A leftover the OS refuses to delete (its containing directory is not
    writable) must not abort remove_mod; it simply stays. If the `except
    OSError: continue` around the unlink were removed, this fails: the
    PermissionError would propagate out of remove_mod instead."""
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    locked_dir = clean_repo.path / "BepInEx/plugins/ClientMod/sub"
    locked_dir.mkdir(parents=True)
    leftover = locked_dir / "leftover.txt"
    leftover.write_text("generated while playing", encoding="utf-8")
    os.chmod(locked_dir, 0o555)
    try:
        services.remove_mod(clean_repo.path, "ClientMod")
    finally:
        os.chmod(locked_dir, 0o755)

    assert leftover.exists()
    assert leftover.read_text(encoding="utf-8") == "generated while playing"


def test_remove_mod_leaves_a_now_empty_dir_in_place_when_its_parent_is_locked(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """A leftover's directory becomes deletable, but pruning the now-empty
    directory fails because its own parent is not writable; this must not
    abort remove_mod either. If the `except OSError: continue` around
    _prune_empty were removed, this fails: the PermissionError from rmdir
    would propagate out of remove_mod instead."""
    install_commit(
        clean_repo,
        "ClientMod",
        files={"BepInEx/plugins/ClientMod/ClientMod.dll": "v1"},
    )
    locked_dir = clean_repo.path / "BepInEx/plugins/ClientMod/sub"
    prunable_dir = locked_dir / "subsub"
    prunable_dir.mkdir(parents=True)
    leftover = prunable_dir / "leftover.txt"
    leftover.write_text("generated while playing", encoding="utf-8")
    os.chmod(locked_dir, 0o555)
    try:
        services.remove_mod(clean_repo.path, "ClientMod")
    finally:
        os.chmod(locked_dir, 0o755)

    assert not leftover.exists()
    assert prunable_dir.is_dir()
