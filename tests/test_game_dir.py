"""Tests for game directory detection, SPT version lookup and process check.

CONTRACT CHANGE (spt-mod-manager-fyx): ``validate_game_dir`` now returns the
detected ``GameLayout`` instead of ``None`` (still raising ``NotAGameDirError``
for the same bad-input cases, so the tests below are otherwise unchanged), and
the free ``spt_version(path)`` function was removed in favor of the layout- and
git-repo-aware version chain (``read_core_json_version``, ``read_pe_file_version``,
``resolve_spt_version``). Its former core.json-reading coverage moved to
``tests/test_game_layout.py::test_read_core_json_version_*``.
"""

from pathlib import Path

import pytest

from sptmod.core.errors import GameRunningError, NotAGameDirError
from sptmod.core.game_dir import (
    ensure_game_not_running,
    is_game_dir,
    is_game_process_name,
    validate_game_dir,
)


def _make_game_dir(root: Path) -> Path:
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)
    return root


# --- is_game_dir / validate_game_dir ---------------------------------------


def test_is_game_dir_true_for_valid_layout(tmp_path: Path) -> None:
    game_dir = _make_game_dir(tmp_path / "game")

    assert is_game_dir(game_dir) is True


@pytest.mark.parametrize("present_dir", ["BepInEx", "SPT"])
def test_is_game_dir_false_when_a_required_dir_is_missing(
    tmp_path: Path, present_dir: str
) -> None:
    game_dir = tmp_path / "game"
    (game_dir / present_dir).mkdir(parents=True)

    assert is_game_dir(game_dir) is False


def test_is_game_dir_false_when_path_is_a_file(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")

    assert is_game_dir(file_path) is False


def test_is_game_dir_false_when_path_does_not_exist(tmp_path: Path) -> None:
    assert is_game_dir(tmp_path / "does-not-exist") is False


def test_validate_game_dir_accepts_valid_layout(tmp_path: Path) -> None:
    game_dir = _make_game_dir(tmp_path / "game")

    validate_game_dir(game_dir)  # must not raise


@pytest.mark.parametrize("present_dir", ["BepInEx", "SPT"])
def test_validate_game_dir_raises_when_a_required_dir_is_missing(
    tmp_path: Path, present_dir: str
) -> None:
    game_dir = tmp_path / "game"
    (game_dir / present_dir).mkdir(parents=True)

    with pytest.raises(NotAGameDirError):
        validate_game_dir(game_dir)


def test_validate_game_dir_raises_when_path_is_a_file(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(NotAGameDirError):
        validate_game_dir(file_path)


def test_validate_game_dir_raises_when_path_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(NotAGameDirError):
        validate_game_dir(tmp_path / "does-not-exist")


# --- is_game_process_name ---------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "SPT.Server.exe",
        "EscapeFromTarkov.exe",
        "spt.server.exe",
        "escapefromtarkov.exe",
        "SPT.Server",
        "EscapeFromTarkov",
        "/some/wine/path/EscapeFromTarkov.exe",
    ],
)
def test_is_game_process_name_matches_game_processes(name: str) -> None:
    assert is_game_process_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "explorer.exe",
        "steam.exe",
        "python.exe",
        "",
        "notepad.exe",
    ],
)
def test_is_game_process_name_rejects_unrelated_processes(name: str) -> None:
    assert is_game_process_name(name) is False


# --- ensure_game_not_running -------------------------------------------------


def test_ensure_game_not_running_passes_when_no_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sptmod.core.game_dir.running_game_processes", lambda: [])

    ensure_game_not_running()  # must not raise


def test_ensure_game_not_running_raises_when_processes_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sptmod.core.game_dir.running_game_processes",
        lambda: ["SPT.Server.exe"],
    )

    with pytest.raises(GameRunningError):
        ensure_game_not_running()


def test_ensure_game_not_running_message_names_the_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sptmod.core.game_dir.running_game_processes",
        lambda: ["SPT.Server.exe", "EscapeFromTarkov.exe"],
    )

    with pytest.raises(GameRunningError) as exc_info:
        ensure_game_not_running()

    assert "SPT.Server.exe" in str(exc_info.value)
    assert "EscapeFromTarkov.exe" in str(exc_info.value)
