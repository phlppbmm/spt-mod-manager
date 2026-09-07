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
from enum import StrEnum
from pathlib import Path
from typing import Final

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
READY_TIMEOUT: Final = 300.0
POLL_SECONDS: Final = 1.0
WATCH_POLL_SECONDS: Final = 3.0
WATCH_GRACE_SECONDS: Final = 120.0
CLIENT_NEVER_STARTED: Final = "The game did not start."
TERMINATE_TIMEOUT: Final = 10.0
CLIENT_ARGS_HEAD: Final[tuple[str, ...]] = ("-force-gfx-jobs", "native")

_TEMP_PREFIX: Final = ".sptmod-launch-"
_WAIT_PERCENT_START: Final = 10
_WAIT_PERCENT_SPAN: Final = 60
_GAMEMODE_COMMAND: Final = "gamemoderun"
# The official launcher sets this for the whole process on Linux and the game
# inherits it, so a launch from sptmod runs in the same environment.
_NVIDIA_SYNC_ENV: Final = "__NV_DISABLE_EXPLICIT_SYNC"


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
    profile the last launch used and is empty when that is unknown.
    """

    server_running: bool
    client_running: bool
    server_pid: int
    client_pid: int
    profile_id: str


@dataclass(frozen=True)
class StopResult:
    """What stopping the game closed."""

    server_stopped: bool
    client_stopped: bool


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
        server = _running_ref(running, "server")

    _report(progress, LaunchStep.LOGIN, 80, f"Signing in as {nickname}")
    if not sptserver.login(base_url, username, request_timeout):
        raise LoginFailedError(
            f"The SPT server does not know the profile {nickname}. "
            f"Start the official SPT launcher once to create it."
        )

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
    nothing is known about simply reports nothing running.
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
    )


def stop_game(path: Path) -> StopResult:
    """Close the game and the SPT server of the installation at ``path``.

    Closes the watcher first so it cannot act on a half-stopped installation,
    then the game with everything it started, then the server. A server or
    game that is running without being remembered is found in the process
    table and closed as well; every game process found there is closed, not
    only the first, because a game hosted by Wine shows up next to the
    program that started it.
    """
    state = _read_state(path)
    running = game_dir.game_processes()
    if state.watcher is not None:
        _terminate(state.watcher)
    server = _live_ref(state.server) or _running_ref(running, "server")
    client_stopped = False
    for client in _client_refs(state.client, running):
        client_stopped = _terminate(client) or client_stopped
    server_stopped = server is not None and _terminate(server)
    _clear_state(path)
    return StopResult(server_stopped=server_stopped, client_stopped=client_stopped)


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
    while the game keeps running. When no game shows up within
    ``grace_seconds`` and the started process is gone too, the launch failed:
    the server is closed as it would be after a game, and the reason is
    written to the state file. Otherwise the server is closed once the last
    game process is gone, if the launch asked for it and sptmod started that
    server; the remembered state is dropped either way.

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
        _stop_watched_server(state)
        with suppress(OSError):
            _write_state(path, replace(state, failure=CLIENT_NEVER_STARTED))
        return
    while _client_running(state.client):
        sleep(poll_seconds)
    _stop_watched_server(state)
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


def _client_running(client: _ProcessRef | None) -> bool:
    """Return whether a game is running, remembered or found by name."""
    return _running_pid(client, game_dir.game_processes(), "client") != 0


def _stop_watched_server(state: _LaunchState) -> None:
    """Close the server of one launch when that launch asked for it."""
    if (
        state.stop_server_on_exit
        and state.server_started_by_sptmod
        and state.server is not None
    ):
        _terminate(state.server)


def tokenize_launch_settings(text: str) -> list[str]:
    """Split launch settings into tokens the way the official launcher does.

    Whitespace separates tokens outside quotes; a double quote toggles quoting
    and is dropped; a quote that is never closed ends its token at the end of
    the text.
    """
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    started = False
    for character in text:
        if character == '"':
            quoted = not quoted
            started = True
        elif character.isspace() and not quoted:
            if started:
                tokens.append("".join(current))
                current.clear()
                started = False
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
    _report(progress, LaunchStep.WAITING, _WAIT_PERCENT_START, "Waiting for the server")
    while True:
        answer = sptserver.probe(base_url, request_timeout)
        if answer == "spt":
            return
        if answer == "foreign":
            raise ServerPortBusyError(_port_busy_message(base_url))
        if not game_dir.process_alive(server.pid, server.started_at):
            raise LaunchError(
                "The SPT server stopped right after it was started. "
                "Start it once yourself to see what it reports."
            )
        elapsed = clock() - started
        if elapsed >= ready_timeout:
            raise LaunchError(
                f"The SPT server did not answer within {int(ready_timeout)} "
                f"seconds. Start it once yourself to see what it reports."
            )
        percent = _WAIT_PERCENT_START + int(
            _WAIT_PERCENT_SPAN * elapsed / ready_timeout
        )
        _report(progress, LaunchStep.WAITING, percent, "Waiting for the server")
        sleep(poll_seconds)


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
    return _spawn_detached(
        [str(binary)], layout.root / layout.runtime_dir, dict(os.environ)
    )


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
        return _spawn_detached(
            [str(executable), *arguments], layout.root, dict(os.environ)
        )
    settings = _linux_settings(layout)
    environment = dict(os.environ)
    # The official launcher points Wine at the ``pfx`` directory inside the prefix folder.
    environment["WINEPREFIX"] = str(Path(settings.prefix_path) / "pfx")
    environment["PROTONPATH"] = settings.proton_version
    environment[_NVIDIA_SYNC_ENV] = "1"
    extra: list[str] = []
    tokens = [settings.default_env] if settings.default_env else []
    tokens += tokenize_launch_settings(settings.launch_settings)
    for token in tokens:
        separator = token.find("=")
        if token.startswith("-") or separator <= 0:
            extra.append(token)
        else:
            environment[token[:separator]] = token[separator + 1 :].strip('"')
    argv = [settings.umu_path, game_dir.CLIENT_EXE_NAME, *arguments, *extra]
    if settings.game_mode:
        argv.insert(0, _GAMEMODE_COMMAND)
    return _spawn_detached(argv, layout.root, environment)


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
    return _spawn_detached(
        [sys.executable, "-m", "sptmod.watch", str(path)], path, dict(os.environ)
    )


def _open_detached(
    argv: Sequence[str], cwd: Path, environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    """Start ``argv`` detached from this process and return the raw handle.

    The child gets its own session (or, on Windows, its own process group and
    no console), so it neither receives the signals of the caller nor leaves a
    window behind, and all three of its streams are closed. The handle is
    deliberately not closed: the process must outlive this one.
    """
    if sys.platform == "win32":
        return subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _spawn_detached(
    argv: Sequence[str], cwd: Path, environment: dict[str, str]
) -> _ProcessRef:
    """Start ``argv`` so it keeps running after this process is gone."""
    try:
        process = _open_detached(argv, cwd, environment)
    except OSError as error:
        raise LaunchError(f"{argv[0]} could not be started: {error}") from error
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
    close; survivors of the friendly request are killed.
    """
    try:
        process = psutil.Process(reference.pid)
        if process.create_time() != reference.started_at:
            return False
        targets = [*process.children(recursive=True), process]
    except psutil.Error:
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


def _client_refs(
    client: _ProcessRef | None, running: Sequence[GameProcess]
) -> list[_ProcessRef]:
    """Return every running game process, remembered one and scanned ones."""
    references: dict[int, _ProcessRef] = {}
    live = _live_ref(client)
    if live is not None:
        references[live.pid] = live
    for process in running:
        if process.kind == "client":
            references.setdefault(
                process.pid,
                _ProcessRef(pid=process.pid, started_at=process.started_at),
            )
    return list(references.values())


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


def _clear_state(path: Path) -> None:
    """Forget what was running in the installation at ``path``."""
    with suppress(OSError):
        _state_file(path).unlink(missing_ok=True)
