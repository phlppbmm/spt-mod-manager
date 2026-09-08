"""Waiting for the game of one launch to close, and cleaning up after it."""

import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Final

import psutil

from sptmod.core import game_dir
from sptmod.core.launch.process import running_ref, terminate
from sptmod.core.launch.state import (
    CLIENT_MODE,
    LaunchState,
    ProcessRef,
    clear_state,
    log,
    read_state,
    write_state,
)

WATCH_POLL_SECONDS: Final = 3.0
WATCH_GRACE_SECONDS: Final = 120.0
CLIENT_NEVER_STARTED: Final = "The game did not start."
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
    state = read_state(path)
    client = state.client
    if client is None:
        return
    if not _await_client(
        client,
        poll_seconds=poll_seconds,
        grace_seconds=grace_seconds,
        sleep=sleep,
        clock=clock,
    ):
        log(
            path,
            f"{CLIENT_NEVER_STARTED} The process the launch started is gone "
            f"and no game appeared within {int(grace_seconds)} seconds.",
        )
        _log_process_table(path)
        _stop_watched_server(path, state)
        with suppress(OSError):
            write_state(path, replace(state, failure=CLIENT_NEVER_STARTED))
        return
    log(path, "The game is running.")
    while _client_running(client):
        state = _adopt(path, state, client)
        sleep(poll_seconds)
    log(path, "The game and the process the launch started are both gone.")
    _stop_watched_server(path, state)
    clear_state(path)


def _await_client(
    client: ProcessRef,
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


def _client_running(client: ProcessRef) -> bool:
    """Return whether a game is running, remembered or found by name."""
    if _spawned_alive(client):
        return True
    return running_ref(game_dir.game_processes(), "client") is not None


def _spawned_alive(client: ProcessRef) -> bool:
    """Return whether the process the launch started for the game still runs.

    Deliberately weaker than :func:`live_ref`: the bare process number counts
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


def _adopt(path: Path, state: LaunchState, client: ProcessRef) -> LaunchState:
    """Note the game the watcher found running for this launch.

    Happens at most once per launch, on any poll and while the process the
    launch started may well still be alive -- that process often dies between
    two polls. From then on the game is remembered under its own number, so it
    can still be closed when the process that started it is gone and the game
    hangs off nothing.
    """
    if state.adopted_client is not None:
        return state
    adopted = _adopted_client(client)
    if adopted is None:
        return state
    log(
        path,
        "The game now runs on its own and was noted, so it can still be closed.",
    )
    updated = replace(state, adopted_client=adopted)
    with suppress(OSError):
        write_state(path, updated)
    return updated


def _adopted_client(recorded: ProcessRef) -> ProcessRef | None:
    """Return the running game that belongs to the launch of ``recorded``.

    Only a game that began no earlier than the process the launch started can
    have come from it; an older one was there before and belongs to somebody
    else. A start time of ``0.0`` could not be read at spawn time and orders
    nothing, so nothing is adopted then.
    """
    if recorded.started_at <= 0.0:
        return None
    found = running_ref(game_dir.game_processes(), "client")
    if found is None or found.started_at < recorded.started_at:
        return None
    return found


def _stop_watched_server(path: Path, state: LaunchState) -> None:
    """Close the server of one launch when that launch asked for it."""
    if state.mode == CLIENT_MODE:
        log(
            path,
            "The game was played on another player's server; "
            "nothing to close here.",
        )
        return
    if (
        state.stop_server_on_exit
        and state.server_started_by_sptmod
        and state.server is not None
    ):
        terminate(state.server)
        log(path, "The SPT server was closed.")
        return
    log(path, "The SPT server was left running.")


def _log_process_table(path: Path) -> None:
    """Note what the process table held when no game could be found."""
    games = ", ".join(
        f"{process.name} ({process.kind}, process {process.pid})"
        for process in game_dir.game_processes()
    )
    log(path, f"SPT processes seen: {games or 'none'}.")
    sightings = ", ".join(
        f"{sighting.name} ({sighting.command}, process {sighting.pid})"
        for sighting in game_dir.spt_process_sightings()
    )
    log(path, f"Processes mentioning SPT: {sightings or 'none'}.")
