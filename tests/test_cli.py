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


@pytest.mark.parametrize(
    "argv",
    [["list"], ["files", "CoolMod"], ["history"]],
    ids=["list", "files-by-name", "history"],
)
def test_read_only_commands_exit_zero_on_a_repository_with_one_installed_mod(
    argv: list[str],
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_commit: InstallCommit,
) -> None:
    """The commands that only read the repository succeed on an ordinary installation.

    Creates a game directory with a single install commit for "CoolMod" and runs each
    reading command from inside it; asserts every one exits 0. Reason: these are the
    commands a user reaches for first, so a failure to even read the repository has to
    surface immediately.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0


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


@pytest.mark.parametrize(
    ("extra_args", "stdin"),
    [([], "y\n"), (["--yes"], None)],
    ids=["confirm-at-prompt", "yes-flag"],
)
def test_an_approved_removal_deletes_the_mod_files_and_reports_the_mod(
    extra_args: list[str],
    stdin: str | None,
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_commit: InstallCommit,
) -> None:
    """A removal the user approves deletes the mod files, whether approved at the prompt
    or up front with ``--yes``.

    Installs "CoolMod" and removes it once by answering "y" at the confirmation prompt and
    once by passing ``--yes`` without any input; asserts both exit 0, name the mod on
    stdout and leave its DLL deleted. Reason: ``--yes`` exists to skip the question, not
    to take a different path, so both routes must end in the same removal.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    dll = fake_game_dir / "BepInEx/plugins/CoolMod.dll"

    result = runner.invoke(app, ["remove", "CoolMod", *extra_args], input=stdin)

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


def test_install_into_a_read_only_mods_folder_exits_21_and_reports_on_stderr(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """A refused mod file write exits with the code cli.EXIT_CODES maps it to.

    The mods folder of the game directory is read-only, so the install cannot create the
    mod directory; the CLI must report the plain message on stderr and exit 21 instead of
    letting a traceback and the generic exit code 1 reach the user.
    """
    clean_repo_at(fake_game_dir)
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0"})}, "zip"
    )
    monkeypatch.chdir(fake_game_dir)
    obstacle = fake_game_dir / "SPT/user/mods"
    obstacle.chmod(0o555)

    try:
        result = runner.invoke(app, ["install", "--file", str(archive)])
    finally:
        obstacle.chmod(0o755)

    assert result.exit_code == 21
    assert result.stderr.startswith("error: The mod files could not be written")
    assert "SPT/user/mods/CoolMod/package.json" in result.stderr
