"""Starting the SPT server and the game client for one player profile.

This is the second module allowed to use ``subprocess``: it starts programs
that outlive the call, with an environment shaped like the one the official SPT
launcher builds, which is the opposite of the short, captured git calls of
:mod:`sptmod.core.repo`.

What runs is remembered in ``.sptmod-launch.json`` inside the game directory,
next to the apply lock, because every short-lived process involved — the call
that starts the game, the calls that report its state, and the separate watcher
that notices the game closing — only knows the game directory. The file is a
hint: the process table decides what is really running.

What happened during a launch is appended to ``.sptmod-launch.log`` beside it,
because the game and the server are started with all their streams closed: the
log is the only trace a launch that went wrong leaves behind.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import IO, Final

import psutil

from sptmod.core import game_dir, sptserver
from sptmod.core.errors import (
    GameRunningError,
    LaunchError,
    LauncherSettingsError,
    LoginFailedError,
    ServerPortBusyError,
)
from sptmod.core.game_dir import GameLayout, GameProcess, detect_layout
from sptmod.core.models import Json, Progress

LAUNCH_STATE_FILE_NAME: Final = ".sptmod-launch.json"
LAUNCH_LOCK_FILE_NAME: Final = ".sptmod-launch.lock"
LAUNCH_LOG_FILE_NAME: Final = ".sptmod-launch.log"
LAUNCH_LOG_MAX_BYTES: Final = 200 * 1024
READY_TIMEOUT: Final = 300.0
POLL_SECONDS: Final = 1.0
WATCH_POLL_SECONDS: Final = 3.0
WATCH_GRACE_SECONDS: Final = 120.0
CLIENT_NEVER_STARTED: Final = "The game did not start."
TERMINATE_TIMEOUT: Final = 10.0
CLIENT_ARGS_HEAD: Final[tuple[str, ...]] = ("-force-gfx-jobs", "native")
CLIENT_EXIT_GRACE_SECONDS: Final = 0.5

_TEMP_PREFIX: Final = ".sptmod-launch-"
_WAIT_PERCENT_START: Final = 10
_WAIT_PERCENT_SPAN: Final = 60
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


class LaunchStep(StrEnum):
    """The phases a launch reports progress for."""

    SERVER = "server"
    WAITING = "waiting"
    LOGIN = "login"
    CLIENT = "client"
    DONE = "done"


@dataclass(frozen=True)
class LaunchResult:
    """What one launch started.

    ``server_pid`` is ``0`` when a server that was already running got used
    instead of a new one, which ``server_started`` reports.
    """

    profile_id: str
    nickname: str
    server_pid: int
    client_pid: int
    server_started: bool


@dataclass(frozen=True)
class LaunchStatus:
    """What of the installation is running right now.

    The process IDs are ``0`` where nothing runs; ``profile_id`` names the
    profile the last launch used and is empty when that is unknown. The
    ``_stoppable`` flags say that sptmod can close this process: it started it
    and it is still that same process. A process found only by its name, and a
    server that was already running when the launch began, are reported but
    never closed.
    """

    server_running: bool
    client_running: bool
    server_pid: int
    client_pid: int
    profile_id: str
    server_stoppable: bool
    client_stoppable: bool


@dataclass(frozen=True)
class StopResult:
    """What stopping the game closed, and how to say so to the player."""

    server_stopped: bool
    client_stopped: bool
    message: str


@dataclass(frozen=True)
class _ProcessRef:
    """A remembered process: the pair survives a reused process number."""

    pid: int
    started_at: float


@dataclass(frozen=True)
class _LaunchState:
    """The contents of the launch state file.

    ``failure`` names why the launch did not end up with a running game,
    empty while nothing went wrong.
    """

    profile_id: str = ""
    stop_server_on_exit: bool = True
    server: _ProcessRef | None = None
    server_started_by_sptmod: bool = False
    client: _ProcessRef | None = None
    watcher: _ProcessRef | None = None
    failure: str = ""


@dataclass(frozen=True)
class _LinuxSettings:
    """The Linux part of the settings the official SPT launcher writes."""

    prefix_path: str
    umu_path: str
    launch_settings: str
    proton_version: str
    default_env: str
    game_mode: bool


def launch_profile(
    path: Path,
    profile_id: str,
    stop_server_on_exit: bool = True,
    progress: Progress | None = None,
    *,
    ready_timeout: float = READY_TIMEOUT,
    poll_seconds: float = POLL_SECONDS,
    request_timeout: float = sptserver.REQUEST_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> LaunchResult:
    """Start the game at ``path`` with the player profile ``profile_id``.

    Starts the SPT server unless one is already answering, waits until it does,
    signs the profile in and starts the game. Both processes outlive this call;
    what was started is remembered so :func:`launch_status` and
    :func:`stop_game` find it again. When ``stop_server_on_exit`` is set and
    the server was started here, it is closed once the game closes.

    Args:
        path: The game directory.
        profile_id: The profile to play, named by its file name without the
            extension.
        stop_server_on_exit: Close the server when the game closes.
        progress: Optional callback for the four phases of the launch.
        ready_timeout: Seconds to wait for the server to answer.
        poll_seconds: Seconds between two questions to the server.
        request_timeout: Seconds one question to the server may take.
        sleep: Sleep function, injected for testing.
        clock: Monotonic clock, injected for testing.

    Returns:
        The profile, its player name and the started processes.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        GameRunningError: The game is already running.
        ServerPortBusyError: Another program holds the server's address.
        LoginFailedError: The server does not know the profile.
        LauncherSettingsError: The settings of the official SPT launcher are
            missing or unusable.
        LaunchError: A program is missing, could not be started, the server
            did not answer in time, another launch is already in progress, or
            what was started could not be noted in the game directory.
    """
    layout = detect_layout(path)
    with _launch_lock(path):
        return _launch(
            path,
            layout,
            profile_id,
            stop_server_on_exit,
            progress,
            ready_timeout=ready_timeout,
            poll_seconds=poll_seconds,
            request_timeout=request_timeout,
            sleep=sleep,
            clock=clock,
        )


def _launch(
    path: Path,
    layout: GameLayout,
    profile_id: str,
    stop_server_on_exit: bool,
    progress: Progress | None,
    *,
    ready_timeout: float,
    poll_seconds: float,
    request_timeout: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> LaunchResult:
    """Run one launch while the launch lock is held."""
    running = game_dir.game_processes()
    _refuse_running_client(running)
    username, nickname = _profile_login(layout, profile_id)
    # The profile id is the login token, so only the player name is logged.
    _log(path, f"Starting the game for {nickname}.")

    base_url = sptserver.server_base_url(layout)
    answer = sptserver.probe(base_url, request_timeout)
    if answer == "foreign":
        raise ServerPortBusyError(_port_busy_message(base_url))

    server_started = answer == "free"
    server: _ProcessRef | None
    if server_started:
        _report(progress, LaunchStep.SERVER, 5, "Starting the server")
        server = _spawn_server(layout)
        _wait_for_server(
            path,
            base_url,
            server,
            progress,
            ready_timeout=ready_timeout,
            poll_seconds=poll_seconds,
            request_timeout=request_timeout,
            sleep=sleep,
            clock=clock,
        )
    else:
        _log(path, "The SPT server was already answering, so it is reused.")
        server = _running_ref(running, "server")

    _report(progress, LaunchStep.LOGIN, 80, f"Signing in as {nickname}")
    if not sptserver.login(base_url, username, request_timeout):
        _log(path, f"The SPT server refused to sign {nickname} in.")
        raise LoginFailedError(
            f"The SPT server does not know the profile {nickname}. "
            f"Start the official SPT launcher once to create it."
        )

    _log(path, f"The SPT server signed {nickname} in.")
    _report(progress, LaunchStep.CLIENT, 95, "Starting the game")
    client = _spawn_client(layout, profile_id, base_url)

    state = _LaunchState(
        profile_id=profile_id,
        stop_server_on_exit=stop_server_on_exit,
        server=server,
        server_started_by_sptmod=server_started,
        client=client,
    )
    try:
        _write_state(path, state)
        _write_state(path, replace(state, watcher=_spawn_watcher(path)))
    except OSError as error:
        raise LaunchError(
            f"The SPT server (process {server.pid if server else 0}) and the "
            f"game (process {client.pid}) are running, but sptmod cannot note "
            f"that in {path}: {error}. Close them yourself when you are done."
        ) from error
    _report(progress, LaunchStep.DONE, 100, "The game is starting")
    return LaunchResult(
        profile_id=profile_id,
        nickname=nickname,
        server_pid=server.pid if server is not None else 0,
        client_pid=client.pid,
        server_started=server_started,
    )


def launch_status(path: Path) -> LaunchStatus:
    """Report whether the server and the game of ``path`` are running.

    Reads the remembered processes and the process table, so a server or game
    started outside sptmod is reported as well. Never fails: an installation
    nothing is known about simply reports nothing running. Only a process
    sptmod started here is stoppable; one found by its name alone, and a server
    that was already running, are reported and left alone.
    """
    state = _read_state(path)
    running = game_dir.game_processes()
    server = _running_pid(state.server, running, "server")
    client = _running_pid(state.client, running, "client")
    return LaunchStatus(
        server_running=server != 0,
        client_running=client != 0,
        server_pid=server,
        client_pid=client,
        profile_id=state.profile_id,
        server_stoppable=state.server_started_by_sptmod
        and _live_ref(state.server) is not None,
        client_stoppable=_live_ref(state.client) is not None,
    )


def stop_game(path: Path) -> StopResult:
    """Close the game and the SPT server sptmod started at ``path``.

    Closes the watcher first so it cannot act on a half-stopped installation,
    then the game with everything it started, then the server. Only a process
    sptmod both noted at launch time and started itself is closed, and only
    while the number and the start time noted with it still name that very
    process. Anything else is left alone, however plainly it shows up in the
    process table: it belongs to somebody else, the server that was already
    answering when the launch began included.
    """
    state = _read_state(path)
    if state.watcher is not None:
        _terminate(state.watcher)
    client_stopped = state.client is not None and _terminate(state.client)
    server_stopped = (
        state.server is not None
        and state.server_started_by_sptmod
        and _terminate(state.server)
    )
    _clear_state(path)
    still_playing = any(
        process.kind == "client" for process in game_dir.game_processes()
    )
    return StopResult(
        server_stopped=server_stopped,
        client_stopped=client_stopped,
        message=_stop_message(
            state,
            server_stopped=server_stopped,
            client_stopped=client_stopped,
            still_playing=still_playing,
        ),
    )


def _stop_message(
    state: _LaunchState,
    *,
    server_stopped: bool,
    client_stopped: bool,
    still_playing: bool,
) -> str:
    """Say in plain words what closing the game did.

    ``still_playing`` says a game is left in the process table afterwards,
    which on Linux is the usual outcome: what a launch starts there only hosts
    the game and the game outlives it under a number of its own. It is said out
    loud rather than acted on, because sptmod closes nothing it did not start.
    """
    if state.server is None and state.client is None:
        first = "sptmod did not start a game here, so there is nothing to close."
    else:
        closed = [
            name
            for name, stopped in (
                ("game", client_stopped),
                ("SPT server", server_stopped),
            )
            if stopped
        ]
        first = (
            f"Closed the {' and the '.join(closed)}."
            if closed
            else "Nothing sptmod started here is still running."
        )
    sentences = [first]
    if state.server is not None and not state.server_started_by_sptmod:
        sentences.append(
            "The SPT server was already running before sptmod, so it stays up."
        )
    if still_playing:
        sentences.append("The game is still running; close it yourself.")
    return " ".join(sentences)


def watch_game(
    path: Path,
    *,
    poll_seconds: float = WATCH_POLL_SECONDS,
    grace_seconds: float = WATCH_GRACE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until the game of ``path`` closes, then clean up after it.

    Runs in its own process for as long as the game does. The game is looked
    for by process name as well as by the process the launch started, because
    on Linux that process only wraps the game and may be gone or replaced
    while the game keeps running. As long as that started process lives the
    game counts as running, whatever the process table shows. Only when it is
    gone as well and no game shows up within ``grace_seconds`` did the launch
    fail: the server is closed as it would be after a game, and the reason is
    written to the state file. Otherwise the server is closed once both the
    started process and every game process are gone, if the launch asked for
    it and sptmod started that server; the remembered state is dropped either
    way.

    Args:
        path: The game directory.
        poll_seconds: Seconds between two looks at the process table.
        grace_seconds: Seconds the game has to show up after the launch.
        sleep: Sleep function, injected for testing.
        clock: Monotonic clock, injected for testing.
    """
    state = _read_state(path)
    if state.client is None:
        return
    if not _await_client(
        state.client,
        poll_seconds=poll_seconds,
        grace_seconds=grace_seconds,
        sleep=sleep,
        clock=clock,
    ):
        _log(
            path,
            f"{CLIENT_NEVER_STARTED} The process the launch started is gone "
            f"and no game appeared within {int(grace_seconds)} seconds.",
        )
        _log_process_table(path)
        _stop_watched_server(path, state)
        with suppress(OSError):
            _write_state(path, replace(state, failure=CLIENT_NEVER_STARTED))
        return
    _log(path, "The game is running.")
    while _client_running(state.client):
        sleep(poll_seconds)
    _log(path, "The game and the process the launch started are both gone.")
    _stop_watched_server(path, state)
    _clear_state(path)


def _await_client(
    client: _ProcessRef,
    *,
    poll_seconds: float,
    grace_seconds: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    """Wait for a running game, at most ``grace_seconds`` long."""
    started = clock()
    while not _client_running(client):
        if clock() - started >= grace_seconds:
            return False
        sleep(poll_seconds)
    return True


def _client_running(client: _ProcessRef) -> bool:
    """Return whether a game is running, remembered or found by name."""
    if _spawned_alive(client):
        return True
    return _running_ref(game_dir.game_processes(), "client") is not None


def _spawned_alive(client: _ProcessRef) -> bool:
    """Return whether the process the launch started for the game still runs.

    Deliberately weaker than :func:`_live_ref`: the bare process number counts
    as well, even when the start time noted at spawn time does not compare
    equal to the one the operating system reports now. That start time is read
    a moment after the process was created and is ``0.0`` when it could not be
    read at all, so a mismatch says far more often "the note is imprecise" than
    "this number belongs to a stranger now". Being wrong here costs a watcher
    that waits longer than it had to; being wrong the other way closes the SPT
    server under a player who is in a raid, which is what this guard exists for.
    """
    return game_dir.process_alive(client.pid, client.started_at) or psutil.pid_exists(
        client.pid
    )


def _stop_watched_server(path: Path, state: _LaunchState) -> None:
    """Close the server of one launch when that launch asked for it."""
    if (
        state.stop_server_on_exit
        and state.server_started_by_sptmod
        and state.server is not None
    ):
        _terminate(state.server)
        _log(path, "The SPT server was closed.")
        return
    _log(path, "The SPT server was left running.")


def _log_process_table(path: Path) -> None:
    """Note what the process table held when no game could be found."""
    games = ", ".join(
        f"{process.name} ({process.kind}, process {process.pid})"
        for process in game_dir.game_processes()
    )
    _log(path, f"SPT processes seen: {games or 'none'}.")
    sightings = ", ".join(
        f"{sighting.name} ({sighting.command}, process {sighting.pid})"
        for sighting in game_dir.spt_process_sightings()
    )
    _log(path, f"Processes mentioning SPT: {sightings or 'none'}.")


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


def _refuse_running_client(running: Sequence[GameProcess]) -> None:
    """Refuse to start a second game; a running server is reused instead."""
    names = [process.name for process in running if process.kind == "client"]
    if names:
        raise GameRunningError(
            f"The game is already running ({', '.join(names)}). Close it first."
        )


def _profile_login(layout: GameLayout, profile_id: str) -> tuple[str, str]:
    """Return the account name and the player name of one profile file."""
    if profile_id in {"", ".", ".."} or "/" in profile_id or "\\" in profile_id:
        raise LaunchError(f"The player profile {profile_id} cannot be read.")
    profile_file = layout.root / layout.profiles_dir / f"{profile_id}.json"
    try:
        payload: Json = json.loads(profile_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise LaunchError(
            f"The player profile {profile_id} cannot be read: {error}"
        ) from error
    info = payload.get("info") if isinstance(payload, dict) else None
    username = info.get("username") if isinstance(info, dict) else None
    if not isinstance(username, str) or not username:
        raise LaunchError(
            f"The player profile {profile_id} carries no account name, "
            f"so the SPT server cannot sign it in."
        )
    return username, _nickname(payload, username)


def _nickname(payload: Json, fallback: str) -> str:
    """Return the player name of a parsed profile, or ``fallback``."""
    node: Json = payload
    for key in ("characters", "pmc", "Info", "Nickname"):
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, str) and node else fallback


def _wait_for_server(
    path: Path,
    base_url: str,
    server: _ProcessRef,
    progress: Progress | None,
    *,
    ready_timeout: float,
    poll_seconds: float,
    request_timeout: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """Ask the freshly started server until it answers, or give up."""
    started = clock()
    polls = 0
    _report(progress, LaunchStep.WAITING, _WAIT_PERCENT_START, "Waiting for the server")
    while True:
        answer = sptserver.probe(base_url, request_timeout)
        polls += 1
        if answer != "free":
            _log_readiness(path, polls, clock() - started, answer)
        if answer == "spt":
            return
        if answer == "foreign":
            raise ServerPortBusyError(_port_busy_message(base_url))
        if not game_dir.process_alive(server.pid, server.started_at):
            _log_readiness(path, polls, clock() - started, "nothing, the server had stopped")
            raise LaunchError(
                "The SPT server stopped right after it was started. "
                "Start it once yourself to see what it reports."
            )
        elapsed = clock() - started
        if elapsed >= ready_timeout:
            _log_readiness(path, polls, elapsed, "nothing at all")
            raise LaunchError(
                f"The SPT server did not answer within {int(ready_timeout)} "
                f"seconds. Start it once yourself to see what it reports."
            )
        percent = _WAIT_PERCENT_START + int(
            _WAIT_PERCENT_SPAN * elapsed / ready_timeout
        )
        _report(progress, LaunchStep.WAITING, percent, "Waiting for the server")
        sleep(poll_seconds)


def _log_readiness(path: Path, polls: int, seconds: float, answer: str) -> None:
    """Note how often and how long the SPT server was asked, and what it said."""
    _log(
        path,
        f"Checks for the SPT server: {polls}, seconds waited: {seconds:.0f}, "
        f"last answer: {answer}.",
    )


def _port_busy_message(base_url: str) -> str:
    """Name the address a stranger holds, in a way a player can act on."""
    return (
        f"Port {base_url.rsplit(':', 1)[-1]} is already in use by another "
        f"program that is not the SPT server. Close that program and try again."
    )


def _spawn_server(layout: GameLayout) -> _ProcessRef:
    """Start the SPT server of ``layout`` in its runtime directory."""
    binary = layout.root / layout.server_binary
    if not binary.is_file():
        raise LaunchError(f"The SPT server program {binary} is missing.")
    working_dir = layout.root / layout.runtime_dir
    _log(
        layout.root,
        f"Starting the SPT server: {binary} (working directory {working_dir}).",
    )
    return _process_ref(_spawn_detached([str(binary)], working_dir, dict(os.environ)))


def _spawn_client(layout: GameLayout, profile_id: str, base_url: str) -> _ProcessRef:
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
        _log(layout.root, f"Starting the game: {_redacted_command(windows_argv)}.")
        return _process_ref(
            _spawn_detached(windows_argv, layout.root, dict(os.environ))
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
    _log(layout.root, f"Starting the game: {_redacted_command(argv)}.")
    _log(
        layout.root,
        f"Settings sptmod passed to the game: {_environment_summary(assigned)}.",
    )
    with _log_stream(layout.root) as stderr:
        process = _spawn_detached(
            argv, layout.root, dict(os.environ) | assigned, stderr=stderr
        )
    _refuse_failed_client(process, layout.root)
    return _process_ref(process)


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
    _log(path, f"The game program stopped right away with exit code {code}.")
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


def _spawn_watcher(path: Path) -> _ProcessRef:
    """Start the process that waits for the game to close."""
    return _process_ref(
        _spawn_detached(
            [sys.executable, "-m", "sptmod.watch", str(path)], path, dict(os.environ)
        )
    )


def _open_detached(
    argv: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    stderr: IO[bytes] | int,
) -> subprocess.Popen[bytes]:
    """Start ``argv`` detached from this process and return the raw handle.

    The child gets its own session (or, on Windows, its own process group and
    no console), so it neither receives the signals of the caller nor leaves a
    window behind, and it reads and writes nothing except what ``stderr``
    names. The handle is deliberately not closed: the process must outlive
    this one.
    """
    if sys.platform == "win32":
        return subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW,
        )
    return subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        start_new_session=True,
    )


def _spawn_detached(
    argv: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    *,
    stderr: IO[bytes] | int = subprocess.DEVNULL,
) -> subprocess.Popen[bytes]:
    """Start ``argv`` so it keeps running after this process is gone."""
    try:
        return _open_detached(argv, cwd, environment, stderr)
    except OSError as error:
        raise LaunchError(f"{argv[0]} could not be started: {error}") from error


def _process_ref(process: subprocess.Popen[bytes]) -> _ProcessRef:
    """Remember a freshly started process by number and start time."""
    return _ProcessRef(pid=process.pid, started_at=_started_at(process.pid))


def _started_at(pid: int) -> float:
    """Return the start time of ``pid``, or ``0.0`` when it is already gone."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return 0.0


def _terminate(reference: _ProcessRef, timeout: float = TERMINATE_TIMEOUT) -> bool:
    """Close the remembered process and everything it started.

    Returns whether the process was still the remembered one and got asked to
    close; survivors of the friendly request are killed. Whether it still runs
    is the one question :func:`_live_ref` answers, so what a status call calls
    stoppable is what this closes; a process that has ended and only waits to
    be reaped counts as gone for both.
    """
    try:
        process = psutil.Process(reference.pid)
        targets = [*process.children(recursive=True), process]
    except psutil.Error:
        return False
    if _live_ref(reference) is None:
        return False
    for target in targets:
        with suppress(psutil.Error):
            target.terminate()
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for survivor in alive:
        with suppress(psutil.Error):
            survivor.kill()
    return True


def _live_ref(reference: _ProcessRef | None) -> _ProcessRef | None:
    """Return ``reference`` while it still names the process it was made for."""
    if reference is None or not game_dir.process_alive(
        reference.pid, reference.started_at
    ):
        return None
    return reference


def _running_pid(
    reference: _ProcessRef | None, running: Sequence[GameProcess], kind: str
) -> int:
    """Return the process number of the running server or client, or ``0``.

    The remembered process is the fast, certain answer; the process table is
    asked for a process of ``kind`` when that one is gone, so a game hosted by
    Wine is found even after the program that started it exited.
    """
    reference = _live_ref(reference) or _running_ref(running, kind)
    return reference.pid if reference is not None else 0


def _running_ref(running: Sequence[GameProcess], kind: str) -> _ProcessRef | None:
    """Return the first scanned process of ``kind`` as a remembered process."""
    for process in running:
        if process.kind == kind:
            return _ProcessRef(pid=process.pid, started_at=process.started_at)
    return None


def _report(progress: Progress | None, step: str, percent: int, message: str) -> None:
    """Report one launch phase when the caller asked for progress."""
    if progress is not None:
        progress(step, percent, message)


def _state_file(path: Path) -> Path:
    """Return the launch state file of the installation at ``path``."""
    return path / LAUNCH_STATE_FILE_NAME


@contextmanager
def _launch_lock(path: Path) -> Generator[None]:
    """Let only one launch at a time run for the installation at ``path``.

    Raises:
        LaunchError: Another launch is already running, or the marker cannot be
            written.
    """
    lock_file = path / LAUNCH_LOCK_FILE_NAME
    try:
        os.close(os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError as error:
        raise LaunchError(
            "A launch is already in progress. Wait for it to finish."
        ) from error
    except OSError as error:
        raise LaunchError(f"{path} cannot be written: {error}") from error
    try:
        yield
    finally:
        with suppress(OSError):
            lock_file.unlink()


def _read_state(path: Path) -> _LaunchState:
    """Return the remembered launch state, empty when there is none.

    A missing, unreadable or damaged file means "nothing is running": the file
    is a hint, and the process table is the truth.
    """
    try:
        payload: Json = json.loads(_state_file(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _LaunchState()
    if not isinstance(payload, dict):
        return _LaunchState()
    profile_id = payload.get("profile_id")
    server = payload.get("server")
    failure = payload.get("failure")
    return _LaunchState(
        profile_id=profile_id if isinstance(profile_id, str) else "",
        stop_server_on_exit=payload.get("stop_server_on_exit") is not False,
        server=_reference(server),
        server_started_by_sptmod=isinstance(server, dict)
        and server.get("started_by_sptmod") is True,
        client=_reference(payload.get("client")),
        watcher=_reference(payload.get("watcher")),
        failure=failure if isinstance(failure, str) else "",
    )


def _reference(value: Json) -> _ProcessRef | None:
    """Read one remembered process, ``None`` when the entry is unusable."""
    if not isinstance(value, dict):
        return None
    pid = value.get("pid")
    started_at = value.get("started_at")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if not isinstance(started_at, int | float) or isinstance(started_at, bool):
        return None
    return _ProcessRef(pid=pid, started_at=float(started_at))


def _write_state(path: Path, state: _LaunchState) -> None:
    """Write the launch state of ``path`` in one piece.

    Raises:
        OSError: The game directory cannot be written.
    """
    payload: dict[str, object] = {
        "profile_id": state.profile_id,
        "stop_server_on_exit": state.stop_server_on_exit,
        "client": _reference_payload(state.client),
        "watcher": _reference_payload(state.watcher),
        "failure": state.failure,
    }
    server = _reference_payload(state.server)
    if server is not None:
        server["started_by_sptmod"] = state.server_started_by_sptmod
    payload["server"] = server
    descriptor, temp_name = tempfile.mkstemp(dir=path, prefix=_TEMP_PREFIX)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(payload, temp_file)
        os.replace(temp_path, _state_file(path))
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _reference_payload(reference: _ProcessRef | None) -> dict[str, object] | None:
    """Render one remembered process for the state file."""
    if reference is None:
        return None
    return {"pid": reference.pid, "started_at": reference.started_at}


def _log(path: Path, message: str) -> None:
    """Append one line to the launch log of ``path``.

    A launch must not fail because its log cannot be written, so every failure
    of the file system is swallowed here.
    """
    log_file = path / LAUNCH_LOG_FILE_NAME
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with suppress(OSError):
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
        _trim_log(log_file)


@contextmanager
def _log_stream(path: Path) -> Generator[IO[bytes] | int]:
    """Hand the error output of the game to the launch log of ``path``.

    Falls back to dropping that output when the log cannot be opened, because
    a launch must not fail over its own bookkeeping.
    """
    try:
        handle = (path / LAUNCH_LOG_FILE_NAME).open("ab")
    except OSError:
        yield subprocess.DEVNULL
        return
    try:
        yield handle
    finally:
        handle.close()


def _trim_log(log_file: Path) -> None:
    """Drop the oldest whole lines until the log fits its size limit."""
    if log_file.stat().st_size <= LAUNCH_LOG_MAX_BYTES:
        return
    _, _, kept = log_file.read_bytes()[-LAUNCH_LOG_MAX_BYTES:].partition(b"\n")
    log_file.write_bytes(kept)


def _clear_state(path: Path) -> None:
    """Forget what was running in the installation at ``path``."""
    with suppress(OSError):
        _state_file(path).unlink(missing_ok=True)
