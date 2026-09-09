"""Tests for the "mod runtime data" carve-out of ``sptmod.core.services``.

An active mod's own directory (a top-level directory under
``<runtime>/user/mods/``, ``BepInEx/plugins/`` or ``BepInEx/patchers/`` that
the mod's recorded files live in) may contain untracked files the mod itself
wrote while the game ran. Those files are "runtime data": ``status()`` leaves
them out of the report entirely, they never block install/remove/disable/
enable, are never snapshotted, and are deleted together with the mod on
``remove_mod``/``disable_mod``. A directory no active mod owns is still a
manual-mod blocker; a modified *tracked* file inside an owned directory is
the mod's own settings change and is snapshotted instead of blocking.
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import clean_repo_at
from fixture_types import InstallCommit, MakeArchive
from sptmod.core import services
from sptmod.core.errors import DirtyTreeError
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

# --- status: runtime data is left out of the report entirely ----------------


@pytest.mark.parametrize("mod_dir", ["BepInEx/plugins", "BepInEx/patchers"], ids=["plugins-dir", "patchers-dir"])
def test_status_ignores_runtime_data_in_a_managed_client_mod_dir(
    clean_repo: Repo, install_commit: InstallCommit, mod_dir: str
) -> None:
    """status() omits untracked files under an active mod's directory from the report.

    The mod wrote an ``EditorDefaults.json`` next to its DLL, a nested
    ``cache/nested.json`` and a binary ``cache.bin`` while the game ran. None
    of them was ever tracked, and none of them may show up as a manual mod or
    as untracked content, whether the mod lives in the plugins or in the
    patchers directory: the report has to stay empty.
    """
    install_commit(
        clean_repo,
        "ClientMod",
        files={f"{mod_dir}/ClientMod/ClientMod.dll": "v1"},
    )
    mod_path = clean_repo.path / mod_dir / "ClientMod"
    (mod_path / "EditorDefaults.json").write_text("generated while playing", encoding="utf-8")
    (mod_path / "cache.bin").write_bytes(b"binary cache data")
    nested = mod_path / "cache/nested.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested runtime data", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report == services.StatusReport(spt_version="4.1.0")


@pytest.mark.parametrize(
    "game_dir_fixture",
    [
        pytest.param("fake_game_dir", id="spt-4.0-layout"),
        pytest.param("fake_game_dir_runtime", id="spt-4.1-layout"),
    ],
)
def test_status_ignores_runtime_data_in_a_managed_server_mod_dir(
    game_dir_fixture: str, request: pytest.FixtureRequest, install_commit: InstallCommit
) -> None:
    """Status ignores runtime data in server mod directories across both game layouts."""
    game_dir_path: Path = request.getfixturevalue(game_dir_fixture)
    repo = clean_repo_at(game_dir_path)
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

    assert not report.manual_mods
    assert not report.other_untracked


# --- install: runtime data never blocks, is never snapshotted ---------------


def test_install_of_another_mod_is_not_blocked_by_runtime_data(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """install_from_archive for NewMod succeeds despite untracked files in ClientMod's dir.

    An untracked EditorDefaults.json in BepInEx/plugins/ClientMod does not block
    install of another mod; no snapshot commit is made and the untracked file survives.
    """
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
    """install_from_archive still blocks on foreign directories but omits runtime data.

    Manual SPT/user/mods/Foreign/x.txt causes DirtyTreeError; the error message names
    Foreign but not the untracked EditorDefaults.json in the owned ClientMod directory.
    """
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


@pytest.mark.parametrize(
    "delete",
    [pytest.param(False, id="modified"), pytest.param(True, id="deleted")],
)
def test_install_snapshots_a_modified_tracked_file_inside_an_owned_dir(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive, delete: bool
) -> None:
    """install_from_archive secures modified tracked files of an active mod instead of blocking.

    BepInEx/plugins/ClientMod/config.json is rewritten or deleted the way a mod changes its
    own settings while the game runs; install_from_archive must succeed, record the change as
    'snapshot: mod settings changed' before the new mod commit and leave the tree clean. Mods
    writing into their own directory are normal operation, not files sptmod cannot account
    for.
    """
    install_commit(
        clean_repo,
        "ClientMod",
        files={
            "BepInEx/plugins/ClientMod/ClientMod.dll": "v1",
            "BepInEx/plugins/ClientMod/config.json": '{"v": 1}',
        },
    )
    config = clean_repo.path / "BepInEx/plugins/ClientMod/config.json"
    if delete:
        config.unlink()
    else:
        config.write_text('{"v": 2}', encoding="utf-8")
    archive = make_archive({"BepInEx/plugins/NewMod.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "NewMod"
    subjects = [commit.subject for commit in services.history(clean_repo.path)]
    assert subjects[0].startswith("mod: NewMod")
    assert subjects[1] == "snapshot: mod settings changed"
    assert not services.status(clean_repo.path).modified_mod_settings
    assert clean_repo.is_clean()


@pytest.mark.parametrize(
    ("dirtied", "snapshot_subject"),
    [
        pytest.param(
            "SPT/user/profiles/abc.json",
            "snapshot: automatic snapshot before changing mods",
            id="after-profile-snapshot",
        ),
        pytest.param(
            "BepInEx/plugins/ClientMod/config.json",
            "snapshot: mod settings changed",
            id="after-mod-settings-snapshot",
        ),
        pytest.param(
            "BepInEx/plugins/spt/spt-common.dll",
            "snapshot: game files updated",
            id="after-game-files-snapshot",
        ),
    ],
)
def test_runtime_data_stays_owned_after_a_snapshot_commit(
    clean_repo: Repo,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    dirtied: str,
    snapshot_subject: str,
) -> None:
    """A snapshot commit does not take an active mod's directory away from it.

    One install secures a changed profile, mod setting or game file in a trailer-less
    snapshot commit; a runtime file the mod writes afterwards must still count as its data,
    so status() stays empty and the next install is not blocked. This is the regression where
    a snapshot touching a mod's directory made its own runtime data look foreign.
    """
    install_commit(
        clean_repo,
        "ClientMod",
        files={
            "BepInEx/plugins/ClientMod/ClientMod.dll": "v1",
            "BepInEx/plugins/ClientMod/config.json": '{"v": 1}',
        },
    )
    spt_dll = clean_repo.path / "BepInEx/plugins/spt/spt-common.dll"
    spt_dll.parent.mkdir(parents=True)
    spt_dll.write_bytes(b"4.1.0-build")
    clean_repo.commit("chore: seed SPT client files", paths=[Path("BepInEx/plugins/spt/spt-common.dll")])
    (clean_repo.path / dirtied).write_text("changed while playing", encoding="utf-8")
    services.install_from_archive(clean_repo.path, make_archive({"BepInEx/plugins/First.dll": b"dll"}, "zip"))
    runtime_file = clean_repo.path / "BepInEx/plugins/ClientMod/prices-regular.json"
    runtime_file.write_text("{}", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert [commit.subject for commit in services.history(clean_repo.path)][1] == snapshot_subject
    assert not report.manual_mods
    assert not report.other_untracked
    second_archive = make_archive({"BepInEx/plugins/Second.dll": b"dll"}, "zip")
    assert services.install_from_archive(clean_repo.path, second_archive).mod.name == "Second"


# --- remove/disable: runtime data is deleted with the mod --------------------


@pytest.mark.parametrize(
    ("action", "mod_dir"),
    [
        pytest.param(services.remove_mod, "BepInEx/plugins", id="remove_mod-plugins"),
        pytest.param(services.disable_mod, "BepInEx/plugins", id="disable_mod-plugins"),
        pytest.param(services.remove_mod, "BepInEx/patchers", id="remove_mod-patchers"),
        pytest.param(services.disable_mod, "BepInEx/patchers", id="disable_mod-patchers"),
    ],
)
def test_removing_or_disabling_deletes_runtime_data_and_the_now_empty_directory(
    action: Callable[[Path, str], object],
    clean_repo: Repo,
    install_commit: InstallCommit,
    mod_dir: str,
) -> None:
    """remove_mod and disable_mod delete tracked files and runtime data alike.

    The mod's directory holds two tracked files and, next to them, a generated
    JSON file, a binary cache and two files in subdirectories the mod created
    while the game ran. Removing or disabling the mod takes the whole
    directory with it, while the plugins or patchers directory it lived in
    stays, because other mods live there too.
    """
    install_commit(
        clean_repo,
        "ClientMod",
        files={
            f"{mod_dir}/ClientMod/ClientMod.dll": "v1",
            f"{mod_dir}/ClientMod/sub/tracked.json": "tracked content",
        },
    )
    mod_path = clean_repo.path / mod_dir / "ClientMod"
    (mod_path / "Editor.json").write_text("generated", encoding="utf-8")
    (mod_path / "cache.bin").write_bytes(b"binary cache data")
    (mod_path / "sub/nested.bin").write_bytes(b"nested cache data")
    nested_runtime = mod_path / "sub/other/leftover.txt"
    nested_runtime.parent.mkdir(parents=True)
    nested_runtime.write_text("nested leftover", encoding="utf-8")

    action(clean_repo.path, "ClientMod")

    assert not mod_path.exists()
    assert (clean_repo.path / mod_dir).is_dir()


def test_enable_mod_after_disable_restores_only_the_recorded_files(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """enable_mod restores only tracked files, leaving deleted runtime data absent.

    After disabling ClientMod, enable_mod restores BepInEx/plugins/ClientMod/ClientMod.dll
    with original content but does not restore the untracked Editor.json that was deleted.
    """
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
    """remove_mod deletes only the specified mod's files, preserving other mods in the dir.

    Removing ModA leaves ModB's BepInEx/plugins/SharedDir/b.txt intact and also removes
    the untracked runtime.json because it was generated while both mods were active.
    """
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
    """A directory with files added after a mod was disabled is reported as manual.

    After disabling ClientMod, adding ReAdded.dll to BepInEx/plugins/ClientMod causes
    status() to report the directory as a manual_mod with path and files.
    """
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


def test_remove_mod_leaves_an_unremovable_leftover_in_place(clean_repo: Repo, install_commit: InstallCommit) -> None:
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
