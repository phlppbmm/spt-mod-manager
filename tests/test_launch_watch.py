"""Tests for ``sptmod.core.launch.watch_game`` and the ``python -m
sptmod.watch`` process entry that runs it.

Uses real throwaway processes (never the real game) so ``psutil`` sees a
genuine process table: one of them is a copy of ``sleep`` carrying the game's
own file name, which is what the watcher's name scan looks for. The poll
cadence and the clock are always injected so no test waits on a real clock.
"""

import json
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
import pytest

from conftest import process_ref, write_launch_state
from fixture_types import SpawnProcess, SpawnSleeper
from sptmod.core import game_dir
from sptmod.core.game_dir import CLIENT_EXE_NAME, SERVER_LINUX_NAME, GameProcess
from sptmod.core.launch import (
    CLIENT_NEVER_STARTED,
    LAUNCH_LOG_FILE_NAME,
    LAUNCH_STATE_FILE_NAME,
    launch_status,
    watch_game,
)
from sptmod.watch import USAGE, main

pytestmark = pytest.mark.timeout(5)


@pytest.fixture(autouse=True)
def own_processes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Narrow the real process scan to the processes this test started.

    These tests drive ``watch_game`` against the genuine process table, and a
    developer machine may be running SPT for real while they run, which would
    make every "no game is running" case describe that game instead of the
    test. Every process a test here starts is a descendant of the test
    process, so keeping only those leaves the real scan doing the real
    classification while it can only see what the test put there.
    """
    scan = game_dir.game_processes

    def own_processes() -> list[GameProcess]:
        mine = {child.pid for child in psutil.Process(os.getpid()).children(recursive=True)}
        return [process for process in scan() if process.pid in mine]

    monkeypatch.setattr(game_dir, "game_processes", own_processes)


def _live_ref(process: subprocess.Popen[bytes], *, started_by_sptmod: bool | None = None) -> dict[str, Any]:
    """Build a process reference from a running Popen, capturing its real start time.

    Queries psutil for the process's create_time and calls _ref with the pid and
    timestamp. Optional started_by_sptmod flag is passed through.
    """
    return process_ref(
        process.pid,
        psutil.Process(process.pid).create_time(),
        started_by_sptmod=started_by_sptmod,
    )


def _dead_ref(spawn: SpawnSleeper) -> dict[str, Any]:
    """Spawn a process, note it and end it: a launch process that is gone."""
    process = spawn()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    return process_ref(process.pid, started_at)


@pytest.fixture
def spawn_client(
    tmp_path_factory: pytest.TempPathFactory,
    spawn_process: SpawnProcess,
) -> SpawnSleeper:
    """Spawn processes the real name scan reports as the running game.

    A copy of ``sleep`` under the game's file name, so ``psutil`` reports that
    name and ``game_dir.game_processes`` classifies it as a client -- no
    monkeypatching of the process table anywhere in the watcher flow.
    """
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    executable = tmp_path_factory.mktemp("game") / CLIENT_EXE_NAME
    shutil.copy(sleep_bin, executable)

    def _spawn() -> subprocess.Popen[bytes]:
        return spawn_process([str(executable), "30"])

    return _spawn


@pytest.fixture
def spawn_server(
    tmp_path_factory: pytest.TempPathFactory,
    spawn_process: SpawnProcess,
) -> SpawnSleeper:
    """Spawn processes the real name scan reports as the running SPT server.

    A copy of ``sleep`` under the server's file name, the server counterpart
    of :func:`spawn_client`, so a launch log can be asserted to name a real
    scanned process.
    """
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    executable = tmp_path_factory.mktemp("server") / SERVER_LINUX_NAME
    shutil.copy(sleep_bin, executable)

    def _spawn() -> subprocess.Popen[bytes]:
        return spawn_process([str(executable), "30"])

    return _spawn


def _log_text(root: Path) -> str:
    """Return the launch log of a game directory, empty when there is none."""
    log_file = root / LAUNCH_LOG_FILE_NAME
    return log_file.read_text(encoding="utf-8") if log_file.exists() else ""


def _ticking(
    on_tick: Callable[[int], None],
) -> tuple[Callable[[float], None], Callable[[], float]]:
    """Return a sleep/clock pair that moves a fake clock instead of waiting.

    ``on_tick`` receives the number of the sleep that just happened, which is
    where a test starts or ends its throwaway processes.
    """
    now = 0.0
    tick = 0

    def _sleep(seconds: float) -> None:
        nonlocal now, tick
        tick += 1
        now += seconds
        on_tick(tick)

    def _clock() -> float:
        return now

    return _sleep, _clock


def _nothing(_tick: int) -> None:
    """No-op callback for _ticking when test doesn't start/stop processes per cycle.

    Ignores the tick count and does nothing; used where _ticking's on_tick is needed
    but no process lifecycle changes happen.
    """
    return None


def _state_file(root: Path) -> Path:
    """Return path to LAUNCH_STATE_FILE_NAME in the given root directory.

    Simple accessor for the state file path; makes assertions about state file
    existence/content more readable.
    """
    return root / LAUNCH_STATE_FILE_NAME


# --------------------------------------------------------------------------
# watch_game: immediate-return siblings (state file never cleared).
# --------------------------------------------------------------------------


def test_watch_game_returns_immediately_with_no_state_file(tmp_path: Path) -> None:
    """watch_game returns immediately without polling when state file is absent.

    No state file → no launch recorded → nothing to watch. Monkeypatched sleep
    fails test if called. watch_game returns without touching filesystem.
    """
    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))

    assert not _state_file(tmp_path).exists()


def test_watch_game_returns_immediately_with_a_malformed_state_file(
    tmp_path: Path,
) -> None:
    """watch_game returns immediately when state file JSON is unparseable.

    State file contains "not json" (not valid JSON); watch_game fails to parse it,
    returns without polling (sleep not called), and leaves the file byte-identical.
    """
    _state_file(tmp_path).write_text("not json", encoding="utf-8")

    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))

    assert _state_file(tmp_path).read_text(encoding="utf-8") == "not json"


def test_watch_game_returns_immediately_with_no_client_recorded(tmp_path: Path) -> None:
    """watch_game returns immediately when state records server but no client.

    State file has server ref but client=None; no client process to wait for.
    watch_game skips polling (sleep not called) and leaves state file untouched.
    """
    write_launch_state(tmp_path, profile_id="p", server=process_ref(1, 1.0, started_by_sptmod=True))

    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))

    assert _state_file(tmp_path).exists()  # left untouched, not cleared


# --------------------------------------------------------------------------
# watch_game: normal flow -- waits for the game, then decides on the server.
# --------------------------------------------------------------------------


def _watch_until_client_exits(tmp_path: Path, client: subprocess.Popen[bytes]) -> None:
    """Run the watcher with a fake clock that kills the client after the first poll."""

    def _on_tick(tick: int) -> None:
        if tick == 1:
            client.kill()
            client.wait(timeout=4)

    sleep, clock = _ticking(_on_tick)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=30.0, sleep=sleep, clock=clock)


def test_watch_game_stops_the_server_when_it_started_it_and_was_asked_to(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """watch_game terminates server after client exits when stop_server_on_exit=true.

    Server marked started_by_sptmod=True, stop_server_on_exit=True; client is running.
    Watcher polls until client exits, then terminates server and clears state.
    Server process exits before timeout.
    """
    server = spawn_sleeper()
    client = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_live_ref(client),
    )

    _watch_until_client_exits(tmp_path, client)

    server.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_watch_game_follows_the_game_by_name_when_the_recorded_pid_is_gone(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """watch_game scans for game by name when recorded client pid is dead.

    State records dead client pid (wrapper process exited); scan finds EscapeFromTarkov
    running (real game). watch_game waits for the scanned process; when client exits,
    server (started_by_sptmod=True) is terminated and state cleared.
    """
    # What a Linux launch looks like: the process sptmod spawned was only the
    # wrapper and is already gone, while the game itself runs under Wine.
    server = spawn_sleeper()
    client = spawn_client()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    server.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_watch_game_waits_for_the_game_to_appear_before_watching_it(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """watch_game waits within grace_seconds for game to appear by name before monitoring.

    State records dead client pid. Grace period is 30s; game appears on tick 2
    (at 6s elapsed) within the grace. watch_game waits for it to exit, stops
    the server, clears state.
    """
    server = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )
    started: list[subprocess.Popen[bytes]] = []

    def _on_tick(tick: int) -> None:
        if tick == 2:  # the game shows up well inside the grace period
            started.append(spawn_client())
        if tick == 4:
            started[0].kill()
            started[0].wait(timeout=4)

    sleep, clock = _ticking(_on_tick)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=30.0, sleep=sleep, clock=clock)

    server.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_watch_game_keeps_waiting_while_the_recorded_process_is_still_alive(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """watch_game waits for recorded process even after grace period expires if it's still alive.

    Grace period is 0s (expired immediately), no game name appears in scan, but
    recorded client pid is still alive. watch_game waits for it to exit rather
    than giving up at grace expiry.
    """
    # Nothing carries the game's name, the grace period is over from the
    # first look -- but the process the launch spawned still runs, so it is
    # the game (or the wrapper hosting it) and gets waited for.
    server = spawn_sleeper()
    client = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_live_ref(client),
    )

    def _on_tick(tick: int) -> None:
        if tick == 2:
            client.kill()
            client.wait(timeout=4)

    sleep, clock = _ticking(_on_tick)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=0.0, sleep=sleep, clock=clock)

    server.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_watch_game_leaves_the_server_when_stop_on_exit_is_false(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """watch_game leaves server running when stop_server_on_exit=false.

    Server started_by_sptmod=True but stop_server_on_exit=False. Client exits;
    watch_game clears state but leaves server running (does not terminate it).
    """
    server = spawn_sleeper()
    client = spawn_client()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=False,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    assert server.poll() is None  # still running
    assert not _state_file(tmp_path).exists()


def test_watch_game_leaves_a_server_it_did_not_start(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """watch_game leaves server alone when started_by_sptmod is false.

    Server was not started by sptmod (started externally); even though
    stop_server_on_exit is true, the server is not terminated. State is cleared.
    """
    server = spawn_sleeper()
    client = spawn_client()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=False),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    assert server.poll() is None  # still running -- sptmod never started it
    assert not _state_file(tmp_path).exists()


def test_watch_game_clears_state_with_no_server_recorded_at_all(tmp_path: Path, spawn_sleeper: SpawnSleeper) -> None:
    """watch_game clears state when client exits and no server was ever recorded.

    State has only client (no server entry). After client exits, watch_game clears
    state file (since nothing needs it anymore).
    """
    client = spawn_sleeper()
    write_launch_state(tmp_path, profile_id="p", stop_server_on_exit=True, client=_live_ref(client))

    _watch_until_client_exits(tmp_path, client)

    assert not _state_file(tmp_path).exists()


# --------------------------------------------------------------------------
# watch_game: the game never shows up.
# --------------------------------------------------------------------------


def test_watch_game_stops_the_server_and_notes_why_when_no_game_appears(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """watch_game stops server and records failure when game doesn't appear within grace period.

    Grace period is 9s; game never appears (tick callback does nothing). After
    grace expires, watch_game stops the server and writes failure note to state.
    """
    server = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=9.0, sleep=sleep, clock=clock)

    server.wait(timeout=4)
    payload = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert payload["failure"] == CLIENT_NEVER_STARTED


def test_watch_game_keeps_waiting_when_the_recorded_start_time_does_not_match(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """watch_game waits on the spawned process even when its start time differs.

    State records a running process under a start time of 1.0, which no
    process really has; nothing carries the game's name and the grace period
    is over from the first look. The watcher must still treat the running
    process number as the game rather than declaring the launch failed --
    stopping the SPT server under a player in a raid is the one outcome this
    guard exists to prevent. The process is killed on the second tick, after
    which the watcher clears the state file as it does for any closed game.
    """
    stranger = spawn_sleeper()
    write_launch_state(tmp_path, profile_id="p", client=process_ref(stranger.pid, 1.0))

    def _on_tick(tick: int) -> None:
        if tick == 2:
            stranger.kill()
            stranger.wait(timeout=4)

    sleep, clock = _ticking(_on_tick)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=0.0, sleep=sleep, clock=clock)

    assert not _state_file(tmp_path).exists()
    assert CLIENT_NEVER_STARTED not in _log_text(tmp_path)


def test_the_noted_failure_survives_reading_the_state_file_back(tmp_path: Path, spawn_sleeper: SpawnSleeper) -> None:
    """Failure note persists in state file; launch_status still reads other fields like profile_id.

    watch_game writes failure note; state file is not deleted. launch_status can
    still read the profile_id field from the state despite the failure.
    """
    write_launch_state(tmp_path, profile_id="p", client=_dead_ref(spawn_sleeper))

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=1.0, grace_seconds=1.0, sleep=sleep, clock=clock)

    payload = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert payload["failure"] == CLIENT_NEVER_STARTED
    # The rest of the state survives and still reads back as launch state.
    assert launch_status(tmp_path).profile_id == "p"


# --------------------------------------------------------------------------
# watch_game: the launch log.
# --------------------------------------------------------------------------


def test_watch_game_writes_its_decisions_to_the_launch_log(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """watch_game notes that it saw the game, that it closed and what it did to the server.

    A launch is set up with a server sptmod started and a running game; the
    game is killed on the first tick. The log must carry the three decisions a
    player needs to understand afterwards -- the game was seen, both it and
    the spawned process are gone, and the server was closed -- because both
    processes run with their output closed and leave no other trace.
    """
    server = spawn_sleeper()
    client = spawn_client()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    server.wait(timeout=4)
    log = _log_text(tmp_path)
    assert "The game is running." in log
    assert "The game and the process the launch started are both gone." in log
    assert "The SPT server was closed." in log


def test_watch_game_notes_the_server_it_left_running(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """watch_game notes in the log that it left a server it must not close alone.

    The launch asked for the server to stay up. When the game closes, the log
    must say the server was left running, so a later launch that finds a busy
    port has an explanation.
    """
    server = spawn_sleeper()
    client = spawn_client()
    write_launch_state(
        tmp_path,
        profile_id="p",
        stop_server_on_exit=False,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    assert "The SPT server was left running." in _log_text(tmp_path)


def test_watch_game_logs_the_process_table_when_no_game_appeared(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_server: SpawnSleeper,
) -> None:
    """watch_game records why it gave up and what the process table held.

    The spawned process is gone and no game ever appears, so the fallback
    fires. A running process named like the SPT server is started first: the
    log must name the reason, the grace period, and both snapshots -- the
    processes the scan classified and the wider list of processes mentioning
    SPT -- because that is the only evidence of why the game was not found.
    """
    spawn_server()
    write_launch_state(tmp_path, profile_id="p", client=_dead_ref(spawn_sleeper))

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=9.0, sleep=sleep, clock=clock)

    log = _log_text(tmp_path)
    assert CLIENT_NEVER_STARTED in log
    assert "The process the launch started is gone and no game appeared within 9 seconds." in log
    assert f"SPT processes seen: {SERVER_LINUX_NAME} (server, process " in log
    assert "Processes mentioning SPT: " in log


def test_watch_game_logs_an_empty_process_table_when_nothing_spt_runs(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """watch_game says so plainly when the scan found no SPT process at all.

    Nothing the test started carries an SPT name, so the classified snapshot
    is empty. The log must still hold a readable line rather than a dangling
    label, so the fallback reads the same whether or not anything was found.
    """
    write_launch_state(tmp_path, profile_id="p", client=_dead_ref(spawn_sleeper))

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=1.0, grace_seconds=1.0, sleep=sleep, clock=clock)

    assert "SPT processes seen: none." in _log_text(tmp_path)


def test_watch_game_survives_a_launch_log_it_cannot_write(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """watch_game still does its work when the launch log cannot be written.

    The log path is a directory, so every append raises OSError. The watcher
    must swallow that and still record the failure in the state file:
    bookkeeping never decides whether the game is watched.
    """
    (tmp_path / LAUNCH_LOG_FILE_NAME).mkdir()
    write_launch_state(tmp_path, profile_id="p", client=_dead_ref(spawn_sleeper))

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=1.0, grace_seconds=1.0, sleep=sleep, clock=clock)

    payload = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert payload["failure"] == CLIENT_NEVER_STARTED


# --------------------------------------------------------------------------
# sptmod.watch: the process entry.
# --------------------------------------------------------------------------


def test_main_reports_usage_and_exits_2_with_the_wrong_argument_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() exits 2 and prints USAGE to stderr when argv list is empty.

    Called with no arguments; main() validates argument count and exits with
    code 2. USAGE constant is printed to stderr.
    """
    assert main([]) == 2
    assert USAGE in capsys.readouterr().err


def test_main_reports_usage_and_exits_2_with_too_many_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() exits 2 and prints USAGE to stderr when argv has 2 items.

    Called with ["a", "b"]; main() validates argument count, finds too many,
    exits with code 2 and prints USAGE to stderr.
    """
    assert main(["a", "b"]) == 2
    assert USAGE in capsys.readouterr().err


def test_main_watches_the_given_directory_and_returns_0(tmp_path: Path) -> None:
    """main() parses path arg, calls watch_game, returns exit code 0.

    Argument list is [str(tmp_path)]; no state file exists so watch_game returns
    immediately. Proves main() parses the path and calls watch_game correctly.
    """
    # No state file at all: watch_game returns immediately, so this proves
    # main() actually calls watch_game with the parsed path, end to end.
    assert main([str(tmp_path)]) == 0


def test_run_module_as_main_watches_and_exits_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running sptmod.watch as __main__ calls main() and exits 0.

    sys.argv is ["watch", str(tmp_path)]; state has server but no client so
    watch_game returns immediately. Module's __main__ block raises SystemExit(0).
    """
    # Mirrors tests/test_dump_openapi.py's coverage of the __main__ guard: a
    # state file naming no client at all makes watch_game return immediately.
    write_launch_state(tmp_path, profile_id="p", server=process_ref(1, 1.0, started_by_sptmod=True))
    monkeypatch.setattr(sys, "argv", ["watch", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("sptmod.watch", run_name="__main__", alter_sys=True)

    assert exit_info.value.code == 0
