"""Tests for the part of ``launch_profile`` that starts the game itself:
the full happy path, the command line and environment built from the launcher
settings, what is written to the state file and to the launch log, a game
program that stops again right away, and the exclusive launch lock.

No test starts a real SPT server or game; the stub binaries never bind a
port, and every ping/login answer is simulated through respx.
"""

import json
import os
import stat
import zlib
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
import respx

from fixtures_launch import LaunchableGameDir, kill_launchable_processes, stub_dump
from launch_helpers import capture_progress, login_ok, pong_route, wait_until
from sptmod.core.errors import (
    LaunchError,
)
from sptmod.core.launch import (
    LAUNCH_LOCK_FILE_NAME,
    LAUNCH_LOG_FILE_NAME,
    LAUNCH_LOG_MAX_BYTES,
    LAUNCH_STATE_FILE_NAME,
    LaunchResult,
    launch_profile,
)
from sptmod.core.sptserver import PING_PATH

pytestmark = pytest.mark.timeout(5)


def _free_then_ready_route(router: respx.MockRouter) -> respx.Route:
    """The initial check answers "free" once; every later poll answers "spt"."""
    calls = {"n": 0}

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    return router.get(PING_PATH).mock(side_effect=_side_effect)


def _state_payload(root: Path) -> dict[str, Any]:
    """Load and parse LAUNCH_STATE_FILE_NAME from the game directory.

    Reads the JSON state file from root and returns the parsed payload dict,
    which holds keys like profile_id, server, client, watcher, stop_server_on_exit.
    """
    payload: dict[str, Any] = json.loads((root / LAUNCH_STATE_FILE_NAME).read_text(encoding="utf-8"))
    return payload


def _log_text(root: Path) -> str:
    """Read the launch log of the game directory.

    Returns the whole appended text of LAUNCH_LOG_FILE_NAME so a test can
    assert on the sections one launch wrote.
    """
    return (root / LAUNCH_LOG_FILE_NAME).read_text(encoding="utf-8")


def _settings_with(d: LaunchableGameDir, **linux_settings: Any) -> None:
    """Overwrite named LinuxSettings keys of the launcher settings file.

    Reads LauncherSettings.json, replaces the given keys inside LinuxSettings
    and writes it back, so a test only names the setting it cares about.
    """
    settings = json.loads(d.launcher_settings_file.read_text(encoding="utf-8"))
    settings["LinuxSettings"].update(linux_settings)
    d.launcher_settings_file.write_text(json.dumps(settings), encoding="utf-8")


# --------------------------------------------------------------------------
# launch_profile: full happy path, command line, state file
# --------------------------------------------------------------------------


def test_launch_profile_full_happy_path(fake_launchable_dir: LaunchableGameDir) -> None:
    """Full launch completes: server starts, login succeeds, client starts, state file written.

    Validates: LaunchResult fields and type, process dumps match argv/cwd/env
    specs, state file records process references with started_by_sptmod marker,
    watcher process is spawned, progress callback receives ordered phases with
    increasing percentages, login request uses zlib compression with correct
    header.
    """
    d = fake_launchable_dir
    events, progress = capture_progress()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_then_ready_route(router)
        login_route = login_ok(router)

        result = launch_profile(d.root, d.profile_id, True, progress, poll_seconds=0.0)

    # -- result -------------------------------------------------------
    assert result == LaunchResult(
        profile_id=d.profile_id,
        nickname=d.nickname,
        server_pid=result.server_pid,
        client_pid=result.client_pid,
        server_started=True,
    )
    assert result.server_pid > 0
    assert result.client_pid > 0

    # -- progress phases, in order, exactly the four named steps ------
    steps = [step for step, _, _ in events]
    assert steps[0] == "server"
    assert "waiting" in steps
    assert steps[-3:] == ["login", "client", "done"]
    assert events[-3] == ("login", 80, f"Signing in as {d.nickname}")
    assert events[-2] == ("client", 95, "Starting the game")
    assert events[-1] == ("done", 100, "The game is starting")

    # -- login request: zlib PUT body, raw-response header -------------
    login_request = login_route.calls.last.request
    assert login_request.method == "PUT"
    assert login_request.headers["responsecompressed"] == "0"
    assert json.loads(zlib.decompress(login_request.content)) == {"username": d.username}

    # -- server argv/cwd ------------------------------------------------
    wait_until(lambda: Path(f"{d.server_binary}.dump.json").exists())
    server_dump = stub_dump(d.server_binary)
    assert server_dump["argv"] == [str(d.server_binary)]
    assert server_dump["cwd"] == str(d.root / "SPT_Runtime")

    # -- client argv/cwd/env ---------------------------------------------
    wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"] == [
        str(d.umu_path),
        str(d.client_exe),
        "-force-gfx-jobs",
        "native",
        f"-token={d.profile_id}",
        f"-config={{'BackendUrl':'{d.base_url}','Version':'live','MatchingVersion':'live'}}",
    ]
    assert client_dump["cwd"] == str(d.root)
    env = client_dump["env"]
    assert env["WINEPREFIX"] == str(d.root / "prefix")
    assert env["PROTONPATH"] == "GE-Proton11-5"
    assert env["__NV_DISABLE_EXPLICIT_SYNC"] == "1"
    assert env["WINEDLLOVERRIDES"] == "winhttp=n,b"
    assert env["PROTON_USE_XALIA"] == "0"
    assert env["PROTON_MEDIA_USE_GST"] == "1"

    # -- state file -------------------------------------------------------
    state = _state_payload(d.root)
    assert state["profile_id"] == d.profile_id
    assert state["stop_server_on_exit"] is True
    assert state["server"]["pid"] == result.server_pid
    assert state["server"]["started_by_sptmod"] is True
    assert state["client"]["pid"] == result.client_pid
    assert isinstance(state["watcher"]["pid"], int)
    assert state["watcher"]["pid"] > 0

    kill_launchable_processes(d.root)


def test_launch_profile_writes_stop_server_on_exit_false(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile writes stop_server_on_exit false to state file when param is false.

    Successful launch with stop_server_on_exit=False argument; state file's
    stop_server_on_exit field reads as False.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_then_ready_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id, False, poll_seconds=0.0)

    assert _state_payload(d.root)["stop_server_on_exit"] is False


def test_launch_profile_tokenizes_launch_settings_with_quotes_and_extra_flags(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile tokenizes LaunchSettings: X=value sets env vars, -flag adds argv.

    LaunchSettings string with environment variable assignments (X=...) and flags
    (-SomeFlag) and quoted values with embedded spaces; verification checks that
    the wrapper (umu) received the correct argv and env vars.
    """
    d = fake_launchable_dir
    settings = json.loads(d.launcher_settings_file.read_text(encoding="utf-8"))
    settings["LinuxSettings"]["LaunchSettings"] = 'PROTON_USE_XALIA=0 -SomeFlag X="a b"'
    d.launcher_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"][-1] == "-SomeFlag"
    assert client_dump["env"]["PROTON_USE_XALIA"] == "0"
    assert client_dump["env"]["X"] == "a b"


def test_launch_profile_unquotes_a_single_quoted_default_environment_value(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile strips single quotes from a DefaultEnv value.

    DefaultEnv is one setting, not a list, so it never passes the tokenizer
    and carries its quotes into the value. The official launcher writes it
    with either quote character, and the game must see the value without
    them: quotes inside WINEDLLOVERRIDES would make Wine ignore the override.
    """
    d = fake_launchable_dir
    _settings_with(d, DefaultEnv="WINEDLLOVERRIDES='winhttp=n,b'")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    assert stub_dump(d.umu_path)["env"]["WINEDLLOVERRIDES"] == "winhttp=n,b"


def test_launch_profile_prefixes_gamemoderun_when_game_mode_is_set(
    fake_launchable_dir: LaunchableGameDir,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launch_profile prepends gamemoderun wrapper when GameMode=true in settings.

    Settings LinuxSettings.GameMode is set to true; fake gamemoderun on PATH leaves
    a marker file and execs umu. launch_profile starts client wrapped with gamemoderun,
    verified by marker existence and by gamemoderun being invoked before umu argv[0].
    """
    d = fake_launchable_dir
    settings = json.loads(d.launcher_settings_file.read_text(encoding="utf-8"))
    settings["LinuxSettings"]["GameMode"] = True
    d.launcher_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    # A passthrough "gamemoderun" put first on PATH: it leaves a marker (proof
    # it, specifically, was invoked) and then execs straight into the umu
    # stub, which keeps writing its own dump exactly as in the non-GameMode
    # case.
    marker = tmp_path / "gamemoderun-was-invoked"
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    gamemoderun = fake_bin / "gamemoderun"
    gamemoderun.write_text(f'#!/bin/sh\necho invoked > "{marker}"\nexec "$@"\n', encoding="utf-8")
    gamemoderun.chmod(gamemoderun.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    wait_until(marker.exists)
    assert marker.read_text(encoding="utf-8").strip() == "invoked"
    wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"][0] == str(d.umu_path)


def test_launch_profile_records_zero_started_at_when_the_process_vanishes_instantly(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_profile records started_at=0 when client process exits after spawn but before create_time().

    The race condition (process exits between Popen and psutil.create_time) is
    simulated: psutil.Process first raises NoSuchProcess, then succeeds on retry
    (simulating the later parts of startup checking). state file records 0.0 as
    the start time.
    """
    # The vanish-between-spawn-and-create_time() race is not otherwise
    # reproducible on demand. The patch is scoped to a `monkeypatch.context()`
    # block (reverted before this function returns, not at fixture teardown
    # time) because psutil's own internals do `isinstance(x, Process)`
    # lookups against the module-level name; leaving the patch active past
    # this block corrupts unrelated psutil calls made during fixture cleanup.
    d = fake_launchable_dir
    real_process = psutil.Process
    calls = {"n": 0}

    def _fake_process(pid: int) -> psutil.Process:
        calls["n"] += 1
        if calls["n"] == 1:
            raise psutil.NoSuchProcess(pid)
        return real_process(pid)

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)
        with monkeypatch.context() as ctx:
            ctx.setattr(psutil, "Process", _fake_process)
            launch_profile(d.root, d.profile_id)

    assert _state_payload(d.root)["client"]["started_at"] == 0.0


def test_launch_profile_wraps_a_state_file_write_failure_naming_the_running_pids(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile wraps state file write OSError into LaunchError with pids.

    After server login and client spawn, launch_profile attempts atomic replace of
    state file; LAUNCH_STATE_FILE_NAME is a directory instead of a file, so
    os.replace() fails. launch_profile wraps the OSError into LaunchError naming
    server and client pids and game directory path, warning they run unrecorded.
    """
    # write_state's atomic-replace step has no recovery path of its own; a
    # state "file" that is actually a directory makes os.replace() fail.
    # launch_profile wraps that OSError into a LaunchError naming the server
    # and client pids that are running unrecorded, rather than letting a raw
    # OSError escape.
    d = fake_launchable_dir
    (d.root / LAUNCH_STATE_FILE_NAME).mkdir()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LaunchError) as exc_info:
            launch_profile(d.root, d.profile_id)

    assert str(d.root) in str(exc_info.value)


# --------------------------------------------------------------------------
# launch_profile: the launch log
# --------------------------------------------------------------------------


def test_launch_profile_writes_a_launch_log_describing_every_step(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile appends a plain-language account of the launch to the log.

    Both the server and the game are started with their output closed, so the
    log is the only trace a launch leaves. It must name the player, the server
    program and its working directory, how often and how long the server was
    asked before it answered, that the login was accepted, and the command the
    game was started with.
    """
    d = fake_launchable_dir

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_then_ready_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id, poll_seconds=0.0)

    log = _log_text(d.root)
    assert f"Starting the game for {d.nickname}." in log
    assert f"Starting the SPT server: {d.server_binary} (working directory {d.root / 'SPT_Runtime'})." in log
    assert "Checks for the SPT server: 1, seconds waited: 0, last answer: spt." in log
    assert f"The SPT server signed {d.nickname} in." in log
    assert f"Starting the game: {d.umu_path} {d.client_exe} -force-gfx-jobs native -token=" in log


def test_launch_profile_keeps_the_profile_token_out_of_the_launch_log(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile writes the game's command line with the token hidden.

    The profile id is the token the game signs in with, so it must not appear
    anywhere in a file a player may pass on when asking for help. The command
    line is still readable, with the token replaced by a placeholder.
    """
    d = fake_launchable_dir

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    log = _log_text(d.root)
    assert "-token=<hidden>" in log
    assert d.profile_id not in log


def test_launch_profile_logs_the_game_settings_without_their_values(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile names every setting it passed on, showing only two values.

    A game that starts into nothing is almost always a wrong prefix or Proton
    version, so those two are shown in full; every other setting is named
    without its value, because the launcher settings may carry personal paths.
    """
    d = fake_launchable_dir

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    log = _log_text(d.root)
    assert f"WINEPREFIX={d.root / 'prefix'}" in log
    assert "PROTONPATH=GE-Proton11-5" in log
    assert "__NV_DISABLE_EXPLICIT_SYNC=<hidden>" in log
    assert "WINEDLLOVERRIDES=<hidden>" in log
    assert "winhttp" not in log


def test_launch_profile_drops_the_oldest_lines_of_an_oversized_launch_log(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile trims the launch log from the front, dropping whole lines.

    The log is pre-filled past its size limit. After a launch it must fit the
    limit again, the oldest lines must be gone, the newest pre-filled line
    must have survived, and the first surviving line must still be complete --
    a half line would make the oldest entry unreadable.
    """
    d = fake_launchable_dir
    padding = "x" * 100
    log_file = d.root / LAUNCH_LOG_FILE_NAME
    log_file.write_text("".join(f"old line {n:06d} {padding}\n" for n in range(2200)), encoding="utf-8")
    assert log_file.stat().st_size > LAUNCH_LOG_MAX_BYTES

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    lines = _log_text(d.root).splitlines()
    assert log_file.stat().st_size <= LAUNCH_LOG_MAX_BYTES
    assert "old line 000000" not in lines[0]
    assert lines[0].endswith(padding)
    assert any("old line 002199" in line for line in lines)
    assert any("Starting the game for" in line for line in lines)


def test_launch_profile_succeeds_when_the_launch_log_cannot_be_written(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile starts the game even when its log cannot be written.

    The log path is a directory, so both appending a line and handing the
    game's error output to the log raise OSError. A launch must never fail
    over its own bookkeeping, so the game is started and reported as usual.
    """
    d = fake_launchable_dir
    (d.root / LAUNCH_LOG_FILE_NAME).mkdir()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        result = launch_profile(d.root, d.profile_id)

    assert result.client_pid > 0
    assert _state_payload(d.root)["client"]["pid"] == result.client_pid


# --------------------------------------------------------------------------
# launch_profile: a game program that stops right away
# --------------------------------------------------------------------------


def test_launch_profile_reports_a_game_program_that_fails_immediately(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises and points at the log when the game program fails.

    The program that hosts the game exits with code 3 and complains on its
    error output. That output goes into the launch log, and the raised error
    names the log file instead of repeating a technical message, because the
    complaint belongs to the program, not to sptmod.
    """
    d = fake_launchable_dir
    d.umu_path.write_text("#!/bin/sh\necho 'umu: the prefix is unusable' >&2\nexit 3\n", encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LaunchError) as exc_info:
            launch_profile(d.root, d.profile_id)

    assert str(d.root / LAUNCH_LOG_FILE_NAME) in str(exc_info.value)
    log = _log_text(d.root)
    assert "umu: the prefix is unusable" in log
    assert "The game program stopped right away with exit code 3." in log


def test_launch_profile_accepts_a_game_program_that_stops_with_success(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile lets a game program that exits cleanly count as started.

    A wrapper that hands the game over and returns zero is how a launch may
    legitimately look, so no error is raised; the log still notes that the
    started program is already gone, which is what the watcher acts on next.
    """
    d = fake_launchable_dir
    d.umu_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        result = launch_profile(d.root, d.profile_id)

    assert result.client_pid > 0
    assert "The game program stopped right away with exit code 0." in _log_text(d.root)


# --------------------------------------------------------------------------
# launch_profile: the exclusive launch lock
# --------------------------------------------------------------------------


def test_launch_profile_refuses_a_concurrent_launch(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when launch lock file already exists.

    Lock file LAUNCH_LOCK_FILE_NAME is present; launch_profile detects it and
    raises LaunchError with "already in progress", refusing to proceed.
    """
    d = fake_launchable_dir
    (d.root / LAUNCH_LOCK_FILE_NAME).touch()

    with pytest.raises(LaunchError, match="already in progress"):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_when_the_lock_file_cannot_be_created(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when lock file creation fails (no write).

    Game directory is chmod 0o555 (read-only); open() to create LAUNCH_LOCK_FILE_NAME
    fails. launch_profile catches the OSError and raises LaunchError with
    "cannot be written" in the message.
    """
    d = fake_launchable_dir
    d.root.chmod(0o555)
    try:
        with pytest.raises(LaunchError, match="cannot be written"):
            launch_profile(d.root, d.profile_id)
    finally:
        d.root.chmod(0o755)


def test_launch_profile_removes_the_lock_file_after_a_successful_launch(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile deletes lock file after successful profile launch.

    After all startup steps complete (server ready, login, client spawn, state written),
    launch_profile removes LAUNCH_LOCK_FILE_NAME and returns LaunchResult. Lock file
    does not exist after successful return.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        launch_profile(d.root, d.profile_id)

    assert not (d.root / LAUNCH_LOCK_FILE_NAME).exists()


def test_launch_profile_removes_the_lock_file_after_a_failed_launch(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile removes lock file even when launch fails partway through.

    Profile name does not exist, so launch_profile raises LaunchError; the lock
    file created at start is cleaned up in the exception path. Lock file does
    not exist after the exception.
    """
    d = fake_launchable_dir

    with pytest.raises(LaunchError):
        launch_profile(d.root, "no-such-profile")

    assert not (d.root / LAUNCH_LOCK_FILE_NAME).exists()


def test_launch_profile_raises_launch_error_when_the_client_program_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when wrapper binary (UMU) does not exist.

    After login succeeds, attempt to spawn client fails because UmuPath points to
    non-existent binary.
    """
    d = fake_launchable_dir
    settings = json.loads(d.launcher_settings_file.read_text(encoding="utf-8"))
    settings["LinuxSettings"]["UmuPath"] = str(d.root / "does-not-exist-binary")
    d.launcher_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LaunchError, match="could not be started"):
            launch_profile(d.root, d.profile_id)
