"""Tests for Repo.init: repository creation, whitelist .gitignore, local identity."""

import subprocess
from pathlib import Path

import pytest

from sptmod.core.errors import GitError
from sptmod.core.repo import COMMITTER_EMAIL, COMMITTER_NAME, GITIGNORE_WHITELIST, Repo


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_init_returns_repo_bound_to_the_given_path(fake_game_dir: Path) -> None:
    repo = Repo.init(fake_game_dir)

    assert repo.path == fake_game_dir


def test_init_creates_a_git_repository(fake_game_dir: Path) -> None:
    Repo.init(fake_game_dir)

    assert (fake_game_dir / ".git").is_dir()


def test_init_writes_the_whitelist_gitignore_verbatim(fake_game_dir: Path) -> None:
    Repo.init(fake_game_dir)

    gitignore = fake_game_dir / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == GITIGNORE_WHITELIST


def test_init_does_not_create_an_initial_commit(fake_game_dir: Path) -> None:
    Repo.init(fake_game_dir)

    # The repository must exist (otherwise the assertion below is meaningless)
    # but have zero commits, i.e. no valid HEAD to log.
    assert (fake_game_dir / ".git").is_dir()
    result = subprocess.run(
        ["git", "-C", str(fake_game_dir), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "does not have any commits" in result.stderr.lower()


def test_init_sets_local_committer_identity_not_global(fake_game_dir: Path) -> None:
    Repo.init(fake_game_dir)

    name = _git(fake_game_dir, "config", "--local", "--get", "user.name")
    email = _git(fake_game_dir, "config", "--local", "--get", "user.email")

    assert name == COMMITTER_NAME
    assert email == COMMITTER_EMAIL


def test_init_identity_works_without_any_global_git_config(fake_game_dir: Path) -> None:
    # The autouse isolate_git_config fixture already points HOME and
    # GIT_CONFIG_GLOBAL at nonexistent files. If Repo.init relied on the
    # global config being present it would fail here.
    repo = Repo.init(fake_game_dir)

    (fake_game_dir / "SPT" / "user" / "profiles" / "abc.json").write_text(
        '{"changed": true}', encoding="utf-8"
    )
    commit_hash = repo.commit(
        "mod: identity smoke test", paths=[Path("SPT/user/profiles/abc.json")]
    )

    assert commit_hash != ""


def test_init_twice_raises_git_error(fake_game_dir: Path) -> None:
    Repo.init(fake_game_dir)

    with pytest.raises(GitError):
        Repo.init(fake_game_dir)
