"""CLI smoke tests for sptmod: exit codes, stderr messages, happy-path output."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sptmod.cli import app
from sptmod.core import game_dir
from sptmod.core.repo import Repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(path)
    repo.commit(
        "chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None
    )
    return repo


def test_init_happy_path_exits_zero_and_reports_the_spt_version(
    fake_game_dir: Path,
) -> None:
    result = runner.invoke(app, ["init", str(fake_game_dir)])

    assert result.exit_code == 0
    assert "4.1.0" in result.stdout


def test_init_on_a_non_game_dir_exits_with_code_3_and_reports_on_stderr(
    tmp_path: Path,
) -> None:
    not_a_game_dir = tmp_path / "not-a-game"
    not_a_game_dir.mkdir()

    result = runner.invoke(app, ["init", str(not_a_game_dir)])

    assert result.exit_code == 3
    assert result.stderr.startswith("error:")


def test_install_happy_path_exits_zero_and_reports_the_installed_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive
) -> None:
    _clean_repo(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "--file", str(archive)])

    assert result.exit_code == 0
    assert "CoolPlugin" in result.stdout


def test_list_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0


def test_files_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["files", "CoolMod"])

    assert result.exit_code == 0


def test_history_happy_path_exits_zero(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0


def test_remove_happy_path_exits_zero_and_reports_the_removed_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "CoolMod"])

    assert result.exit_code == 0
    assert "CoolMod" in result.stdout


def test_remove_unknown_mod_exits_with_code_11_and_reports_on_stderr(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "Ghost"])

    assert result.exit_code == 11
    assert result.stderr.startswith("error:")


def test_install_with_invalid_metadata_exits_with_code_13(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive
) -> None:
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
    make_archive,
    install_commit,
) -> None:
    if scenario == "dirty_tree":
        # No baseline commit: the tracked profile is still untracked.
        Repo.init(fake_game_dir)
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
