"""Game directory detection, SPT version lookup and the running-game check."""

import json
from pathlib import Path
from typing import Final

import psutil

from sptmod.core.errors import GameRunningError, NotAGameDirError

SPT_DIR: Final = Path("SPT")
BEPINEX_DIR: Final = Path("BepInEx")
MODS_DIR: Final = Path("SPT/user/mods")
PROFILES_DIR: Final = Path("SPT/user/profiles")
PROFILE_BACKUPS_DIR: Final = Path("SPT/user/profiles/backups")
PLUGINS_DIR: Final = Path("BepInEx/plugins")
BEPINEX_CONFIG_DIR: Final = Path("BepInEx/config")
CORE_CONFIG_FILE: Final = Path("SPT/SPT_Data/configs/core.json")

SPT_VERSION_FIELD: Final = "sptVersion"

GAME_PROCESS_NAMES: Final[tuple[str, ...]] = ("SPT.Server", "EscapeFromTarkov")


def is_game_dir(path: Path) -> bool:
    """Return whether ``path`` looks like an SPT installation.

    True exactly when both ``SPT/`` and ``BepInEx/`` exist below ``path``.
    """
    return (path / SPT_DIR).is_dir() and (path / BEPINEX_DIR).is_dir()


def validate_game_dir(path: Path) -> None:
    """Check that ``path`` is an SPT installation.

    Raises:
        NotAGameDirError: ``path`` is missing, is no directory or lacks
            ``SPT/`` or ``BepInEx/``. The message names what is missing.
    """
    if not path.exists():
        raise NotAGameDirError(f"{path} does not exist")
    if not path.is_dir():
        raise NotAGameDirError(f"{path} is not a directory")
    missing = [
        str(directory)
        for directory in (SPT_DIR, BEPINEX_DIR)
        if not (path / directory).is_dir()
    ]
    if missing:
        raise NotAGameDirError(
            f"{path} is not an SPT game directory, missing: {', '.join(missing)}"
        )


def spt_version(path: Path) -> str | None:
    """Return the SPT version of the installation at ``path``.

    Reads the ``sptVersion`` field from ``SPT/SPT_Data/configs/core.json``.
    Returns ``None`` if the file is missing, unreadable, no valid JSON or has
    no ``sptVersion`` field; callers warn instead of failing.
    """
    config_file = path / CORE_CONFIG_FILE
    try:
        raw = config_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(config, dict):
        return None
    version = config.get(SPT_VERSION_FIELD)
    if not isinstance(version, str) or not version:
        return None
    return version


def is_game_process_name(name: str) -> bool:
    """Return whether a process name belongs to the SPT server or client.

    Matches case-insensitively on the substrings in ``GAME_PROCESS_NAMES``, so
    Wine processes whose name ends in ``.exe`` match as well.
    """
    lowered = name.lower()
    return any(candidate.lower() in lowered for candidate in GAME_PROCESS_NAMES)


def running_game_processes() -> list[str]:
    """Return the names of the running SPT server and client processes.

    The only place that scans the process table (via ``psutil``); tests replace
    this function instead of touching real processes.
    """
    names: list[str] = []
    for process in psutil.process_iter():
        try:
            name = process.name()
        except psutil.Error:
            continue
        if is_game_process_name(name):
            names.append(name)
    return names


def ensure_game_not_running() -> None:
    """Check that neither the SPT server nor the client is running.

    Raises:
        GameRunningError: at least one process reported by
            :func:`running_game_processes` is running. The message names them.
    """
    names = running_game_processes()
    if names:
        raise GameRunningError(
            f"the game is running, close it first: {', '.join(names)}"
        )
