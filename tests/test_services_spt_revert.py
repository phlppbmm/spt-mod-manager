"""Behaviour of ``revert_spt_update``: the undo of a version change.

Covers the undo of a version change and of a game-file snapshot, repeated
undos stepping back one change each, an installation of unknown version, a
running game, a directory that is no game directory and the plain refusals
when a change no longer fits.
"""

from pathlib import Path

import pytest

from conftest import clean_repo_at
from fixture_types import InstallCommit, MakeArchive, MakeSptRelease
from fixtures_spt import (
    INSTALLED,
    NEW,
    NEWEST,
    SPT_OWN_PLUGIN,
    SptReleaseSource,
    offer,
    release_subjects,
    unversioned_game_dir,
    write_client_exe,
)
from pe_builder import write_server_exe
from sptmod.core import game_dir, services
from sptmod.core.errors import GameRunningError, GitError, NotAGameDirError
from sptmod.core.game_dir import SPT_VERSION_CONFIG_KEY
from sptmod.core.models import ModFile
from sptmod.core.repo import Repo

# How many characters of a commit hash a message would have to carry for a
# user to see one.
_SHORT_HASH_LENGTH = 7


# --- revert_spt_update ---------------------------------------------------


def test_revert_puts_the_previous_release_back(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: InstallCommit,
) -> None:
    """revert_spt_update() undoes the last version change in one commit.

    The program files hold what they held before, the version is the one
    before again, and the installed mods keep their files, because a version
    change never contained any.
    """
    install_commit(Repo(spt_game_dir), "ModA", files={"BepInEx/plugins/ModA.dll": "mod"})
    offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)

    result = services.revert_spt_update(spt_game_dir)

    assert (result.previous_version, result.version) == (NEW, INSTALLED)
    assert release_subjects(spt_game_dir) == [
        f'Revert "spt: {NEW}"',
        f"spt: {NEW}",
        "mod: ModA 1.0.0",
        "chore: clean SPT 4.1.0",
    ]
    assert not (spt_game_dir / "winhttp.dll").exists()
    assert (spt_game_dir / "BepInEx/plugins/ModA.dll").read_text(
        encoding="utf-8"
    ) == "mod"
    assert [mod.name for mod in services.list_mods(spt_game_dir)] == ["ModA"]
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == INSTALLED
    assert result.files == [
        ModFile(path=Path("BepInEx/core/BepInEx.dll"), change="deleted"),
        ModFile(path=Path("BepInEx/patchers/spt-prepatch.dll"), change="deleted"),
        ModFile(path=Path("BepInEx/plugins/spt/spt-core.dll"), change="deleted"),
        ModFile(path=Path("LICENSE-BepInEx.txt"), change="deleted"),
        ModFile(path=Path("SPT_Runtime/SPT.Server.exe"), change="modified"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/configs/core.json"), change="modified"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/database/x.json"), change="deleted"),
        ModFile(path=Path("doorstop_config.ini"), change="deleted"),
        ModFile(path=Path("winhttp.dll"), change="deleted"),
    ]


def test_revert_undoes_the_securing_of_files_an_update_outside_sptmod_wrote(
    fake_game_dir_runtime: Path,
) -> None:
    """revert_spt_update() also takes back a snapshot of changed game files.

    An SPT update run outside sptmod is secured as ``snapshot: game files
    updated``; undoing the version change means undoing that commit, and a
    version that stays unreadable is not stored.
    """
    path = unversioned_game_dir(fake_game_dir_runtime)
    (path / "SPT_Runtime/SPT_Data/database/new.json").parent.mkdir(exist_ok=True)
    (path / "SPT_Runtime/SPT_Data/database/new.json").write_text("{}", encoding="utf-8")
    services.snapshot(path, "played a raid")

    result = services.revert_spt_update(path)

    assert (result.previous_version, result.version) == ("", "")
    assert release_subjects(path)[:2] == [
        'Revert "snapshot: game files updated"',
        "snapshot: game files updated",
    ]
    assert not (path / "SPT_Runtime/SPT_Data/database/new.json").exists()
    assert Repo(path).config_get(SPT_VERSION_CONFIG_KEY) is None


def test_revert_without_a_version_change_says_there_is_nothing_to_undo(
    spt_game_dir: Path,
) -> None:
    """revert_spt_update() raises GitError when no version change was ever made."""
    with pytest.raises(GitError, match="There is no SPT version change to undo."):
        services.revert_spt_update(spt_game_dir)

    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_revert_refuses_while_the_game_is_running(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revert_spt_update() raises GameRunningError and leaves the version in place."""
    offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: ["SPT.Server.exe"])

    with pytest.raises(GameRunningError, match="close it first"):
        services.revert_spt_update(spt_game_dir)

    assert release_subjects(spt_game_dir)[0] == f"spt: {NEW}"


def test_revert_rejects_a_directory_that_is_no_game_directory(tmp_path: Path) -> None:
    """revert_spt_update() raises NotAGameDirError before it touches git."""
    with pytest.raises(NotAGameDirError, match="not an SPT game directory"):
        services.revert_spt_update(tmp_path)


def test_revert_twice_after_one_version_change_finds_nothing_left_to_undo(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """revert_spt_update() called twice after one change refuses the second time.

    One release was applied and taken back; the undo commit takes its subject
    back with it, so the change below it is no candidate any more. The second
    undo therefore says in plain words that there is nothing to undo and
    leaves both the history and the game directory as the first undo left them.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)
    services.revert_spt_update(spt_game_dir)

    with pytest.raises(GitError, match="There is no SPT version change to undo."):
        services.revert_spt_update(spt_game_dir)

    assert release_subjects(spt_game_dir) == [
        f'Revert "spt: {NEW}"',
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert not (spt_game_dir / "winhttp.dll").exists()
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == INSTALLED


def test_revert_twice_after_two_version_changes_reaches_the_older_change(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """revert_spt_update() steps back one version change per call.

    Two releases were applied one after the other; each undo cancels exactly
    one of them, so the first lands on the release in between and the second
    on the version the installation started with. The files and the stored
    version follow each step, because that is what "one step back" means.
    """
    offer(spt_release_source, make_spt_release, NEW)
    offer(spt_release_source, make_spt_release, NEWEST)
    services.apply_spt_release(spt_game_dir, NEW)
    services.apply_spt_release(spt_game_dir, NEWEST)

    first = services.revert_spt_update(spt_game_dir)
    middle = (spt_game_dir / "winhttp.dll").read_text(encoding="utf-8")
    second = services.revert_spt_update(spt_game_dir)

    assert (first.previous_version, first.version) == (NEWEST, NEW)
    assert middle == f"doorstop of {NEW}"
    assert (second.previous_version, second.version) == (NEW, INSTALLED)
    assert release_subjects(spt_game_dir) == [
        f'Revert "spt: {NEW}"',
        f'Revert "spt: {NEWEST}"',
        f"spt: {NEWEST}",
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert not (spt_game_dir / "winhttp.dll").exists()
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == INSTALLED


def test_revert_twice_takes_back_both_securings_of_changed_game_files(
    spt_game_dir: Path,
) -> None:
    """revert_spt_update() cancels one securing of changed game files per call.

    An SPT update outside sptmod is secured twice under the same subject; one
    undo may therefore not stand for both. The first undo brings the file back
    to the state the first securing held, the second removes it altogether,
    and a third call finds nothing left -- two changes, two undos, no more.
    """
    data = spt_game_dir / "SPT_Runtime/SPT_Data/database/loot.json"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("first update", encoding="utf-8")
    services.snapshot(spt_game_dir, "after the first update")
    data.write_text("second update", encoding="utf-8")
    services.snapshot(spt_game_dir, "after the second update")

    services.revert_spt_update(spt_game_dir)
    after_first = data.read_text(encoding="utf-8")
    second = services.revert_spt_update(spt_game_dir)

    assert after_first == "first update"
    assert not data.exists()
    assert second.files == [
        ModFile(
            path=Path("SPT_Runtime/SPT_Data/database/loot.json"), change="deleted"
        )
    ]
    with pytest.raises(GitError, match="There is no SPT version change to undo."):
        services.revert_spt_update(spt_game_dir)


def _assert_plain_undo_failure(error: GitError, path: Path, detail: str) -> None:
    """Assert ``error`` reports ``detail`` in the words a user gets to read.

    The message reaches the user unchanged, so it may name neither the git
    operation that failed nor any commit of the repository at ``path``.
    """
    message = str(error)
    assert message == f"Going back to the previous SPT version did not work: {detail}"
    assert "revert" not in message.lower()
    assert "commit" not in message.lower()
    assert not any(
        commit.hash[:_SHORT_HASH_LENGTH] in message
        for commit in services.history(path)
    )


def test_revert_names_the_files_that_changed_since_the_version_change(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    make_archive: MakeArchive,
) -> None:
    """revert_spt_update() reports the files that no longer fit, in plain words.

    A mod was force-installed over a file SPT itself owns, so taking the
    version change back would have to remove a file that now holds the mod's
    content. The undo fails naming that file, and it changes nothing: the
    history and the file stay as they were.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)
    archive = make_archive({SPT_OWN_PLUGIN: "patched by a mod"}, "zip")
    services.install_from_archive(spt_game_dir, archive, force=True)

    with pytest.raises(GitError) as failure:
        services.revert_spt_update(spt_game_dir)

    _assert_plain_undo_failure(
        failure.value,
        spt_game_dir,
        f"these files have changed since then: {SPT_OWN_PLUGIN}",
    )
    assert failure.value.conflicting_paths == [Path(SPT_OWN_PLUGIN)]
    assert release_subjects(spt_game_dir) == [
        "mod: spt-core",
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert (spt_game_dir / SPT_OWN_PLUGIN).read_text(encoding="utf-8") == (
        "patched by a mod"
    )


def test_revert_says_the_folder_no_longer_matches_when_the_change_is_already_gone(
    fake_game_dir_runtime: Path,
    make_archive: MakeArchive,
) -> None:
    """revert_spt_update() fails in plain words when there is nothing left to take back.

    The securing of a changed game file is the youngest change, but a mod
    force-installed afterwards already wrote the earlier content back, so
    undoing it would change nothing at all. The message names no file, because
    none conflicts, and the game directory stays untouched.
    """
    path = fake_game_dir_runtime
    plugin = path / SPT_OWN_PLUGIN
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text("shipped with SPT", encoding="utf-8")
    write_server_exe(path, "SPT_Runtime", INSTALLED)
    write_client_exe(path)
    clean_repo_at(path)
    plugin.write_text("rewritten by an SPT update", encoding="utf-8")
    services.snapshot(path, "after the update")
    services.install_from_archive(
        path, make_archive({SPT_OWN_PLUGIN: "shipped with SPT"}, "zip"), force=True
    )

    with pytest.raises(GitError) as failure:
        services.revert_spt_update(path)

    _assert_plain_undo_failure(
        failure.value, path, "the game folder no longer matches that state"
    )
    assert failure.value.conflicting_paths == []
    assert release_subjects(path) == [
        "mod: spt-core",
        "snapshot: game files updated",
        "chore: clean SPT 4.1.0",
    ]
    assert plugin.read_text(encoding="utf-8") == "shipped with SPT"
