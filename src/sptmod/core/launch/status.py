"""Reporting what of one installation is running, and closing it again."""

from dataclasses import dataclass
from pathlib import Path

from sptmod.core import game_dir
from sptmod.core.launch.process import TERMINATE_TIMEOUT, live_ref, running_pid, terminate
from sptmod.core.launch.state import CLIENT_MODE, LaunchState, ProcessRef, clear_state, read_state


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
def launch_status(path: Path) -> LaunchStatus:
    """Report whether the server and the game of ``path`` are running.

    Reads the remembered processes and the process table, so a server or game
    started outside sptmod is reported as well. A game played on another
    player's server reports no server of its own: the one on this machine, if
    any, belongs to somebody else. Never fails: an installation nothing is
    known about simply reports nothing running. Only a process sptmod started
    here is stoppable; one found by its name alone, and a server that was
    already running, are reported and left alone.
    """
    state = read_state(path)
    running = game_dir.game_processes()
    server = (
        0
        if state.mode == CLIENT_MODE
        else running_pid(state.server, running, "server")
    )
    client = running_pid(_live_client(state), running, "client")
    return LaunchStatus(
        server_running=server != 0,
        client_running=client != 0,
        server_pid=server,
        client_pid=client,
        profile_id=state.profile_id,
        server_stoppable=state.server_started_by_sptmod
        and live_ref(state.server) is not None,
        client_stoppable=_live_client(state) is not None,
    )
def stop_game(path: Path, *, terminate_timeout: float = TERMINATE_TIMEOUT) -> StopResult:
    """Close the game and the SPT server sptmod started at ``path``.

    Closes the watcher first so it cannot act on a half-stopped installation,
    then the game with everything it started, then the server. Only a process
    sptmod both noted at launch time and started itself is closed, and only
    while the number and the start time noted with it still name that very
    process. Anything else is left alone, however plainly it shows up in the
    process table: it belongs to somebody else, the server that was already
    answering when the launch began included. A game played on another
    player's server closes alone: the server there belongs to that player and
    was never noted here.

    Args:
        path: The game directory.
        terminate_timeout: Seconds a process has to close on its own before it
            is killed.
    """
    state = read_state(path)
    if state.watcher is not None:
        terminate(state.watcher, terminate_timeout)
    client_stopped = _stop_client(state, terminate_timeout)
    server_stopped = (
        state.server is not None
        and state.server_started_by_sptmod
        and terminate(state.server, terminate_timeout)
    )
    clear_state(path)
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


def _stop_client(state: LaunchState, terminate_timeout: float) -> bool:
    """Close the game of one launch, whichever of its processes still runs.

    Both the process the launch started and the game that was adopted from it
    are asked to close, because the game can leave the tree of a wrapper that
    is still alive; neither closes the other.
    """
    spawned = state.client is not None and terminate(state.client, terminate_timeout)
    adopted = state.adopted_client is not None and terminate(
        state.adopted_client, terminate_timeout
    )
    return spawned or adopted


def _live_client(state: LaunchState) -> ProcessRef | None:
    """Return the game process of one launch that is still running."""
    return live_ref(state.client) or live_ref(state.adopted_client)


def _stop_message(
    state: LaunchState,
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
