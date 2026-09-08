"""CLI smoke tests for sptmod: exit codes, stderr messages, happy-path output."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import clean_repo_at
from fixture_types import InstallCommit, MakeArchive
from sptmod.cli import app
from sptmod.core import game_dir
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")

runner = CliRunner()


def test_init_happy_path_exits_zero_and_reports_the_spt_version(
    fake_game_dir: Path,
) -> None:
    """The init command exits 0 and outputs the detected SPT version to stdout.

    Calls ``sptmod init <path>`` on a valid game directory with core.json
    present; must exit with code 0 and print "4.1.0" to stdout (detected from
    the layout's PE or core.json, never from user input).
    """
    result = runner.invoke(app, ["init", str(fake_game_dir)])

    assert result.exit_code == 0
    assert "4.1.0" in result.stdout


def test_init_on_a_non_game_dir_exits_with_code_3_and_reports_on_stderr(
    tmp_path: Path,
) -> None:
    """The init command exits 3 for non-game directories with stderr output.

    Invokes ``sptmod init`` on an empty directory lacking SPT and BepInEx
    subdirectories; must exit with code 3 (mapped NotAGameDirError) and write
    a message to stderr starting with "error:".
    """
    not_a_game_dir = tmp_path / "not-a-game"
    not_a_game_dir.mkdir()

    result = runner.invoke(app, ["init", str(not_a_game_dir)])

    assert result.exit_code == 3
    assert result.stderr.startswith("error:")


def test_install_file_without_a_resolvable_version_prints_a_hint(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """A bare-DLL client install with no version override has no version to
    read (``meta_from_dll`` leaves it empty); the CLI must call this out and
    point at ``--version`` rather than silently committing an empty version.
    """
    clean_repo_at(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "--file", str(archive)])

    assert result.exit_code == 0
    assert "CoolPlugin" in result.stdout
    assert "--version" in result.stdout


def test_install_file_with_a_version_override_prints_no_versionless_hint(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """Supplying --version suppresses the stdout hint to use --version.

    Installs a bare DLL with ``--file --version 1.0.0``; the CLI must not
    print the ``--version`` hint that would normally guide a user to resolve
    the missing version for a bare DLL.
    """
    clean_repo_at(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "--file", str(archive), "--version", "1.0.0"])

    assert result.exit_code == 0
    assert "--version" not in result.stdout


def test_list_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The list command exits 0 when the repository has one mod installed.

    Creates a repo with a single installed mod and runs ``sptmod list``;
    must exit with code 0.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0


def test_files_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The files command exits 0 when querying an installed mod by name.

    Creates a repo with one installed mod named "CoolMod" and runs
    ``sptmod files CoolMod``; must exit with code 0.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["files", "CoolMod"])

    assert result.exit_code == 0


def test_history_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The history command exits 0 when the repository has one install commit.

    Creates a repo with a mod install commit and runs ``sptmod history``;
    must exit with code 0.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0


def test_remove_happy_path_exits_zero_and_reports_the_removed_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The remove command exits 0 and prints the mod name to stdout.

    Removes an installed mod with ``--yes``; must exit with code 0 and print
    the mod name "CoolMod" to stdout.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "CoolMod", "--yes"])

    assert result.exit_code == 0
    assert "CoolMod" in result.stdout


@pytest.mark.parametrize(
    "extra_args",
    [[], ["--force"]],
    # ``--force`` only bypasses the profile-residue check inside
    # ``remove_mod``; it must not also imply ``--yes``.
    ids=["plain", "with-force"],
)
def test_remove_without_yes_prompts_and_declining_removes_nothing(
    extra_args: list[str],
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_commit: InstallCommit,
) -> None:
    """Declining the confirmation prompt leaves the mod files in place.

    Runs ``sptmod remove <name>`` without ``--yes`` and answers "n" to the
    confirmation; the mod's DLL must still exist and exit code must be
    non-zero.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    dll = fake_game_dir / "BepInEx/plugins/CoolMod.dll"

    result = runner.invoke(app, ["remove", "CoolMod", *extra_args], input="n\n")

    assert result.exit_code != 0
    assert dll.exists()


def test_remove_without_yes_prompts_and_confirming_removes_the_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """Confirming the prompt removes the mod and exits 0.

    Runs ``sptmod remove <name>`` without ``--yes`` and answers "y"; must exit
    with code 0, print the mod name, and the DLL must no longer exist.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    dll = fake_game_dir / "BepInEx/plugins/CoolMod.dll"

    result = runner.invoke(app, ["remove", "CoolMod"], input="y\n")

    assert result.exit_code == 0
    assert "CoolMod" in result.stdout
    assert not dll.exists()


def test_remove_with_yes_flag_skips_the_confirmation_prompt(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The --yes flag bypasses the confirmation prompt and removes the mod.

    Runs ``sptmod remove <name> --yes``; must exit with code 0, print the mod
    name, and the DLL must no longer exist (no interactive prompt).
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    dll = fake_game_dir / "BepInEx/plugins/CoolMod.dll"

    result = runner.invoke(app, ["remove", "CoolMod", "--yes"])

    assert result.exit_code == 0
    assert "CoolMod" in result.stdout
    assert not dll.exists()


def test_remove_unknown_mod_exits_with_code_11_and_reports_on_stderr(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing a non-existent mod exits 11 with stderr output.

    Runs ``sptmod remove Ghost`` (name not in any install commit); must exit
    with code 11 (mapped ModNotFoundError) and write a message to stderr
    starting with "error:".
    """
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "Ghost", "--yes"])

    assert result.exit_code == 11
    assert result.stderr.startswith("error:")


def test_install_with_invalid_metadata_exits_with_code_13(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """Installing a mod with missing required metadata exits 13.

    Installs an archive containing package.json missing the required "version"
    field; must exit with code 13 (mapped InvalidMetadataError) and write
    "error:" to stderr.
    """
    clean_repo_at(fake_game_dir)
    # "version" is mandatory in package.json; missing it makes read_package_json
    # raise InvalidMetadataError, which the CLI must map to its own exit code.
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod"})},
        "zip",
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "--file", str(archive)])

    assert result.exit_code == 13
    assert result.stderr.startswith("error:")


@pytest.mark.parametrize(
    ("scenario", "expected_exit_code"),
    [
        ("dirty_tree", 4),
        ("game_running", 5),
        ("ambiguous_mod", 12),
    ],
)
def test_cli_maps_specific_core_errors_to_their_exit_codes(
    scenario: str,
    expected_exit_code: int,
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_archive: MakeArchive,
    install_commit: InstallCommit,
) -> None:
    """Core exceptions map to exit codes: 4 for dirty tree, 5 for game running,
    12 for ambiguous mod names.

    Parametrized over three scenarios triggering each error; the CLI must
    exit with the corresponding code (4, 5, or 12) instead of the default 1.
    """
    if scenario == "dirty_tree":
        # No baseline commit: the tracked profile is still untracked.
        Repo.init(detect_layout(fake_game_dir))
        archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
        argv = ["install", "--file", str(archive)]
    elif scenario == "game_running":
        clean_repo_at(fake_game_dir)
        monkeypatch.setattr(game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"])
        archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
        argv = ["install", "--file", str(archive)]
    else:
        repo = clean_repo_at(fake_game_dir)
        install_commit(repo, "CoolMod", minute=1)
        install_commit(repo, "coolmod", minute=2)
        argv = ["remove", "COOLMOD", "--yes"]
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, argv)

    assert result.exit_code == expected_exit_code
