"""Running one launch: the server, the sign-in and the game client."""

import json
import os
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

from sptmod.core import game_dir, sptserver
from sptmod.core.errors import (
    FikaHostUnreachableError,
    GameRunningError,
    LaunchError,
    LoginFailedError,
    ServerPortBusyError,
)
from sptmod.core.game_dir import GameLayout, GameProcess, detect_layout
from sptmod.core.launch.process import running_ref
from sptmod.core.launch.spawn import spawn_client, spawn_server, spawn_watcher
from sptmod.core.launch.state import CLIENT_MODE, LaunchState, ProcessRef, log, write_state
from sptmod.core.models import Json, Progress

LAUNCH_LOCK_FILE_NAME: Final = ".sptmod-launch.lock"
READY_TIMEOUT: Final = 300.0
POLL_SECONDS: Final = 1.0
_WAIT_PERCENT_START: Final = 10
_WAIT_PERCENT_SPAN: Final = 60
class LaunchStep(StrEnum):
    """The phases a launch reports progress for."""

    MODSET = "modset"
    SERVER = "server"
    WAITING = "waiting"
    HOST = "host"
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
def launch_profile(
    path: Path,
    profile_id: str,
    stop_server_on_exit: bool = True,
    progress: Progress | None = None,
    *,
    fika_host: str = "",
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

    With ``fika_host`` the game is played on another player's server instead:
    nothing is started locally, the profile and its player name come from that
    server, and only the game itself runs here.

    Args:
        path: The game directory.
        profile_id: The profile to play, named by its file name without the
            extension.
        stop_server_on_exit: Close the server when the game closes.
        progress: Optional callback for the four phases of the launch.
        fika_host: Base URL of the Fika host to play on; empty to play on the
            server of this installation.
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
        FikaHostUnreachableError: The Fika host does not answer as an SPT
            server.
        LoginFailedError: The server does not know the profile.
        LauncherSettingsError: The settings of the official SPT launcher are
            missing or unusable.
        LaunchError: A program is missing, could not be started, the server
            did not answer in time, the host knows no such profile, another
            launch is already in progress, or what was started could not be
            noted in the game directory.
    """
    layout = detect_layout(path)
    with _launch_lock(path):
        return _launch(
            path,
            layout,
            profile_id,
            stop_server_on_exit,
            progress,
            fika_host=fika_host,
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
    fika_host: str,
    ready_timeout: float,
    poll_seconds: float,
    request_timeout: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> LaunchResult:
    """Run one launch while the launch lock is held."""
    running = game_dir.game_processes()
    _refuse_running_client(running)
    if fika_host:
        return _launch_on_host(
            path,
            layout,
            profile_id,
            stop_server_on_exit,
            progress,
            fika_host=fika_host,
            request_timeout=request_timeout,
        )
    username, nickname = _profile_login(layout, profile_id)
    # The profile id is the login token, so only the player name is logged.
    log(path, f"Starting the game for {nickname}.")

    base_url = sptserver.server_base_url(layout)
    answer = sptserver.probe(base_url, request_timeout)
    if answer == "foreign":
        raise ServerPortBusyError(_port_busy_message(base_url))

    server_started = answer == "free"
    server: ProcessRef | None
    if server_started:
        _report(progress, LaunchStep.SERVER, 5, "Starting the server")
        server = spawn_server(layout)
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
        log(path, "The SPT server was already answering, so it is reused.")
        server = running_ref(running, "server")

    _report(progress, LaunchStep.LOGIN, 80, f"Signing in as {nickname}")
    if not sptserver.login(base_url, username, request_timeout):
        log(path, f"The SPT server refused to sign {nickname} in.")
        raise LoginFailedError(
            f"The SPT server does not know the profile {nickname}. "
            f"Start the official SPT launcher once to create it."
        )

    log(path, f"The SPT server signed {nickname} in.")
    _report(progress, LaunchStep.CLIENT, 95, "Starting the game")
    client = spawn_client(layout, profile_id, base_url)

    _remember(
        path,
        LaunchState(
            profile_id=profile_id,
            stop_server_on_exit=stop_server_on_exit,
            server=server,
            server_started_by_sptmod=server_started,
            client=client,
        ),
    )
    _report(progress, LaunchStep.DONE, 100, "The game is starting")
    return LaunchResult(
        profile_id=profile_id,
        nickname=nickname,
        server_pid=server.pid if server is not None else 0,
        client_pid=client.pid,
        server_started=server_started,
    )


def _launch_on_host(
    path: Path,
    layout: GameLayout,
    profile_id: str,
    stop_server_on_exit: bool,
    progress: Progress | None,
    *,
    fika_host: str,
    request_timeout: float,
) -> LaunchResult:
    """Start the game on the server of another player.

    Nothing is started here besides the game: the profile, its player name and
    the sign-in all belong to the host, and the game is pointed at it.
    """
    _report(progress, LaunchStep.HOST, 5, f"Contacting the host at {fika_host}")
    log(path, f"Connecting to the host at {fika_host}.")
    answer = sptserver.probe(fika_host, request_timeout)
    if answer != "spt":
        raise FikaHostUnreachableError(_host_message(answer, fika_host))

    _report(progress, LaunchStep.HOST, 40, "Reading the profiles on the host")
    profile = _host_profile(fika_host, profile_id, request_timeout)

    _report(progress, LaunchStep.LOGIN, 80, f"Signing in as {profile.nickname}")
    if not sptserver.login(fika_host, profile.username, request_timeout):
        raise LoginFailedError(
            f"The host at {fika_host} did not accept the profile "
            f"{profile.nickname}. Profiles that need a password cannot be used."
        )
    log(path, f"The host signed {profile.nickname} in.")

    _report(progress, LaunchStep.CLIENT, 95, "Starting the game")
    client = spawn_client(layout, profile_id, fika_host)
    _remember(
        path,
        LaunchState(
            profile_id=profile_id,
            stop_server_on_exit=stop_server_on_exit,
            client=client,
            mode=CLIENT_MODE,
        ),
    )
    _report(progress, LaunchStep.DONE, 100, "The game is starting")
    return LaunchResult(
        profile_id=profile_id,
        nickname=profile.nickname,
        server_pid=0,
        client_pid=client.pid,
        server_started=False,
    )


def _host_profile(
    base_url: str, profile_id: str, request_timeout: float
) -> sptserver.RemoteProfile:
    """Return the profile ``profile_id`` among the ones the host offers.

    Raises:
        LaunchError: The host offers no profile with that id.
    """
    for profile in sptserver.host_profiles(base_url, request_timeout):
        if profile.profile_id == profile_id:
            return profile
    raise LaunchError(
        f"The host at {base_url} has no profile with this id. "
        f"Pick one of the host's profiles."
    )


def _host_message(answer: sptserver.PortState, base_url: str) -> str:
    """Return why the host at ``base_url`` cannot be played on."""
    if answer == "free":
        return (
            f"No SPT server answers at {base_url}. Ask the host to start "
            f"their server, then try again."
        )
    if answer == "timeout":
        return (
            f"The SPT server at {base_url} did not answer in time. Check the "
            f"address and the connection to the host."
        )
    return (
        f"Something at {base_url} answered, but it is not an SPT server. "
        f"Check the address."
    )


def _remember(path: Path, state: LaunchState) -> None:
    """Note what a launch started and hand it over to its own watcher.

    Raises:
        LaunchError: What was started could not be noted in the game directory.
    """
    try:
        write_state(path, state)
        write_state(path, replace(state, watcher=spawn_watcher(path)))
    except OSError as error:
        server = state.server.pid if state.server is not None else 0
        client = state.client.pid if state.client is not None else 0
        raise LaunchError(
            f"The SPT server (process {server}) and the "
            f"game (process {client}) are running, but sptmod cannot note "
            f"that in {path}: {error}. Close them yourself when you are done."
        ) from error
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
    server: ProcessRef,
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
    log(
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
def _report(progress: Progress | None, step: str, percent: int, message: str) -> None:
    """Report one launch phase when the caller asked for progress."""
    if progress is not None:
        progress(step, percent, message)

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
