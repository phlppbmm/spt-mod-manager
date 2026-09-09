"""Tests for the SPT version chain (spt-mod-manager-fyx).

Covers ``read_core_json_version``, ``Repo.config_get``/``config_set``,
``store_spt_version``/``read_stored_version``, the ``resolve_spt_version``
fallback order (core.json -> PE -> stored -> None), the ``set_spt_version``
service with its rpc adapter, and the ``sptmod init --spt-version`` CLI pin.
Split out of :mod:`test_game_layout`, which keeps ``detect_layout`` and the
game-directory accessors.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pe_builder import write_server_exe
from sptmod.cli import app as cli_app
from sptmod.core import services
from sptmod.core.errors import GitError, NotAGameDirError
from sptmod.core.game_dir import (
    SPT_VERSION_CONFIG_KEY,
    GameLayout,
    read_core_json_version,
    read_stored_version,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.repo import Repo

runner = CliRunner()


# ============================================================================
# read_core_json_version
# ============================================================================


def _layout(tmp_path: Path, runtime_dir_name: str = "SPT_Runtime") -> GameLayout:
    """Create a GameLayout with a minimal core.json directory structure.

    Used by core.json version-reading tests; creates the directory hierarchy
    and returns a GameLayout instance pointing to it.
    """
    (tmp_path / "game" / runtime_dir_name / "SPT_Data" / "configs").mkdir(parents=True, exist_ok=True)
    return GameLayout(root=tmp_path / "game", runtime_dir_name=runtime_dir_name)


def _write_core_json(layout: GameLayout, content: str) -> None:
    """Write a JSON string to core.json at the layout's expected location."""
    (layout.root / layout.core_config_file).write_text(content, encoding="utf-8")


def test_read_core_json_version_returns_the_field_when_present(
    tmp_path: Path,
) -> None:
    """Extract sptVersion from well-formed core.json."""
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "4.1.0"}))

    assert read_core_json_version(layout) == "4.1.0"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(json.dumps({"projectName": "SPT", "compatibleTarkovVersion": "1.0"}), id="key-absent"),
        pytest.param(None, id="file-missing"),
        pytest.param("{not valid json", id="malformed-json"),
        pytest.param(json.dumps({"sptVersion": "   "}), id="whitespace-only-value"),
        pytest.param(json.dumps([1, 2, 3]), id="non-object-root"),
    ],
)
def test_read_core_json_version_none_for_a_core_json_that_names_no_version(
    tmp_path: Path, content: str | None
) -> None:
    """read_core_json_version returns None for every core.json naming no version.

    The file holds a 4.x config without the key, is absent (``content`` None),
    holds an unclosed brace, holds a whitespace-only version or a JSON array
    instead of an object. None of these names a version, and the reader treats
    the file as a hint only, so each returns None instead of raising.
    """
    layout = _layout(tmp_path)
    if content is not None:
        _write_core_json(layout, content)

    assert read_core_json_version(layout) is None


# ============================================================================
# Repo.config_get / config_set
# ============================================================================


def test_config_get_returns_none_for_an_unset_key(initialized_repo: Repo) -> None:
    """Calling config_get() on a non-existent key returns None.

    Queries a key that was never set; must return None.
    """
    assert initialized_repo.config_get("sptmod.unset-key") is None


def test_config_set_then_get_roundtrips_the_value(initialized_repo: Repo) -> None:
    """config_set() followed by config_get() returns the set value.

    Sets a key to "hello-value" and immediately gets it back; must match.
    """
    initialized_repo.config_set("sptmod.testkey", "hello-value")

    assert initialized_repo.config_get("sptmod.testkey") == "hello-value"


def test_config_set_overwrites_an_existing_value(initialized_repo: Repo) -> None:
    """Calling config_set() twice overwrites the first value.

    Sets the key to "first", then immediately sets it to "second"; the final
    get must return "second".
    """
    initialized_repo.config_set("sptmod.testkey", "first")
    initialized_repo.config_set("sptmod.testkey", "second")

    assert initialized_repo.config_get("sptmod.testkey") == "second"


def test_config_get_raises_git_error_for_a_reason_other_than_an_unset_key(
    initialized_repo: Repo,
) -> None:
    """A corrupted ``.git/config`` makes git refuse to read *any* key, which
    is a different failure than the plain "key unset" (returncode 1) case
    every other config_get test exercises."""
    config_file = initialized_repo.path / ".git" / "config"
    with config_file.open("a", encoding="utf-8") as handle:
        handle.write("not a valid config line at all\n")

    with pytest.raises(GitError):
        initialized_repo.config_get("sptmod.testkey")


# ============================================================================
# store_spt_version / read_stored_version
# ============================================================================


def test_read_stored_version_none_when_nothing_was_stored(
    initialized_repo: Repo,
) -> None:
    """read_stored_version() returns None when store_spt_version() was never called.

    On a fresh repo, the SPT_VERSION_CONFIG_KEY has not been set.
    """
    assert read_stored_version(initialized_repo) is None


def test_store_then_read_stored_version_roundtrips(initialized_repo: Repo) -> None:
    """Store and read operations preserve the version string in git config."""
    store_spt_version(initialized_repo, "4.1.2")

    assert read_stored_version(initialized_repo) == "4.1.2"
    assert initialized_repo.config_get(SPT_VERSION_CONFIG_KEY) == "4.1.2"


# ============================================================================
# resolve_spt_version chain: core.json -> PE -> stored -> None
# ============================================================================


def _build_layout_and_repo(
    tmp_path: Path,
    *,
    core_version: str | None = None,
    exe_version: str | None = None,
) -> tuple[GameLayout, Repo]:
    """Create a GameLayout and initialized Repo with optional version sources.

    Used by the resolve_spt_version chain tests; sets up core.json and/or
    a synthetic server EXE, and initializes git.
    """
    layout = _layout(tmp_path)
    core_config: dict[str, str] = {"projectName": "SPT"}
    if core_version is not None:
        core_config["sptVersion"] = core_version
    _write_core_json(layout, json.dumps(core_config))
    if exe_version is not None:
        write_server_exe(layout.root, layout.runtime_dir_name, exe_version)
    repo = Repo.init(layout)
    return layout, repo


@pytest.mark.parametrize(
    ("core_version", "exe_version", "stored", "expected"),
    [
        ("9.9.9", "1.1.1", "2.2.2", "9.9.9"),
        (None, "1.1.1", "2.2.2", "1.1.1"),
        (None, None, "2.2.2", "2.2.2"),
        (None, None, None, None),
    ],
    ids=[
        "prefers-core-json",
        "falls-back-to-pe",
        "falls-back-to-stored",
        "none-when-chain-empty",
    ],
)
def test_resolve_spt_version_walks_the_fallback_chain(
    tmp_path: Path,
    core_version: str | None,
    exe_version: str | None,
    stored: str | None,
    expected: str | None,
) -> None:
    """Fallback chain: core.json → PE → git config → None."""
    layout, repo = _build_layout_and_repo(tmp_path, core_version=core_version, exe_version=exe_version)
    if stored is not None:
        store_spt_version(repo, stored)

    assert resolve_spt_version(layout, repo) == expected


# ============================================================================
# set_spt_version (service)
# ============================================================================


def test_set_spt_version_stores_and_returns_the_stripped_value(
    initialized_repo: Repo,
) -> None:
    """set_spt_version() trims whitespace and stores the result.

    Calls with "  4.1.2  "; must return "4.1.2" and store it trimmed.
    """
    result = services.set_spt_version(initialized_repo.path, "  4.1.2  ")

    assert result == "4.1.2"
    assert read_stored_version(initialized_repo) == "4.1.2"


def test_set_spt_version_raises_value_error_for_an_empty_version(
    initialized_repo: Repo,
) -> None:
    """set_spt_version() raises ValueError when passed an empty string.

    The error message must contain "empty".
    """
    with pytest.raises(ValueError, match="empty"):
        services.set_spt_version(initialized_repo.path, "")


def test_set_spt_version_raises_not_a_game_dir_error_for_a_non_game_dir(
    tmp_path: Path,
) -> None:
    """set_spt_version() raises NotAGameDirError for a non-game directory.

    Calls on an empty directory; must raise NotAGameDirError.
    """
    empty = tmp_path / "not-a-game-dir"
    empty.mkdir()

    with pytest.raises(NotAGameDirError):
        services.set_spt_version(empty, "4.1.2")


def test_set_spt_version_raises_git_error_when_not_yet_a_repository(
    fake_game_dir: Path,
) -> None:
    """set_spt_version() raises GitError when git is not yet initialized.

    Calls on a valid game dir with no .git; must raise GitError.
    """
    with pytest.raises(GitError):
        services.set_spt_version(fake_game_dir, "4.1.2")


# ============================================================================
# set_spt_version over the rpc adapter
# ============================================================================


def test_rpc_set_spt_version_stores_and_returns_the_stripped_value(
    fake_game_dir: Path,
) -> None:
    """RPC endpoint for set_spt_version returns result event with trimmed version."""
    services.init_game_dir(fake_game_dir)

    result = runner.invoke(
        cli_app,
        ["rpc", "set_spt_version"],
        input=json.dumps({"path": str(fake_game_dir), "version": "  4.1.9  "}),
    )

    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert result.exit_code == 0
    assert lines == [{"event": "result", "data": {"spt_version": "4.1.9"}}]
    assert read_stored_version(Repo(fake_game_dir)) == "4.1.9"


# ============================================================================
# CLI contract pin: `sptmod init --spt-version` / interactive prompt
# ============================================================================


@pytest.mark.parametrize(
    ("extra_args", "stdin_input"),
    [
        pytest.param(["--spt-version", "4.1.2"], None, id="spt-version-option"),
        pytest.param([], "4.1.2\n", id="interactive-prompt"),
    ],
)
def test_cli_init_stores_the_spt_version_it_was_given(
    fake_game_dir_runtime: Path, extra_args: list[str], stdin_input: str | None
) -> None:
    """CLI init stores the version from the option and from the interactive answer alike.

    ``sptmod init --spt-version 4.1.2`` skips the prompt; ``sptmod init`` without
    the option asks and is answered "4.1.2". Both exit 0, print "initialized SPT
    4.1.2" and leave that version stored in the new repository.
    """
    result = runner.invoke(
        cli_app,
        ["init", str(fake_game_dir_runtime), *extra_args],
        input=stdin_input,
    )

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout
    assert read_stored_version(Repo(fake_game_dir_runtime)) == "4.1.2"


def test_cli_init_reports_a_warning_when_both_runtime_dirs_are_present(
    fake_game_dir_runtime: Path,
) -> None:
    """CLI init prints a warning when both SPT_Runtime and SPT directories exist.

    Creates both runtime dirs and runs init; must exit 0 and print both names
    to stdout (the warning from detect_layout).
    """
    (fake_game_dir_runtime / "SPT").mkdir()
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = runner.invoke(cli_app, ["init", str(fake_game_dir_runtime)])

    assert result.exit_code == 0
    assert "SPT_Runtime" in result.stdout
    assert "SPT" in result.stdout
