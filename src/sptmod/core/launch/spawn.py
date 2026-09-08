"""Starting the SPT server, the game client and the watcher of one launch.

This is where the environment the official SPT launcher builds is rebuilt, so
a launch from sptmod runs the game the same way.
"""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sptmod.core.errors import LaunchError, LauncherSettingsError
from sptmod.core.game_dir import GameLayout
from sptmod.core.launch.process import process_ref, spawn_detached
from sptmod.core.launch.state import LAUNCH_LOG_FILE_NAME, ProcessRef, log, log_stream
from sptmod.core.models import Json

CLIENT_ARGS_HEAD: Final[tuple[str, ...]] = ("-force-gfx-jobs", "native")
CLIENT_EXIT_GRACE_SECONDS: Final = 0.5
_GAMEMODE_COMMAND: Final = "gamemoderun"
# The official launcher sets this for the whole process on Linux and the game
# inherits it, so a launch from sptmod runs in the same environment.
_NVIDIA_SYNC_ENV: Final = "__NV_DISABLE_EXPLICIT_SYNC"
_TOKEN_ARGUMENT: Final = "-token="
_HIDDEN: Final = "<hidden>"
# The two settings a player has to check when the game starts into nothing;
# every other value sptmod passes on may name a personal path.
_SHOWN_ENV_KEYS: Final[tuple[str, ...]] = ("WINEPREFIX", "PROTONPATH")
_QUOTES: Final[tuple[str, str]] = ('"', "'")
@dataclass(frozen=True)
class _LinuxSettings:
    """The Linux part of the settings the official SPT launcher writes."""

    prefix_path: str
    umu_path: str
    launch_settings: str
    proton_version: str
    default_env: str
    game_mode: bool
def tokenize_launch_settings(text: str) -> list[str]:
    """Split launch settings into tokens the way the official launcher does.

    Whitespace separates tokens outside quotes. Both quote characters open a
    run that ends at the same character and is dropped, so a value may carry
    spaces; a backslash before a quote character keeps that character as text.
    A quote that is never closed ends its token at the end of the text, and a
    quoted run that holds nothing still yields an empty token.
    """
    tokens: list[str] = []
    current: list[str] = []
    quote = ""
    started = False
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\" and text[index + 1 : index + 2] in _QUOTES:
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
            else:
                current.append(character)
        elif character in _QUOTES:
            quote = character
        elif character.isspace():
            if started:
                tokens.append("".join(current))
                current.clear()
                started = False
            continue
        else:
            current.append(character)
        started = True
    if started:
        tokens.append("".join(current))
    return tokens
def spawn_server(layout: GameLayout) -> ProcessRef:
    """Start the SPT server of ``layout`` in its runtime directory."""
    binary = layout.root / layout.server_binary
    if not binary.is_file():
        raise LaunchError(f"The SPT server program {binary} is missing.")
    working_dir = layout.root / layout.runtime_dir
    log(
        layout.root,
        f"Starting the SPT server: {binary} (working directory {working_dir}).",
    )
    return process_ref(spawn_detached([str(binary)], working_dir, dict(os.environ)))


def spawn_client(layout: GameLayout, profile_id: str, base_url: str) -> ProcessRef:
    """Start the game client of ``layout`` for one profile and server."""
    executable = layout.root / layout.client_exe
    if not executable.is_file():
        raise LaunchError(f"The game program {executable} is missing.")
    arguments = [
        *CLIENT_ARGS_HEAD,
        f"-token={profile_id}",
        f"-config={{'BackendUrl':'{base_url}',"
        f"'Version':'live','MatchingVersion':'live'}}",
    ]
    if sys.platform == "win32":
        windows_argv = [str(executable), *arguments]
        log(layout.root, f"Starting the game: {_redacted_command(windows_argv)}.")
        return process_ref(
            spawn_detached(windows_argv, layout.root, dict(os.environ))
        )
    settings = _linux_settings(layout)
    # The official launcher points Wine at the ``pfx`` directory inside the prefix folder.
    assigned = {
        "WINEPREFIX": settings.prefix_path,
        "PROTONPATH": settings.proton_version,
        _NVIDIA_SYNC_ENV: "1",
    }
    extra: list[str] = []
    tokens = [settings.default_env] if settings.default_env else []
    tokens += tokenize_launch_settings(settings.launch_settings)
    for token in tokens:
        separator = token.find("=")
        if token.startswith("-") or separator <= 0:
            extra.append(token)
        else:
            assigned[token[:separator]] = _unquoted(token[separator + 1 :])
    # The official launcher hands umu the full path of the executable.
    argv = [settings.umu_path, str(executable), *arguments, *extra]
    if settings.game_mode:
        argv.insert(0, _GAMEMODE_COMMAND)
    log(layout.root, f"Starting the game: {_redacted_command(argv)}.")
    log(
        layout.root,
        f"Settings sptmod passed to the game: {_environment_summary(assigned)}.",
    )
    with log_stream(layout.root) as stderr:
        process = spawn_detached(
            argv, layout.root, dict(os.environ) | assigned, stderr=stderr
        )
    _refuse_failed_client(process, layout.root)
    return process_ref(process)


def _refuse_failed_client(process: subprocess.Popen[bytes], path: Path) -> None:
    """Refuse a launch whose game program stopped again right away.

    A wrapper that exits within :data:`CLIENT_EXIT_GRACE_SECONDS` never got as
    far as the game, and its own complaint is already in the launch log,
    because that is where its error output went.
    """
    try:
        code = process.wait(timeout=CLIENT_EXIT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return
    log(path, f"The game program stopped right away with exit code {code}.")
    if code != 0:
        raise LaunchError(
            f"The game program could not be started. See "
            f"{path / LAUNCH_LOG_FILE_NAME} for what it reported."
        )


def _unquoted(value: str) -> str:
    """Return a settings value without the pair of quotes around it."""
    for quote in _QUOTES:
        if len(value) > 1 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1]
    return value


def _redacted_command(argv: Sequence[str]) -> str:
    """Render a command line with the profile token hidden."""
    return " ".join(
        _TOKEN_ARGUMENT + _HIDDEN if part.startswith(_TOKEN_ARGUMENT) else part
        for part in argv
    )


def _environment_summary(assigned: dict[str, str]) -> str:
    """Render what sptmod put into the game's environment, hiding the values.

    Only the two settings a player has to check when the game starts into
    nothing are shown; everything else is named without its value.
    """
    return " ".join(
        f"{key}={value if key in _SHOWN_ENV_KEYS else _HIDDEN}"
        for key, value in assigned.items()
    )


def _linux_settings(layout: GameLayout) -> _LinuxSettings:
    """Read the Linux settings the official SPT launcher writes."""
    settings_file = layout.root / layout.launcher_settings_file
    try:
        payload: Json = json.loads(settings_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise LauncherSettingsError(
            f"The SPT launcher settings in {settings_file} cannot be read. "
            f"Start the official SPT launcher once so it can create them, "
            f"then try again."
        ) from error
    linux = payload.get("LinuxSettings") if isinstance(payload, dict) else None
    if not isinstance(linux, dict):
        raise LauncherSettingsError(
            f"The SPT launcher settings in {settings_file} hold no Linux "
            f"section. Start the official SPT launcher once so it can create "
            f"it, then try again."
        )
    umu_path = _setting(linux, "UmuPath")
    if not umu_path:
        raise LauncherSettingsError(
            f"The SPT launcher settings in {settings_file} name no program to "
            f"run the game with. Set it in the official SPT launcher, then "
            f"try again."
        )
    return _LinuxSettings(
        prefix_path=_setting(linux, "PrefixPath"),
        umu_path=umu_path,
        launch_settings=_setting(linux, "LaunchSettings"),
        proton_version=_setting(linux, "ProtonVersion"),
        default_env=_setting(linux, "DefaultEnv"),
        game_mode=linux.get("GameMode") is True,
    )


def _setting(linux: dict[str, Json], key: str) -> str:
    """Return one text setting, empty when it is missing or not text."""
    value = linux.get(key)
    return value if isinstance(value, str) else ""


def spawn_watcher(path: Path) -> ProcessRef:
    """Start the process that waits for the game to close."""
    return process_ref(
        spawn_detached(
            [sys.executable, "-m", "sptmod.watch", str(path)], path, dict(os.environ)
        )
    )
