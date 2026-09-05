"""Tests for Repo.init: repository creation, whitelist .gitignore, local identity.

CONTRACT CHANGE (spt-mod-manager-fyx): ``Repo.init`` now takes the detected
``GameLayout`` instead of a bare ``Path`` (so it can generate the runtime-dir-
correct whitelist), and the old fixed ``GITIGNORE_WHITELIST`` constant was
replaced by ``gitignore_whitelist(runtime_dir_name)``. Updated accordingly;
the ``SPT_Runtime`` counterpart of the whitelist-generation behavior lives in
tests/test_game_layout.py.
"""

import subprocess
from pathlib import Path

import pytest

from sptmod.core.errors import GitError, NotAGameDirError
from sptmod.core.game_dir import GameLayout, detect_layout
from sptmod.core.repo import COMMITTER_EMAIL, COMMITTER_NAME, Repo, gitignore_whitelist


def _git(path: Path, *args: str) -> str:
    """Execute git command and return stdout stripped of whitespace.

    Runs git -C <path> <args> as subprocess and returns result.stdout.strip().
    Raises CalledProcessError on non-zero exit (check=True).
    """
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_init_returns_repo_bound_to_the_given_path(fake_game_dir: Path) -> None:
    """Repo.init returns Repo with path attribute bound to game directory.

    Calls Repo.init(detect_layout(fake_game_dir)). Returned repo.path must
    equal fake_game_dir exactly.
    """
    repo = Repo.init(detect_layout(fake_game_dir))

    assert repo.path == fake_game_dir


def test_init_creates_a_git_repository(fake_game_dir: Path) -> None:
    """Repo.init creates .git directory in the game directory.

    Calls Repo.init(detect_layout(fake_game_dir)). Must create .git/ as directory.
    """
    Repo.init(detect_layout(fake_game_dir))

    assert (fake_game_dir / ".git").is_dir()


def test_init_writes_the_whitelist_gitignore_verbatim(fake_game_dir: Path) -> None:
    """Repo.init writes .gitignore with runtime-appropriate whitelist exactly.

    Calls Repo.init(detect_layout(fake_game_dir)). Must write .gitignore
    matching gitignore_whitelist("SPT") exactly (for SPT runtime dir).
    """
    Repo.init(detect_layout(fake_game_dir))

    gitignore = fake_game_dir / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == gitignore_whitelist("SPT")


def test_init_does_not_create_an_initial_commit(fake_game_dir: Path) -> None:
    """Repo.init initializes git repo with zero commits (no HEAD).

    Calls Repo.init(detect_layout(fake_game_dir)). Running `git log` must fail
    with exit code != 0 and stderr containing "does not have any commits".
    """
    Repo.init(detect_layout(fake_game_dir))

    # The repository must exist (otherwise the assertion below is meaningless)
    # but have zero commits, i.e. no valid HEAD to log.
    assert (fake_game_dir / ".git").is_dir()
    result = subprocess.run(
        ["git", "-C", str(fake_game_dir), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "does not have any commits" in result.stderr.lower()


def test_init_sets_local_committer_identity_not_global(fake_game_dir: Path) -> None:
    """Repo.init() configures local git identity for the repo."""
    Repo.init(detect_layout(fake_game_dir))

    name = _git(fake_game_dir, "config", "--local", "--get", "user.name")
    email = _git(fake_game_dir, "config", "--local", "--get", "user.email")

    assert name == COMMITTER_NAME
    assert email == COMMITTER_EMAIL


def test_init_identity_works_without_any_global_git_config(fake_game_dir: Path) -> None:
    """Repo.init configures local identity enabling commits without global config.

    Fixture isolate_git_config points HOME and GIT_CONFIG_GLOBAL to nonexistent
    paths (no global git config). Creates a profile json, calls repo.commit(),
    and verifies commit_hash is non-empty, proving local identity was set.
    """
    # The autouse isolate_git_config fixture already points HOME and
    # GIT_CONFIG_GLOBAL at nonexistent files. If Repo.init relied on the
    # global config being present it would fail here.
    repo = Repo.init(detect_layout(fake_game_dir))

    (fake_game_dir / "SPT" / "user" / "profiles" / "abc.json").write_text('{"changed": true}', encoding="utf-8")
    commit_hash = repo.commit("mod: identity smoke test", paths=[Path("SPT/user/profiles/abc.json")])

    assert commit_hash != ""


def test_init_twice_raises_git_error(fake_game_dir: Path) -> None:
    """Repo.init raises GitError when called twice on same path.

    Calls Repo.init(detect_layout(fake_game_dir)) twice. Second call must
    raise GitError (repository already exists).
    """
    layout = detect_layout(fake_game_dir)
    Repo.init(layout)

    with pytest.raises(GitError):
        Repo.init(layout)


def test_init_raises_not_a_game_dir_error_when_layout_root_is_not_a_directory(
    tmp_path: Path,
) -> None:
    """Repo.init raises NotAGameDirError when layout root is a file, not directory.

    Creates not_a_dir as a regular file (write_text). Passes GameLayout(
    root=not_a_dir, runtime_dir_name="SPT") to Repo.init(). Must raise
    NotAGameDirError.
    """
    not_a_dir = tmp_path / "not-a-directory"
    not_a_dir.write_text("x", encoding="utf-8")
    layout = GameLayout(root=not_a_dir, runtime_dir_name="SPT")

    with pytest.raises(NotAGameDirError):
        Repo.init(layout)


def test_init_raises_not_a_game_dir_error_when_layout_root_is_missing(
    tmp_path: Path,
) -> None:
    """Repo.init raises NotAGameDirError when layout root does not exist.

    Passes GameLayout(root=tmp_path/"does-not-exist", runtime_dir_name="SPT")
    to Repo.init(). Must raise NotAGameDirError.
    """
    layout = GameLayout(root=tmp_path / "does-not-exist", runtime_dir_name="SPT")

    with pytest.raises(NotAGameDirError):
        Repo.init(layout)


def test_configure_called_again_does_not_overwrite_an_existing_gitignore(
    fake_game_dir: Path,
) -> None:
    """Resuming a half-finished init must not clobber a manually edited
    ``.gitignore`` (or one this same init already wrote)."""
    layout = detect_layout(fake_game_dir)
    repo = Repo.init(layout)
    sentinel = "# sentinel: hand-edited whitelist, must survive\n"
    (repo.path / ".gitignore").write_text(sentinel, encoding="utf-8")

    repo.configure(layout)

    assert (repo.path / ".gitignore").read_text(encoding="utf-8") == sentinel
    assert _git(repo.path, "config", "--local", "--get", "user.name") == COMMITTER_NAME
