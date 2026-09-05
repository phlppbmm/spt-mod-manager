"""Tests for Repo.status, Repo.is_clean and Repo.untracked_under general behavior.

Whitelist-specific edge cases (ignored dummies, trackable-prefix boundaries)
live in test_repo_whitelist.py; this module covers the modified/untracked/clean
lifecycle on top of an existing commit.
"""

import subprocess
from pathlib import Path

from sptmod.core.models import RepoStatus
from sptmod.core.repo import Repo

TRACKED_FILE = Path("SPT/user/mods/TestMod/mod.dll")


def test_is_clean_true_right_after_a_commit(committed_repo: tuple[Repo, str]) -> None:
    """is_clean() returns True when no modifications exist (baseline: clean state after commit)."""
    repo, _ = committed_repo

    assert repo.is_clean() is True


def test_status_is_empty_right_after_a_commit(committed_repo: tuple[Repo, str]) -> None:
    """Repo.status() reports no modified or untracked files immediately after commit."""
    repo, _ = committed_repo

    assert repo.status() == RepoStatus()


def test_status_reports_a_modified_tracked_file(
    committed_repo: tuple[Repo, str],
) -> None:
    """Repo.status() lists committed files that have been modified."""
    repo, _ = committed_repo
    (repo.path / TRACKED_FILE).write_bytes(b"binary-mod-content-v2")

    status = repo.status()

    assert status.modified == [TRACKED_FILE]
    assert status.untracked == []


def test_is_clean_false_when_a_tracked_file_is_modified(
    committed_repo: tuple[Repo, str],
) -> None:
    """Repo.is_clean() returns False when any committed file is modified."""
    repo, _ = committed_repo
    (repo.path / TRACKED_FILE).write_bytes(b"binary-mod-content-v2")

    assert repo.is_clean() is False


def test_status_reports_an_untracked_file_under_a_trackable_prefix(
    committed_repo: tuple[Repo, str],
) -> None:
    """Repo.status() detects new files in whitelisted directories."""
    repo, _ = committed_repo
    other = repo.path / "SPT" / "user" / "mods" / "OtherMod" / "other.dll"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other-content")

    status = repo.status()

    assert status.untracked == [Path("SPT/user/mods/OtherMod/other.dll")]
    assert status.modified == []


def test_is_clean_false_when_an_untracked_whitelisted_file_exists(
    committed_repo: tuple[Repo, str],
) -> None:
    """Repo.is_clean() returns False when new files exist in whitelisted dirs."""
    repo, _ = committed_repo
    other = repo.path / "BepInEx" / "config" / "new.cfg"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("data", encoding="utf-8")

    assert repo.is_clean() is False


def test_untracked_under_scopes_to_the_given_prefix_only(
    committed_repo: tuple[Repo, str],
) -> None:
    """Repo.untracked_under() lists only untracked files below the given directory."""
    repo, _ = committed_repo
    mods_file = repo.path / "SPT" / "user" / "mods" / "OtherMod" / "other.dll"
    mods_file.parent.mkdir(parents=True)
    mods_file.write_bytes(b"other-content")
    config_file = repo.path / "BepInEx" / "config" / "new.cfg"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("data", encoding="utf-8")

    under_mods = repo.untracked_under(Path("SPT/user/mods"))
    under_bepinex = repo.untracked_under(Path("BepInEx"))

    assert under_mods == [Path("SPT/user/mods/OtherMod/other.dll")]
    assert under_bepinex == [Path("BepInEx/config/new.cfg")]


def test_status_and_is_clean_see_a_staged_but_uncommitted_change(
    committed_repo: tuple[Repo, str],
) -> None:
    """Repo.status() and is_clean() report staged changes as uncommitted."""
    repo, _ = committed_repo
    (repo.path / TRACKED_FILE).write_bytes(b"binary-mod-content-staged")
    # Stage the change directly (bypassing Repo.commit) so it sits in the
    # index with porcelain code "M " (index column), not "  " or "??".
    subprocess.run(["git", "-C", str(repo.path), "add", str(TRACKED_FILE)], check=True)

    status = repo.status()

    assert TRACKED_FILE in status.modified
    assert repo.is_clean() is False


def test_untracked_under_returns_empty_list_when_nothing_new(
    committed_repo: tuple[Repo, str],
) -> None:
    """untracked_under() returns empty [] when prefix has no new files (baseline: no false positives)."""
    repo, _ = committed_repo

    assert repo.untracked_under(Path("SPT/user/mods")) == []
