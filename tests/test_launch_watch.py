"""Tests for ``sptmod.core.launch.watch_game`` and the ``python -m
sptmod.watch`` process entry that runs it.

Uses real throwaway processes (never the real game) so ``psutil`` sees a
genuine process table; the poll cadence is always injected so no test waits
on a real clock.
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

from sptmod.core.launch import LAUNCH_STATE_FILE_NAME, watch_game
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


@pytest.fixture
def spawn_sleeper() -> Iterator[Callable[[], subprocess.Popen]]:
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    spawned: list[subprocess.Popen] = []

    def _spawn() -> subprocess.Popen:
        process = subprocess.Popen([sleep_bin, "30"])
        spawned.append(process)
        return process

    yield _spawn
    for process in spawned:
        with suppress(OSError):
            process.kill()
            process.wait(timeout=2)


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


def test_watch_game_returns_immediately_when_the_recorded_client_pid_is_dead(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    _write_state_file(tmp_path, client=_ref(process.pid, started_at))

    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))  # type: ignore[arg-type]

    assert _state_file(tmp_path).exists()


def test_watch_game_returns_immediately_on_a_reused_pid(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    process = spawn_sleeper()
    # A real, running process, but a started_at that does not match it --
    # exactly what a pid reused by an unrelated process would look like.
    _write_state_file(tmp_path, client=_ref(process.pid, 1.0))

    watch_game(tmp_path, sleep=lambda _seconds: pytest.fail("must not sleep"))  # type: ignore[arg-type]

    assert _state_file(tmp_path).exists()


# --------------------------------------------------------------------------
# watch_game: normal flow -- waits for the client, then decides on the server.
# --------------------------------------------------------------------------


def _watch_until_client_exits(
    tmp_path: Path, client: subprocess.Popen
) -> None:
    calls = {"n": 0}

    def _fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            client.kill()
            client.wait(timeout=4)

    watch_game(tmp_path, sleep=_fake_sleep)


def test_watch_game_stops_the_server_when_it_started_it_and_was_asked_to(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    server = spawn_sleeper()
    client = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_ref(
            server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True
        ),
        client=_ref(client.pid, psutil.Process(client.pid).create_time()),
    )

    _watch_until_client_exits(tmp_path, client)

    server.wait(timeout=4)
    assert not psutil.pid_exists(server.pid)
    assert not _state_file(tmp_path).exists()


def test_watch_game_leaves_the_server_when_stop_on_exit_is_false(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    server = spawn_sleeper()
    client = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=False,
        server=_ref(
            server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True
        ),
        client=_ref(client.pid, psutil.Process(client.pid).create_time()),
    )

    _watch_until_client_exits(tmp_path, client)

    assert server.poll() is None  # still running
    assert not _state_file(tmp_path).exists()


def test_watch_game_leaves_a_server_it_did_not_start(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    server = spawn_sleeper()
    client = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        server=_ref(
            server.pid,
            psutil.Process(server.pid).create_time(),
            started_by_sptmod=False,
        ),
        client=_ref(client.pid, psutil.Process(client.pid).create_time()),
    )

    _watch_until_client_exits(tmp_path, client)

    assert server.poll() is None  # still running -- sptmod never started it
    assert not _state_file(tmp_path).exists()


def test_watch_game_clears_state_with_no_server_recorded_at_all(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    client = spawn_sleeper()
    _write_state_file(
        tmp_path,
        stop_server_on_exit=True,
        client=_ref(client.pid, psutil.Process(client.pid).create_time()),
    )

    _watch_until_client_exits(tmp_path, client)

    assert not _state_file(tmp_path).exists()


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
    # state file naming a dead client makes watch_game return immediately.
    _write_state_file(tmp_path, client=_ref(999999999, 1.0))
    monkeypatch.setattr(sys, "argv", ["watch", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("sptmod.watch", run_name="__main__", alter_sys=True)

    assert exit_info.value.code == 0
