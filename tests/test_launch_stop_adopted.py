"""Tests for closing and reporting the game a launch noted running on its own.

On Linux the program a launch starts only hosts the game: when that program
dies while the game keeps running, the game hangs off nothing and is only
reachable through the ``adopted_client`` entry the watcher wrote into the
launch state file. These tests drive ``stop_game`` and ``launch_status``
against that entry with real throwaway processes -- two independent ones, one
recorded as the process the launch started and one recorded as the game --
which is the shape the state file has in that situation. No test starts a real
SPT server or game.
"""

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
import pytest

from fixture_types import SpawnProcess, SpawnSleeper
from fixtures_launch import process_ref, write_launch_state
from sptmod.core.launch import (
    LAUNCH_STATE_FILE_NAME,
    LaunchStatus,
    StopResult,
    launch_status,
    stop_game,
)

pytestmark = pytest.mark.timeout(5)

CLOSED_THE_GAME = "Closed the game."
NOTHING_LEFT = "Nothing sptmod started here is still running."


def _wait_until(predicate: Callable[[], bool], timeout: float = 4.0) -> None:
    """Poll ``predicate`` every 20 ms until it holds or ``timeout`` runs out.

    Raises ``AssertionError`` when the deadline passes first, so a process that
    was asked to close but never did fails the test instead of hanging it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was never met")


def _live_ref(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    """Note a running process the way a launch does: number and real start time."""
    return process_ref(process.pid, psutil.Process(process.pid).create_time())


def _gone_ref(spawn: SpawnSleeper) -> dict[str, Any]:
    """Note a process, then end it: the program a launch started and lost."""
    process = spawn()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    return process_ref(process.pid, started_at)


def _state_file(root: Path) -> Path:
    """Return the launch state file of the game directory at ``root``."""
    return root / LAUNCH_STATE_FILE_NAME


def test_stop_game_closes_the_game_that_outlived_the_process_it_started(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """The noted game is closed even though the program the launch started is gone.

    The state file records a process that has ended as the one the launch
    started and a second, running process as the game noted afterwards.
    stop_game must close that second process, say the game was closed and drop
    the state file, because without this the player is left with a game
    nothing can reach and a stop button that does nothing.
    """
    game = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        client=_gone_ref(spawn_sleeper),
        adopted_client=_live_ref(game),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, True, CLOSED_THE_GAME)
    game.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_stop_game_closes_what_the_noted_game_itself_started(
    tmp_path: Path, spawn_sleeper: SpawnSleeper, spawn_process: SpawnProcess
) -> None:
    """Everything below the noted game is closed with it.

    The noted game starts a child of its own and reports its number through a
    file. stop_game must leave neither of the two running, because a surviving
    child keeps the game up while sptmod claims to have closed it -- the same
    promise that holds for the process a launch started itself.
    """
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen(['sleep', '5'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(30)\n"
    )
    game = spawn_process([sys.executable, "-c", script])
    _wait_until(child_pid_file.exists)
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    write_launch_state(
        tmp_path,
        profile_id="p",
        client=_gone_ref(spawn_sleeper),
        adopted_client=_live_ref(game),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, True, CLOSED_THE_GAME)
    _wait_until(lambda: not psutil.pid_exists(child_pid))
    game.wait(timeout=4)


def test_stop_game_leaves_a_noted_game_whose_start_time_no_longer_matches(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """A reused process number is not closed just because it was noted once.

    The state file notes the running process under a start time of 1.0, which
    no process really has, so the number now belongs to somebody else.
    stop_game must report nothing closed and leave that process running: the
    note is a hint, and closing a stranger's program on a reused number is
    worse than leaving a game open.
    """
    stranger = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        client=_gone_ref(spawn_sleeper),
        adopted_client=process_ref(stranger.pid, 1.0),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, False, NOTHING_LEFT)
    assert stranger.poll() is None  # still running -- it was correctly left alone


def test_stop_game_reports_nothing_left_when_both_noted_processes_are_gone(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """Nothing is claimed closed when neither noted process is still alive.

    The program the launch started and the game noted afterwards have both
    ended. stop_game must say in plain words that nothing of this launch is
    running rather than report a game it did not close, and still drop the
    state file so the next launch starts from nothing.
    """
    write_launch_state(
        tmp_path,
        profile_id="p",
        client=_gone_ref(spawn_sleeper),
        adopted_client=_gone_ref(spawn_sleeper),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, False, NOTHING_LEFT)
    assert not _state_file(tmp_path).exists()


def test_launch_status_reports_the_noted_game_as_running_and_stoppable(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """The status names the noted game while the process the launch started is gone.

    The state file records an ended process as the one the launch started and
    a running one as the game noted afterwards. The status must name that
    running number and offer to close it, because a stop button hidden here
    would strand the player with a game the manager can in fact still close.
    """
    game = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        client=_gone_ref(spawn_sleeper),
        adopted_client=_live_ref(game),
    )

    status = launch_status(tmp_path)

    assert status == LaunchStatus(
        server_running=False,
        client_running=True,
        server_pid=0,
        client_pid=game.pid,
        profile_id="p",
        server_stoppable=False,
        client_stoppable=True,
    )


def test_stop_game_closes_a_game_noted_by_a_state_file_without_the_new_entry(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """A state file from before the noted game existed still stops as it always did.

    The file is written without the ``adopted_client`` entry at all, which is
    what a launch left behind before the watcher started noting the game.
    stop_game must close the process the launch started and say so, because an
    update must not strand the game of a launch that was already running.
    """
    game = spawn_sleeper()
    payload = {
        "profile_id": "p",
        "stop_server_on_exit": True,
        "server": None,
        "client": _live_ref(game),
        "watcher": None,
        "mode": "",
    }
    _state_file(tmp_path).write_text(json.dumps(payload), encoding="utf-8")

    result = stop_game(tmp_path)

    assert result == StopResult(False, True, CLOSED_THE_GAME)
    game.wait(timeout=4)
    assert not _state_file(tmp_path).exists()
