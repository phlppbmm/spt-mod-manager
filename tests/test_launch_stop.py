"""Tests for ``sptmod.core.launch.stop_game``: what it closes, what it
leaves alone because sptmod did not start it, and what it says about both.

Every process signalled here is one the test spawned itself through the
``spawn_process`` and ``spawn_sleeper`` fixtures; nothing is taken from a scan
of the real process table.
"""

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from fixture_types import SpawnProcess, SpawnSleeper
from fixtures_launch import process_ref, write_launch_state
from launch_helpers import wait_until
from sptmod.core import game_dir
from sptmod.core.game_dir import GameProcess, GameProcessKind
from sptmod.core.launch import (
    LAUNCH_STATE_FILE_NAME,
    LaunchStatus,
    StopResult,
    launch_status,
    stop_game,
)

pytestmark = pytest.mark.timeout(5)


# --------------------------------------------------------------------------
# stop_game
# --------------------------------------------------------------------------


# A stub process that ignores the friendly request to close: it installs the
# handler first and only then writes the ready file named on its command line,
# so a test that waits for that file knows the next SIGTERM is ignored. Only a
# kill ends it, which is what makes the escalation observable without waiting
# out a real terminate timeout.
DEAF_STUB_SCRIPT = f"""#!{sys.executable}
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write("ready")
time.sleep(30)
"""


NOTHING_STARTED = "sptmod did not start a game here, so there is nothing to close."
NOTHING_LEFT = "Nothing sptmod started here is still running."
SERVER_STAYS_UP = "The SPT server was already running before sptmod, so it stays up."
STILL_PLAYING = "The game is still running; close it yourself."


def _scanned(process: subprocess.Popen[bytes], name: str, kind: GameProcessKind) -> GameProcess:
    """Describe a real throwaway process the way a process-table scan would."""
    return GameProcess(
        pid=process.pid,
        name=name,
        kind=kind,
        started_at=psutil.Process(process.pid).create_time(),
    )


def test_stop_game_is_a_noop_without_a_state_file(tmp_path: Path) -> None:
    """stop_game reports nothing to close when no launch was ever noted here.

    No state file exists, so nothing was recorded. stop_game returns both flags
    false and says so in plain words, because there is no process it may touch.
    """
    assert stop_game(tmp_path) == StopResult(False, False, NOTHING_STARTED)


def test_stop_game_leaves_a_scanned_server_and_game_it_did_not_start_alone(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_game never closes a process it only found in the process table.

    Two real throwaway processes are presented to the scan as the SPT server
    and the game while no state file exists, so nothing ties them to sptmod.
    stop_game reports nothing closed, says the game found there is still
    running, and both processes are alive afterwards: closing a server and a
    raid that belong to somebody else is the regression this pins.
    """
    server = spawn_sleeper()
    client = spawn_sleeper()
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            _scanned(server, "SPT.Server.Linux", "server"),
            _scanned(client, "EscapeFromTarkov.exe", "client"),
        ],
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, False, f"{NOTHING_STARTED} {STILL_PLAYING}")
    assert server.poll() is None
    assert client.poll() is None


def test_stop_game_leaves_a_server_that_was_already_running_before_the_launch(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """stop_game closes the game but keeps a server the launch only adopted.

    The state file records a running game and a running server marked as not
    started by sptmod, the server that was already answering when the launch
    began. stop_game closes the game, leaves that server running and says that
    it stays up, because a server sptmod adopted may serve somebody else.
    """
    server = spawn_sleeper()
    client = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        server=process_ref(server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=False),
        client=process_ref(client.pid, psutil.Process(client.pid).create_time()),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, True, f"Closed the game. {SERVER_STAYS_UP}")
    client.wait(timeout=4)
    assert server.poll() is None


def test_stop_game_reports_an_adopted_server_that_stays_up_when_nothing_was_closed(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """stop_game names the adopted server even when it closed nothing at all.

    The state file records only a server that sptmod did not start, and the
    game is already gone. Nothing is closed, and the answer still explains that
    the server keeps running instead of leaving the player wondering.
    """
    server = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        server=process_ref(server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=False),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(False, False, f"{NOTHING_LEFT} {SERVER_STAYS_UP}")
    assert server.poll() is None


def test_stop_game_closes_a_server_it_started_without_a_game(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """stop_game closes a lone server that sptmod started itself.

    The state file records a running server marked as started by sptmod and no
    game. stop_game closes the server and names only it, so the answer never
    claims a game was closed that never ran.
    """
    server = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        server=process_ref(server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(True, False, "Closed the SPT server.")
    server.wait(timeout=4)


@pytest.mark.parametrize(
    ("server_started_by_sptmod", "expected_message"),
    [
        (True, f"Closed the game and the SPT server. {STILL_PLAYING}"),
        (False, f"Closed the game. {SERVER_STAYS_UP} {STILL_PLAYING}"),
    ],
    ids=["server-sptmod-started", "server-adopted"],
)
def test_stop_game_says_a_game_left_in_the_process_table_is_still_running(
    server_started_by_sptmod: bool,
    expected_message: str,
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_game adds the note about a surviving game to what it did close.

    The state file records a running server and a running game, and a third
    throwaway process is presented to the scan as a game that outlives them.
    stop_game closes what it noted, leaves the surviving game alone and asks
    the player to close it: on Linux the game regularly outlives the program a
    launch starts, and sptmod closes nothing it did not start itself.
    """
    server = spawn_sleeper()
    client = spawn_sleeper()
    survivor = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        server=process_ref(
            server.pid,
            psutil.Process(server.pid).create_time(),
            started_by_sptmod=server_started_by_sptmod,
        ),
        client=process_ref(client.pid, psutil.Process(client.pid).create_time()),
    )
    monkeypatch.setattr(
        game_dir, "game_processes", lambda: [_scanned(survivor, "EscapeFromTarkov.exe", "client")]
    )

    result = stop_game(tmp_path)

    assert result == StopResult(server_started_by_sptmod, True, expected_message)
    client.wait(timeout=4)
    assert survivor.poll() is None


def test_stop_game_and_launch_status_agree_about_a_game_that_only_awaits_reaping(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """A recorded game that has ended counts as gone for the status and the stop.

    A real throwaway process is recorded as the game and then ended, but stays
    in the process table as an entry with no life left in it. launch_status
    must not offer to close it and stop_game must not claim to have closed it:
    a stop button offered for something that cannot be closed would do nothing
    when pressed.
    """
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    wait_until(lambda: psutil.Process(process.pid).status() == psutil.STATUS_ZOMBIE)
    write_launch_state(tmp_path, profile_id="p", client=process_ref(process.pid, started_at))

    status = launch_status(tmp_path)
    result = stop_game(tmp_path)

    assert status == LaunchStatus(
        False, False, 0, 0, "p", server_stoppable=False, client_stoppable=False
    )
    assert result == StopResult(False, False, NOTHING_LEFT)


def test_stop_game_stops_server_client_and_watcher_and_clears_the_state(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """stop_game terminates server/client/watcher and deletes state file.

    State file records all three (server with started_by_sptmod=True, client, watcher);
    all processes are running. stop_game terminates all three and deletes
    LAUNCH_STATE_FILE_NAME. Result shows server_stopped=True, client_stopped=True.
    """
    server = spawn_sleeper()
    client = spawn_sleeper()
    watcher = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        server=process_ref(server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True),
        client=process_ref(client.pid, psutil.Process(client.pid).create_time()),
        watcher=process_ref(watcher.pid, psutil.Process(watcher.pid).create_time()),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(True, True, "Closed the game and the SPT server.")
    for process in (server, client, watcher):
        process.wait(timeout=4)
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


def test_stop_game_kills_the_client_process_tree_recursively(tmp_path: Path, spawn_process: SpawnProcess) -> None:
    """stop_game terminates client and all child processes recursively.

    Client spawns a child process (sleep 30); state records the parent pid. stop_game
    terminates the parent and recursively kills its children. Both parent and child
    exit before timeout; result.client_stopped is True.
    """
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen(['sleep', '30'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(30)\n"
    )
    parent = spawn_process([sys.executable, "-c", script])
    wait_until(child_pid_file.exists)
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    write_launch_state(
        tmp_path,
        client=process_ref(parent.pid, psutil.Process(parent.pid).create_time()),
    )

    result = stop_game(tmp_path)

    assert result.client_stopped is True
    wait_until(lambda: not psutil.pid_exists(child_pid))
    parent.wait(timeout=4)


def test_stop_game_kills_a_deep_client_process_tree_down_to_the_grandchild(
    tmp_path: Path, spawn_process: SpawnProcess
) -> None:
    """stop_game closes a three-level process tree below the recorded process.

    A wrapper process starts a middle process that starts a grandchild, the
    shape a real launch has on Linux, where the game sits several levels below
    the wrapper the launch recorded; only the wrapper is written to the state
    file. stop_game must leave nothing of that tree running and report the game
    as closed, because a grandchild that survives keeps the game up while
    sptmod claims it closed it.
    """
    pid_file = tmp_path / "descendants.pid"
    middle_script = (
        "import os, subprocess, time\n"
        "g = subprocess.Popen(['sleep', '30'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()) + ' ' + str(g.pid))\n"
        "time.sleep(30)\n"
    )
    wrapper_script = (
        f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {middle_script!r}])\ntime.sleep(30)\n"
    )
    wrapper = spawn_process([sys.executable, "-c", wrapper_script])
    wait_until(lambda: pid_file.exists() and len(pid_file.read_text(encoding="utf-8").split()) == 2)
    middle_pid, grandchild_pid = (int(part) for part in pid_file.read_text(encoding="utf-8").split())
    write_launch_state(tmp_path, client=process_ref(wrapper.pid, psutil.Process(wrapper.pid).create_time()))

    result = stop_game(tmp_path)

    assert result.client_stopped is True
    wait_until(lambda: not psutil.pid_exists(grandchild_pid))
    wait_until(lambda: not psutil.pid_exists(middle_pid))
    wrapper.wait(timeout=4)


def test_stop_game_leaves_a_descendant_that_predates_the_recorded_process_alone(
    tmp_path: Path, spawn_sleeper: SpawnSleeper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop_game closes only descendants that began after the recorded process.

    Two real throwaway processes are started, the older one appearing below the
    younger, recorded one in the process tree. stop_game closes the recorded
    process and reports the game as closed, but the older process keeps
    running, because something the launch started cannot have begun before the
    process that started it: such a descendant is a stranger that inherited a
    process number, and closing it would close a program of the player's.
    """
    # A descendant older than its own parent only exists once a process number
    # has been reused, which no test can bring about on demand. The tree is
    # therefore faked for the recorded process alone, while both start times
    # and both processes are real, so the assertions below still say what
    # stop_game did rather than what this monkeypatch wrote.
    stranger = spawn_sleeper()
    stranger_started = psutil.Process(stranger.pid).create_time()
    # Start times tick every hundredth of a second, so the recorded process is
    # started a tick later to be strictly younger than the stranger.
    time.sleep(0.05)
    wrapper = spawn_sleeper()
    assert psutil.Process(wrapper.pid).create_time() > stranger_started
    write_launch_state(tmp_path, client=process_ref(wrapper.pid, psutil.Process(wrapper.pid).create_time()))
    real_children = psutil.Process.children

    def _children(self: psutil.Process, recursive: bool = False) -> list[psutil.Process]:
        if self.pid == wrapper.pid:
            return [psutil.Process(stranger.pid)]
        return real_children(self, recursive=recursive)

    with monkeypatch.context() as ctx:
        ctx.setattr(psutil.Process, "children", _children)
        result = stop_game(tmp_path)

    assert result.client_stopped is True
    wrapper.wait(timeout=4)
    assert stranger.poll() is None


def test_stop_game_tolerates_an_already_gone_process(tmp_path: Path, spawn_sleeper: SpawnSleeper) -> None:
    """stop_game handles gracefully when state records a pid that no longer exists.

    Process was killed before stop_game() called; state file records its (now-dead)
    pid. stop_game attempts termination but finds it gone, returns server_stopped=False,
    and still deletes state file. No error is raised.
    """
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    write_launch_state(tmp_path, server=process_ref(process.pid, started_at, started_by_sptmod=True))

    result = stop_game(tmp_path)

    assert result == StopResult(False, False, NOTHING_LEFT)
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


def test_stop_game_ignores_a_process_whose_start_time_no_longer_matches(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """stop_game skips pid with mismatched start time (PID reuse guard).

    State records server=process_ref(process.pid, 1.0, started_by_sptmod=True). Current
    process at that pid has a much later real create_time. start time mismatch
    proves the pid was reused; stop_game leaves it running. Result server_stopped=False.
    """
    process = spawn_sleeper()
    write_launch_state(tmp_path, server=process_ref(process.pid, 1.0, started_by_sptmod=True))

    result = stop_game(tmp_path)

    assert result.server_stopped is False
    assert process.poll() is None  # still running -- it was correctly left alone


def test_stop_game_tolerates_an_already_gone_watcher(tmp_path: Path, spawn_sleeper: SpawnSleeper) -> None:
    """stop_game handles dead watcher pid in state without raising.

    Unlike server/client (which fall back to scan), watcher pid is handed directly
    to terminate. State records a dead watcher; stop_game attempts termination,
    finds it gone, and returns gracefully. No error is raised.
    """
    # Unlike server/client, the watcher entry is handed to terminate directly
    # (no live_ref/scan fallback), so a dead watcher pid exercises
    # terminate's own "process not found" branch.
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    write_launch_state(tmp_path, watcher=process_ref(process.pid, started_at))

    result = stop_game(tmp_path)  # must not raise

    assert result == StopResult(False, False, NOTHING_STARTED)


def test_stop_game_ignores_a_watcher_whose_start_time_no_longer_matches(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """stop_game skips watcher with mismatched start time (pid reuse guard).

    State records watcher at process.pid with start time 1.0. Current process at
    that pid has a much later real create_time (pid reuse). stop_game detects the
    mismatch and leaves the process running.
    """
    process = spawn_sleeper()
    write_launch_state(tmp_path, watcher=process_ref(process.pid, 1.0))

    stop_game(tmp_path)

    assert process.poll() is None  # still running -- it was correctly left alone


def test_stop_game_kills_a_server_that_ignores_the_request_to_close(
    tmp_path: Path,
    spawn_process: SpawnProcess,
) -> None:
    """stop_game kills a process that survives the friendly request to close.

    A stub process of the test's own making ignores SIGTERM and announces that
    it does so by writing its ready file; it is recorded as the server sptmod
    started. stop_game is given a short ``terminate_timeout``, so the wait for
    a voluntary exit is over quickly and the kill that follows is the only way
    the process can end. It reports the server stopped and the process is gone
    afterwards, which pins the escalation from terminate to kill.
    """
    stub = tmp_path / "deaf-stub.py"
    stub.write_text(DEAF_STUB_SCRIPT, encoding="utf-8")
    ready_file = tmp_path / "deaf-stub.ready"
    process = spawn_process([sys.executable, str(stub), str(ready_file)])
    wait_until(ready_file.is_file)
    started_at = psutil.Process(process.pid).create_time()
    write_launch_state(tmp_path, server=process_ref(process.pid, started_at, started_by_sptmod=True))

    result = stop_game(tmp_path, terminate_timeout=0.2)

    assert result.server_stopped is True
    process.wait(timeout=4)
    assert not psutil.pid_exists(process.pid)
