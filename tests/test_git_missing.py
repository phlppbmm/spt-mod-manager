"""Behaviour of sptmod on a machine where the git binary cannot be used.

Every entry point that needs git -- the ``status`` and ``init_game_dir``
services, the mod operations, the CLI and the RPC layer -- must answer with the
one plain-language ``GitMissingError`` that tells the user where to get Git,
instead of the raw ``[Errno 2] No such file or directory: git`` of the
underlying call. The tests replace ``PATH`` with a directory holding no (or an
unusable) git, which is the real process-environment boundary the detection
reads, and drive the probe through its public ``recheck`` seam.
"""

import contextlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import invoke_rpc
from fixture_types import InstallCommit
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import GitMissingError
from sptmod.core.profile import CheckResult
from sptmod.core.repo import Repo, ensure_git_available
from sptmod.core.services import RemoveResult, ToggleResult

runner = CliRunner()

INSTALL_HINT = (
    "sptmod needs Git to keep your mods safe, and it is not installed. "
    "Install it from https://git-scm.com/downloads "
    "(Windows: 'Git for Windows'; Linux: your package manager) and start sptmod again."
)


@pytest.fixture
def real_path() -> str:
    """The ``PATH`` of the test process, captured before a test replaces it."""
    return os.environ["PATH"]


@pytest.fixture
def bin_dir(tmp_path: Path, real_path: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory that is the whole ``PATH`` for the duration of a test.

    The teardown puts the real ``PATH`` back and probes once more, so the
    remembered answer of the process is the truthful one again for every test
    that follows.
    """
    directory = tmp_path / "fake-bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    try:
        yield directory
    finally:
        monkeypatch.setenv("PATH", real_path)
        ensure_git_available(recheck=True)


@pytest.fixture
def without_git(bin_dir: Path) -> Path:
    """An empty ``PATH`` directory with the process-wide probe already refreshed.

    Callers of the services and adapters do not re-probe; they read the
    remembered answer, so a test that wants them to see a missing git has to
    refresh it first -- exactly what the first sptmod call of a fresh process
    does.
    """
    with contextlib.suppress(GitMissingError):
        ensure_git_available(recheck=True)
    return bin_dir


@pytest.mark.parametrize(
    "git_binary",
    [None, b"#!/bin/sh\nexit 1\n", b"\xff\xfe not a program"],
    ids=["no-git-at-all", "git-answering-with-a-failure", "git-that-cannot-be-started"],
)
def test_an_unusable_git_installation_is_reported_with_the_install_hint(
    git_binary: bytes | None, bin_dir: Path
) -> None:
    """Puts one kind of unusable git on PATH and probes; the error is the hint.

    Whether git is absent, present but answering with a failure, or present but
    not startable at all, the user gets the same single sentence naming the
    download page -- none of the three is a state sptmod could work in.
    """
    if git_binary is not None:
        executable = bin_dir / "git"
        executable.write_bytes(git_binary)
        executable.chmod(0o755)

    with pytest.raises(GitMissingError) as excinfo:
        ensure_git_available(recheck=True)

    assert str(excinfo.value) == INSTALL_HINT


def test_the_probe_answer_is_remembered_until_a_recheck(
    without_git: Path, real_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restores PATH after a failed probe; only a recheck sees git return.

    The probe costs a process start, so it must run once per sptmod process and
    not once per git call. Asserting that the answer survives a PATH that would
    change it is the observable form of that.
    """
    with pytest.raises(GitMissingError):
        ensure_git_available()

    monkeypatch.setenv("PATH", real_path)

    with pytest.raises(GitMissingError):
        ensure_git_available()

    ensure_git_available(recheck=True)


def test_status_without_git_reports_the_install_hint(fake_game_dir: Path, without_git: Path) -> None:
    """status() on a game directory without git raises GitMissingError.

    status is the call the app makes first, so it is the place a user without
    git meets the message -- and it must be the hint, not a git error.
    """
    with pytest.raises(GitMissingError) as excinfo:
        services.status(fake_game_dir)

    assert str(excinfo.value) == INSTALL_HINT


def test_init_game_dir_without_git_reports_the_install_hint(fake_game_dir: Path, without_git: Path) -> None:
    """init_game_dir() on a game directory without git raises GitMissingError.

    Setting up a folder is the first thing a new user does; the failure has to
    name the missing program instead of the failed ``git init``.
    """
    with pytest.raises(GitMissingError) as excinfo:
        services.init_game_dir(fake_game_dir)

    assert str(excinfo.value) == INSTALL_HINT


@pytest.fixture
def repo_with_alpha(clean_repo: Repo, install_commit: InstallCommit) -> Repo:
    """A repository whose history holds one installed mod ``Alpha``."""
    install_commit(clean_repo, "Alpha")
    return clean_repo


MOD_OPERATIONS: list[Callable[[Path], ToggleResult | RemoveResult | CheckResult]] = [
    lambda path: services.disable_mod(path, "Alpha"),
    lambda path: services.enable_mod(path, "Alpha"),
    lambda path: services.remove_mod(path, "Alpha"),
    services.check_profiles,
]


@pytest.mark.parametrize(
    "operation",
    MOD_OPERATIONS,
    ids=["disable_mod", "enable_mod", "remove_mod", "check_profiles"],
)
def test_a_mod_operation_without_git_reports_the_install_hint_unrewritten(
    operation: Callable[[Path], ToggleResult | RemoveResult | CheckResult],
    repo_with_alpha: Repo,
    without_git: Path,
) -> None:
    """Runs a mod operation on an installed mod without git; the hint survives.

    These operations wrap failing git calls into their own messages ("the mod
    cannot be disabled", "the profiles cannot be read"). A missing git must
    reach the user unrewritten, because those messages would drop the only
    information that helps.
    """
    with pytest.raises(GitMissingError) as excinfo:
        operation(repo_with_alpha.path)

    assert str(excinfo.value) == INSTALL_HINT


def test_the_cli_exits_with_seventeen_and_prints_the_hint(fake_game_dir: Path, without_git: Path) -> None:
    """``sptmod init`` without git exits 17 and writes the hint to stderr.

    Scripts distinguish failures by exit status, and a missing git is its own
    cause, so it gets its own code next to the other typed errors.
    """
    result = runner.invoke(app, ["init", str(fake_game_dir)])

    assert result.exit_code == 17
    assert result.stderr.startswith("error: sptmod needs Git")
    assert "https://git-scm.com/downloads" in result.stderr


def test_the_rpc_layer_answers_with_the_git_missing_code(fake_game_dir: Path, without_git: Path) -> None:
    """``rpc status`` without git returns code GIT_MISSING and the hint.

    The desktop app switches on the code, so the missing binary must not share
    GIT_ERROR with a git call that merely failed.
    """
    exit_code, lines = invoke_rpc("status", {"path": str(fake_game_dir)})

    assert exit_code == 1
    assert lines[0]["data"]["code"] == "GIT_MISSING"
    assert lines[0]["data"]["error"] == INSTALL_HINT
