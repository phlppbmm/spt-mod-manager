"""CLI smoke tests for sptmod: exit codes, stderr messages, happy-path output."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fixture_types import InstallCommit, MakeArchive
from sptmod.cli import app
from sptmod.core import game_dir
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no game processes are detected during CLI tests."""
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _clean_repo(path: Path) -> Repo:
    """Initialize a game directory repository and commit the baseline configuration."""
    repo = Repo.init(detect_layout(path))
    repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    return repo


def test_init_happy_path_exits_zero_and_reports_the_spt_version(
    fake_game_dir: Path,
) -> None:
    """init command exits 0 and stdout contains "4.1.0" from the detected layout."""
    result = runner.invoke(app, ["init", str(fake_game_dir)])

    assert result.exit_code == 0
    assert "4.1.0" in result.stdout


def test_init_on_a_non_game_dir_exits_with_code_3_and_reports_on_stderr(
    tmp_path: Path,
) -> None:
    """Init rejects non-game directories with exit code 3."""
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
    _clean_repo(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "--file", str(archive)])

    assert result.exit_code == 0
    assert "CoolPlugin" in result.stdout
    assert "--version" in result.stdout


def test_install_file_with_a_version_override_prints_no_versionless_hint(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """Install with explicit --version suppresses the version hint."""
    _clean_repo(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(
        app, ["install", "--file", str(archive), "--version", "1.0.0"]
    )

    assert result.exit_code == 0
    assert "--version" not in result.stdout


def test_list_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """list command exits 0 when the game directory has mods."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0


def test_files_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """files command exits 0 for an installed mod name."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["files", "CoolMod"])

    assert result.exit_code == 0


def test_history_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """history command exits 0 when repo has commits."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0


def test_remove_happy_path_exits_zero_and_reports_the_removed_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """Remove command succeeds and prints the removed mod name."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "CoolMod"])

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
    """Remove prompts for confirmation and leaves mod when declined."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    dll = fake_game_dir / "BepInEx/plugins/CoolMod.dll"

    result = runner.invoke(app, ["remove", "CoolMod", *extra_args], input="n\n")

    assert result.exit_code != 0
    assert dll.exists()


def test_remove_without_yes_prompts_and_confirming_removes_the_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """Remove proceeds when user confirms the prompt."""
    repo = _clean_repo(fake_game_dir)
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
    """--yes flag bypasses the confirmation prompt."""
    repo = _clean_repo(fake_game_dir)
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
    """Removing a non-existent mod fails with exit code 11."""
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "Ghost"])

    assert result.exit_code == 11
    assert result.stderr.startswith("error:")


def test_install_with_invalid_metadata_exits_with_code_13(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """Installing a mod with invalid metadata fails with exit code 13."""
    _clean_repo(fake_game_dir)
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
    """Dirty tree, game running, and ambiguous mod errors exit with codes 4, 5,
    and 12 respectively."""
    if scenario == "dirty_tree":
        # No baseline commit: the tracked profile is still untracked.
        Repo.init(detect_layout(fake_game_dir))
        archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
        argv = ["install", "--file", str(archive)]
    elif scenario == "game_running":
        _clean_repo(fake_game_dir)
        monkeypatch.setattr(
            game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"]
        )
        archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
        argv = ["install", "--file", str(archive)]
    else:
        repo = _clean_repo(fake_game_dir)
        install_commit(repo, "CoolMod", minute=1)
        install_commit(repo, "coolmod", minute=2)
        argv = ["remove", "COOLMOD"]
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, argv)

    assert result.exit_code == expected_exit_code
