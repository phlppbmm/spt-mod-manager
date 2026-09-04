"""Tests for ``sptmod.core.launch.watch_game`` and the ``python -m
sptmod.watch`` process entry that runs it.

Uses real throwaway processes (never the real game) so ``psutil`` sees a
genuine process table: one of them is a copy of ``sleep`` carrying the game's
own file name, which is what the watcher's name scan looks for. The poll
cadence and the clock are always injected so no test waits on a real clock.
"""

import json
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil
import pytest

from sptmod.core.game_dir import CLIENT_EXE_NAME
from sptmod.core.launch import (
    CLIENT_NEVER_STARTED,
    LAUNCH_STATE_FILE_NAME,
    _read_state,
    watch_game,
)
from sptmod.watch import USAGE, main

pytestmark = pytest.mark.timeout(5)


def _write_state_file(
    root: Path,
    *,
    stop_server_on_exit: bool = True,
    server: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
) -> None:
    payload = {
        "profile_id": "p",
        "stop_server_on_exit": stop_server_on_exit,
        "server": server,
        "client": client,
        "watcher": None,
    }
    (root / LAUNCH_STATE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _ref(pid: int, started_at: float, *, started_by_sptmod: bool | None = None) -> dict:
    payload: dict[str, Any] = {"pid": pid, "started_at": started_at}
    if started_by_sptmod is not None:
        payload["started_by_sptmod"] = started_by_sptmod
    return payload


def _live_ref(
    process: subprocess.Popen, *, started_by_sptmod: bool | None = None
) -> dict[str, Any]:
    return _ref(
        process.pid,
        psutil.Process(process.pid).create_time(),
        started_by_sptmod=started_by_sptmod,
    )


def _dead_ref(spawn: Callable[[], subprocess.Popen]) -> dict[str, Any]:
    """Spawn a process, note it and end it: a launch process that is gone."""
    process = spawn()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    return _ref(process.pid, started_at)


def _spawner(executable: Path) -> Iterator[Callable[[], subprocess.Popen]]:
    spawned: list[subprocess.Popen] = []

    def _spawn() -> subprocess.Popen:
        process = subprocess.Popen([str(executable), "30"])
        spawned.append(process)
        return process

    yield _spawn
    for process in spawned:
        with suppress(OSError):
            process.kill()
            process.wait(timeout=2)


@pytest.fixture
def spawn_sleeper() -> Iterator[Callable[[], subprocess.Popen]]:
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    yield from _spawner(Path(sleep_bin))


@pytest.fixture
def spawn_client(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Callable[[], subprocess.Popen]]:
    """Spawn processes the real name scan reports as the running game.

    A copy of ``sleep`` under the game's file name, so ``psutil`` reports that
    name and ``game_dir.game_processes`` classifies it as a client -- no
    monkeypatching of the process table anywhere in the watcher flow.
    """
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    executable = tmp_path_factory.mktemp("game") / CLIENT_EXE_NAME
    shutil.copy(sleep_bin, executable)
    yield from _spawner(executable)


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
    return None


def _state_file(root: Path) -> Path:
    return root / LAUNCH_STATE_FILE_NAME


# --------------------------------------------------------------------------
# watch_game: immediate-return siblings (state file never cleared).
# --------------------------------------------------------------------------


def test_watch_game_returns_immediately_with_no_state_file(tmp_path: Path) -> None:
    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))  # type: ignore[arg-type]

    assert not _state_file(tmp_path).exists()


def test_watch_game_returns_immediately_with_a_malformed_state_file(
    tmp_path: Path,
) -> None:
    _state_file(tmp_path).write_text("not json", encoding="utf-8")

    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))  # type: ignore[arg-type]

    assert _state_file(tmp_path).read_text(encoding="utf-8") == "not json"


def test_watch_game_returns_immediately_with_no_client_recorded(tmp_path: Path) -> None:
    _write_state_file(tmp_path, server=_ref(1, 1.0, started_by_sptmod=True))

    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))  # type: ignore[arg-type]

    assert _state_file(tmp_path).exists()  # left untouched, not cleared


# --------------------------------------------------------------------------
# watch_game: normal flow -- waits for the game, then decides on the server.
# --------------------------------------------------------------------------


def _watch_until_client_exits(tmp_path: Path, client: subprocess.Popen) -> None:
    def _on_tick(tick: int) -> None:
        if tick == 1:
            client.kill()
            client.wait(timeout=4)

    sleep, clock = _ticking(_on_tick)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=30.0, sleep=sleep, clock=clock)


def test_watch_game_stops_the_server_when_it_started_it_and_was_asked_to(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    server = spawn_sleeper()
    client = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_live_ref(client),
    )

    _watch_until_client_exits(tmp_path, client)

    server.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_watch_game_follows_the_game_by_name_when_the_recorded_pid_is_gone(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen],
    spawn_client: Callable[[], subprocess.Popen],
) -> None:
    # What a Linux launch looks like: the process sptmod spawned was only the
    # wrapper and is already gone, while the game itself runs under Wine.
    server = spawn_sleeper()
    client = spawn_client()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    server.wait(timeout=4)
    assert not _state_file(tmp_path).exists()


def test_watch_game_waits_for_the_game_to_appear_before_watching_it(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen],
    spawn_client: Callable[[], subprocess.Popen],
) -> None:
    server = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )
    started: list[subprocess.Popen] = []

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
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    # Nothing carries the game's name, the grace period is over from the
    # first look -- but the process the launch spawned still runs, so it is
    # the game (or the wrapper hosting it) and gets waited for.
    server = spawn_sleeper()
    client = spawn_sleeper()
    _write_state_file(
        tmp_path,
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
    spawn_sleeper: Callable[[], subprocess.Popen],
    spawn_client: Callable[[], subprocess.Popen],
) -> None:
    server = spawn_sleeper()
    client = spawn_client()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=False,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    assert server.poll() is None  # still running
    assert not _state_file(tmp_path).exists()


def test_watch_game_leaves_a_server_it_did_not_start(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen],
    spawn_client: Callable[[], subprocess.Popen],
) -> None:
    server = spawn_sleeper()
    client = spawn_client()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=False),
        client=_dead_ref(spawn_sleeper),
    )

    _watch_until_client_exits(tmp_path, client)

    assert server.poll() is None  # still running -- sptmod never started it
    assert not _state_file(tmp_path).exists()


def test_watch_game_clears_state_with_no_server_recorded_at_all(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    client = spawn_sleeper()
    _write_state_file(tmp_path, stop_server_on_exit=True, client=_live_ref(client))

    _watch_until_client_exits(tmp_path, client)

    assert not _state_file(tmp_path).exists()


# --------------------------------------------------------------------------
# watch_game: the game never shows up.
# --------------------------------------------------------------------------


def test_watch_game_stops_the_server_and_notes_why_when_no_game_appears(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    server = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_live_ref(server, started_by_sptmod=True),
        client=_dead_ref(spawn_sleeper),
    )

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=9.0, sleep=sleep, clock=clock)

    server.wait(timeout=4)
    payload = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert payload["failure"] == CLIENT_NEVER_STARTED


def test_watch_game_notes_the_failure_when_the_recorded_pid_was_reused(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    # A real, running process, but a started_at that does not match it --
    # exactly what a pid reused by an unrelated process would look like.
    stranger = spawn_sleeper()
    _write_state_file(tmp_path, client=_ref(stranger.pid, 1.0))

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=3.0, grace_seconds=3.0, sleep=sleep, clock=clock)

    assert stranger.poll() is None  # left alone -- it is not the game
    payload = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert payload["failure"] == CLIENT_NEVER_STARTED


def test_the_noted_failure_survives_reading_the_state_file_back(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    _write_state_file(tmp_path, client=_dead_ref(spawn_sleeper))

    sleep, clock = _ticking(_nothing)
    watch_game(tmp_path, poll_seconds=1.0, grace_seconds=1.0, sleep=sleep, clock=clock)

    state = _read_state(tmp_path)
    assert state.failure == CLIENT_NEVER_STARTED
    assert state.profile_id == "p"  # the rest of the state survives


# --------------------------------------------------------------------------
# sptmod.watch: the process entry.
# --------------------------------------------------------------------------


def test_main_reports_usage_and_exits_2_with_the_wrong_argument_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 2
    assert USAGE in capsys.readouterr().err


def test_main_reports_usage_and_exits_2_with_too_many_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["a", "b"]) == 2
    assert USAGE in capsys.readouterr().err


def test_main_watches_the_given_directory_and_returns_0(tmp_path: Path) -> None:
    # No state file at all: watch_game returns immediately, so this proves
    # main() actually calls watch_game with the parsed path, end to end.
    assert main([str(tmp_path)]) == 0


def test_run_module_as_main_watches_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mirrors tests/test_dump_openapi.py's coverage of the __main__ guard: a
    # state file naming no client at all makes watch_game return immediately.
    _write_state_file(tmp_path, server=_ref(1, 1.0, started_by_sptmod=True))
    monkeypatch.setattr(sys, "argv", ["watch", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("sptmod.watch", run_name="__main__", alter_sys=True)

    assert exit_info.value.code == 0
