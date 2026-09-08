"""Tests for ``sptmod.core.launch.launch_status``: what it reports from the
remembered state and the process table, which of the two wins, and how it
treats a state file whose content is malformed.

No test starts a real SPT server or game: every process here is either a
scan result invented by the test or a number ``game_dir.process_alive`` is
made to answer for.
"""

import json
from pathlib import Path

import pytest

from fixtures_launch import process_ref, write_launch_state
from sptmod.core import game_dir
from sptmod.core.game_dir import GameProcess
from sptmod.core.launch import (
    LAUNCH_STATE_FILE_NAME,
    LaunchStatus,
    launch_status,
)

pytestmark = pytest.mark.timeout(5)


def _process_alive(_pid: int, _started_at: float) -> bool:
    """Stand in for ``game_dir.process_alive`` reporting a running process."""
    return True


def _process_gone(_pid: int, _started_at: float) -> bool:
    """Stand in for ``game_dir.process_alive`` reporting a finished process."""
    return False


# --------------------------------------------------------------------------
# launch_status
# --------------------------------------------------------------------------


def test_launch_status_reports_nothing_with_no_state_and_no_processes(
    tmp_path: Path,
) -> None:
    """launch_status returns all-false LaunchStatus when nothing is running or recorded.

    No state file and no game processes found by scan; launch_status returns
    LaunchStatus with all boolean flags false, pids 0, and empty profile_id.
    """
    assert launch_status(tmp_path) == LaunchStatus(
        False, False, 0, 0, "", server_stoppable=False, client_stoppable=False
    )


def test_launch_status_reports_alive_processes_from_the_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status extracts running process pids from state file when alive.

    State file records profile "profile-a" with server pid 111 and client pid 222,
    both started at epoch times 1.0 and 2.0. process_alive returns True for both;
    launch_status returns LaunchStatus reporting both running with the profile_id.
    """
    monkeypatch.setattr(game_dir, "process_alive", _process_alive)
    write_launch_state(
        tmp_path,
        profile_id="profile-a",
        server=process_ref(111, 1.0, started_by_sptmod=True),
        client=process_ref(222, 2.0),
    )

    assert launch_status(tmp_path) == LaunchStatus(
        True, True, 111, 222, "profile-a", server_stoppable=True, client_stoppable=True
    )


def test_launch_status_falls_back_to_the_scanned_process_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status scans running processes when state file is absent.

    No state file exists; process_alive always returns False (so state fallback
    doesn't help). game_processes scan finds SPT.Server.Linux (pid 333) and
    EscapeFromTarkov.exe (pid 444). Result reports both running, profile_id empty.
    """
    monkeypatch.setattr(game_dir, "process_alive", _process_gone)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(pid=333, name="SPT.Server.Linux", kind="server", started_at=9.0),
            GameProcess(pid=444, name="EscapeFromTarkov.exe", kind="client", started_at=10.0),
        ],
    )

    assert launch_status(tmp_path) == LaunchStatus(
        True, True, 333, 444, "", server_stoppable=False, client_stoppable=False
    )


def test_launch_status_reports_the_scanned_game_when_the_recorded_pid_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status falls back to scanned game name when recorded pid dies.

    State file records client pid 222 (dead per process_alive check); scan finds
    EscapeFromTarkov.exe at pid 444 (real game running after wrapper exited).
    launch_status returns the scanned pid (444), not the stale recorded one.
    """
    # The Linux case: the process the launch spawned was only the wrapper and
    # is gone, while the game itself still runs under its own name.
    monkeypatch.setattr(game_dir, "process_alive", _process_gone)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=444, name="EscapeFromTarkov.exe", kind="client", started_at=10.0)],
    )
    write_launch_state(tmp_path, profile_id="profile-a", client=process_ref(222, 2.0))

    status = launch_status(tmp_path)

    assert status.client_running is True
    assert status.client_pid == 444


def test_launch_status_keeps_the_recorded_profile_id_even_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status preserves profile_id from state even when all processes died.

    State file records profile "stale-profile" with pids 1 and 2 (both dead per
    process_alive check) and no scan results. Result shows both_running false
    with pids 0, but profile_id="stale-profile" is retained.
    """
    monkeypatch.setattr(game_dir, "process_alive", _process_gone)
    write_launch_state(
        tmp_path,
        profile_id="stale-profile",
        server=process_ref(1, 1.0, started_by_sptmod=True),
        client=process_ref(2, 2.0),
    )

    assert launch_status(tmp_path) == LaunchStatus(
        False, False, 0, 0, "stale-profile", server_stoppable=False, client_stoppable=False
    )


def test_launch_status_prefers_the_recorded_pid_over_a_scanned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status returns recorded pid from state, not scan, when both present.

    State file records server pid 111 (alive per process_alive); scan simultaneously
    finds SPT.Server.Linux at pid 999. Result server_pid is 111, not 999.
    """
    monkeypatch.setattr(game_dir, "process_alive", _process_alive)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=999, name="SPT.Server.Linux", kind="server", started_at=1.0)],
    )
    write_launch_state(tmp_path, server=process_ref(111, 1.0, started_by_sptmod=True))

    assert launch_status(tmp_path).server_pid == 111


# --------------------------------------------------------------------------
# launch_status: malformed state file content (read_state branches,
# reached only through the public read path).
# --------------------------------------------------------------------------


def test_launch_status_treats_a_non_object_state_file_as_nothing_running(
    tmp_path: Path,
) -> None:
    """launch_status returns all-false when state file is JSON array not object.

    State file contains [1, 2, 3] (array instead of dict); launch_status skips
    the malformed state and returns LaunchStatus with all flags false, pids 0.
    """
    (tmp_path / LAUNCH_STATE_FILE_NAME).write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert launch_status(tmp_path) == LaunchStatus(
        False, False, 0, 0, "", server_stoppable=False, client_stoppable=False
    )


@pytest.mark.parametrize("bad_pid", ["111", -1, 0, True])
def test_launch_status_ignores_a_state_entry_with_an_unusable_pid(
    tmp_path: Path, bad_pid: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status skips server entry with invalid pid (string, negative, zero, bool).

    State server entry has pid="111" (string), or -1, or 0, or True instead of
    positive int. launch_status detects type/value violation and skips the entry,
    returning server_running=False.
    """
    monkeypatch.setattr(game_dir, "process_alive", _process_alive)
    write_launch_state(tmp_path, server={"pid": bad_pid, "started_at": 1.0})

    assert launch_status(tmp_path).server_running is False


@pytest.mark.parametrize("bad_started_at", ["1.0", True])
def test_launch_status_ignores_a_state_entry_with_an_unusable_started_at(
    tmp_path: Path, bad_started_at: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status skips entry with invalid started_at (string "1.0" or bool).

    State server entry has started_at="1.0" (string) or True instead of float.
    launch_status detects type violation and skips the entry, returning
    server_running=False.
    """
    monkeypatch.setattr(game_dir, "process_alive", _process_alive)
    write_launch_state(tmp_path, server={"pid": 111, "started_at": bad_started_at})

    assert launch_status(tmp_path).server_running is False
