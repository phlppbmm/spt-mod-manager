"""Tests for ``sptmod.core.services.remove_mod``.

Covers a clean revert (files gone, ``Mod-State: removed`` trailer), a revert
conflict caused by a later mod overwriting the same file (aborted, tree
clean, later mod named in the error), unknown/non-active mods, ambiguous
case-variant names, a dirty tree, and a running game.
"""

import threading
import time
from pathlib import Path

import pytest

from fixture_types import InstallCommit
from sptmod.core import game_dir, services, workspace
from sptmod.core.errors import (
    AmbiguousModError,
    ConflictError,
    DirtyTreeError,
    GameRunningError,
    ModNotFoundError,
)
from sptmod.core.repo import Repo
from sptmod.core.workspace import game_lock


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock the game process detector to prevent false failures during remove tests."""
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def test_remove_mod_deletes_the_files_added_by_its_install_commit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod deletes the files that were added by the mod's install commit."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})

    services.remove_mod(clean_repo.path, "ModA")

    assert not (clean_repo.path / "BepInEx/plugins/ModA.dll").exists()


def test_remove_mod_result_reports_name_commit_and_files(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod result includes the mod name, commit hash, and removed files."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})

    result = services.remove_mod(clean_repo.path, "ModA")

    assert result.name == "ModA"
    assert set(result.files) == {Path("BepInEx/plugins/ModA.dll")}
    committed_hashes = {commit.hash for commit in clean_repo.log()}
    assert result.commit in committed_hashes


def test_remove_mod_revert_commit_carries_the_removed_state_trailer(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """The revert commit includes Mod and Mod-State trailers marking the mod removed."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})

    result = services.remove_mod(clean_repo.path, "ModA")

    revert_commit = next(c for c in clean_repo.log() if c.hash == result.commit)
    assert revert_commit.trailers.get("Mod") == "ModA"
    assert revert_commit.trailers.get("Mod-State") == "removed"


def test_remove_mod_conflict_names_the_later_mod_and_leaves_tree_clean(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """When a file was overwritten by a later mod, the error names that mod and leaves tree clean."""
    install_commit(
        clean_repo, "ModA", files={"BepInEx/plugins/shared.dll": "base"}, minute=1
    )
    install_commit(
        clean_repo,
        "ModB",
        files={"BepInEx/plugins/shared.dll": "overwritten-by-modb"},
        minute=2,
    )

    with pytest.raises(ConflictError) as exc_info:
        services.remove_mod(clean_repo.path, "ModA")

    assert "ModB" in str(exc_info.value)
    assert clean_repo.is_clean() is True
    assert (clean_repo.path / "BepInEx/plugins/shared.dll").read_text(
        encoding="utf-8"
    ) == "overwritten-by-modb"


def test_remove_mod_conflict_message_names_both_overwritten_files(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """The conflict error message lists all overwritten files."""
    install_commit(
        clean_repo,
        "ModA",
        files={
            "BepInEx/plugins/shared1.dll": "base1",
            "BepInEx/plugins/shared2.dll": "base2",
        },
        minute=1,
    )
    install_commit(
        clean_repo,
        "ModB",
        files={
            "BepInEx/plugins/shared1.dll": "overwritten-by-modb-1",
            "BepInEx/plugins/shared2.dll": "overwritten-by-modb-2",
        },
        minute=2,
    )

    with pytest.raises(ConflictError) as exc_info:
        services.remove_mod(clean_repo.path, "ModA")

    message = str(exc_info.value)
    assert "ModB" in message
    assert "BepInEx/plugins/shared1.dll" in message
    assert "BepInEx/plugins/shared2.dll" in message


def test_remove_mod_raises_mod_not_found_for_unknown_name(clean_repo: Repo) -> None:
    """remove_mod raises ModNotFoundError for a non-existent mod."""
    with pytest.raises(ModNotFoundError):
        services.remove_mod(clean_repo.path, "Ghost")


def test_remove_mod_raises_mod_not_found_for_an_already_removed_mod(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Removing a mod twice raises ModNotFoundError on the second attempt."""
    install_commit(clean_repo, "ModA")
    services.remove_mod(clean_repo.path, "ModA")

    with pytest.raises(ModNotFoundError):
        services.remove_mod(clean_repo.path, "ModA")


def test_remove_mod_raises_ambiguous_mod_error_for_case_variant_names(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod raises AmbiguousModError when multiple case variants exist."""
    install_commit(clean_repo, "CoolMod", minute=1)
    install_commit(clean_repo, "coolmod", minute=2)

    with pytest.raises(AmbiguousModError) as exc_info:
        services.remove_mod(clean_repo.path, "COOLMOD")

    assert set(exc_info.value.candidates) == {"CoolMod", "coolmod"}


def test_remove_mod_rejects_a_dirty_tree(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod raises DirtyTreeError when there are untracked changes."""
    install_commit(clean_repo, "ModA")
    (clean_repo.path / "BepInEx/plugins/stray.txt").write_text(
        "stray", encoding="utf-8"
    )

    with pytest.raises(DirtyTreeError):
        services.remove_mod(clean_repo.path, "ModA")


def test_remove_mod_rejects_a_running_game(
    clean_repo: Repo, install_commit: InstallCommit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """remove_mod raises GameRunningError when the game is running."""
    install_commit(clean_repo, "ModA")
    monkeypatch.setattr(
        game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"]
    )

    with pytest.raises(GameRunningError):
        services.remove_mod(clean_repo.path, "ModA")

    assert (clean_repo.path / "BepInEx/plugins/ModA.dll").exists()


def test_remove_mod_waits_for_a_lock_held_by_another_operation_then_succeeds(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod blocks until the game lock is released, then succeeds."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})
    release_after = 0.3
    holder_ready = threading.Event()

    def hold() -> None:
        with game_lock(clean_repo.path, "installing OtherMod"):
            holder_ready.set()
            time.sleep(release_after)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_ready.wait(timeout=1.0)

    started = time.monotonic()
    result = services.remove_mod(clean_repo.path, "ModA")
    elapsed = time.monotonic() - started
    holder.join(timeout=1.0)

    assert result.name == "ModA"
    assert not (clean_repo.path / "BepInEx/plugins/ModA.dll").exists()
    assert elapsed >= release_after * 0.5
    assert workspace.is_locked(clean_repo.path) is False
