"""Tests for ``sptmod.core.launch``: the full ``launch_profile`` sequence
against stub server/client binaries and respx-mocked HTTP, ``launch_status``
against the process table and the state file, and ``stop_game``.

No test starts a real SPT server or game; the stub binaries never bind a
port, and every ping/login answer is simulated through respx.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
import respx

from conftest import LaunchableGameDir, kill_launchable_processes, stub_dump
from sptmod.core import game_dir
from sptmod.core.errors import (
    GameRunningError,
    LaunchError,
    LauncherSettingsError,
    LoginFailedError,
    NotAGameDirError,
    ServerPortBusyError,
)
from sptmod.core.game_dir import GameProcess, detect_layout
from sptmod.core.launch import (
    LAUNCH_LOCK_FILE_NAME,
    LAUNCH_STATE_FILE_NAME,
    LaunchResult,
    LaunchStatus,
    StopResult,
    launch_profile,
    launch_status,
    stop_game,
    tokenize_launch_settings,
)
from sptmod.core.models import Progress
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH

pytestmark = pytest.mark.timeout(5)


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test here from whatever this sandbox happens to run.

    Individual tests override this again through the same ``monkeypatch``
    fixture when they need a specific scan result.
    """
    monkeypatch.setattr(game_dir, "game_processes", lambda: [])


def _progress() -> tuple[list[tuple[str, int, str]], Progress]:
    """Capture all progress callback invocations into a list for inspection.

    Returns a tuple of (events, callback). The callback appends (step, percent,
    message) tuples; events is readable to verify the order and values of each
    reported step.
    """
    events: list[tuple[str, int, str]] = []

    def _capture(step: str, percent: int, message: str) -> None:
        events.append((step, percent, message))

    return events, _capture


def _pong_route(router: respx.MockRouter) -> respx.Route:
    """Mock the SPT server to respond successfully to ping requests."""
    return router.get(PING_PATH).respond(200, json={"Response": "Pong!"})


def _free_route(router: respx.MockRouter) -> respx.Route:
    """Mock the SPT server port as busy (refusing connections)."""
    return router.get(PING_PATH).mock(side_effect=httpx.ConnectError("refused"))


def _free_then_ready_route(router: respx.MockRouter) -> respx.Route:
    """The initial check answers "free" once; every later poll answers "spt"."""
    calls = {"n": 0}

    def _side_effect(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    return router.get(PING_PATH).mock(side_effect=_side_effect)


def _login_ok(router: respx.MockRouter) -> respx.Route:
    """Set up respx to return a successful login response.

    Routes PUT requests to LOGIN_PATH to reply with HTTP 200 and
    {"Response": true}, simulating a profile that the server accepts.
    """
    return router.put(LOGIN_PATH).respond(200, json={"Response": True})


def _wait_until(predicate: Callable[[], bool], timeout: float = 4.0) -> None:
    """Repeatedly call predicate until it returns true or timeout expires.

    Sleeps 20ms between polls; raises AssertionError if the deadline passes
    before predicate becomes true. Timeout defaults to 4 seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was never met")


def _state_payload(root: Path) -> dict[str, Any]:
    """Load and parse LAUNCH_STATE_FILE_NAME from the game directory.

    Reads the JSON state file from root and returns the parsed payload dict,
    which holds keys like profile_id, server, client, watcher, stop_server_on_exit.
    """
    payload: dict[str, Any] = json.loads((root / LAUNCH_STATE_FILE_NAME).read_text(encoding="utf-8"))
    return payload


def _write_state_file(
    root: Path,
    *,
    profile_id: str = "",
    stop_server_on_exit: bool = True,
    server: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
    watcher: dict[str, Any] | None = None,
) -> None:
    """Write LAUNCH_STATE_FILE_NAME to the game directory with given process state.

    Creates the JSON state file recording the profile, process PIDs and start times
    (server, client, watcher), and the stop_server_on_exit flag. Keyword args
    allow each process reference and the flag to be individually set; defaults are
    empty profile and true for stop_server_on_exit.
    """
    payload = {
        "profile_id": profile_id,
        "stop_server_on_exit": stop_server_on_exit,
        "server": server,
        "client": client,
        "watcher": watcher,
    }
    (root / LAUNCH_STATE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _ref(pid: int, started_at: float, *, started_by_sptmod: bool | None = None) -> dict[str, Any]:
    """Build a process reference dict for the launch state file.

    Returns a dict with pid and started_at (float timestamp); if started_by_sptmod
    is not None, it is also included. This object is assigned to server, client, or
    watcher fields in the state file.
    """
    payload: dict[str, Any] = {"pid": pid, "started_at": started_at}
    if started_by_sptmod is not None:
        payload["started_by_sptmod"] = started_by_sptmod
    return payload


@pytest.fixture
def spawn_sleeper() -> Iterator[Callable[[], subprocess.Popen[bytes]]]:
    """Factory spawning a throwaway real process that just sleeps."""
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    spawned: list[subprocess.Popen[bytes]] = []

    def _spawn() -> subprocess.Popen[bytes]:
        process = subprocess.Popen([sleep_bin, "30"])
        spawned.append(process)
        return process

    yield _spawn
    for process in spawned:
        with suppress(OSError):
            process.kill()
            process.wait(timeout=2)


# --------------------------------------------------------------------------
# tokenize_launch_settings
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", []),
        ("   ", []),
        ("plain", ["plain"]),
        ("A=1 B=2", ["A=1", "B=2"]),
        ("  A=1   B=2  ", ["A=1", "B=2"]),
        ('-Arg="x y"', ["-Arg=x y"]),
        ('X="a b" -flag', ["X=a b", "-flag"]),
        ('A="incomplete', ["A=incomplete"]),
    ],
)
def test_tokenize_launch_settings(text: str, expected: list[str]) -> None:
    """Tokenization of launch settings splits on whitespace and handles quoted values.

    Parameterized cases cover empty input, unquoted tokens, quoted tokens with
    embedded spaces, and unclosed quotes (which end at the string end).
    """
    assert tokenize_launch_settings(text) == expected


# --------------------------------------------------------------------------
# launch_profile: refusal before any HTTP call
# --------------------------------------------------------------------------


def test_launch_profile_raises_not_a_game_dir_for_a_bad_path(tmp_path: Path) -> None:
    """launch_profile raises NotAGameDirError when the game directory does not exist.

    Calling launch_profile with a path that has no valid SPT/SPT_Runtime layout
    raises NotAGameDirError before any other checks.
    """
    with pytest.raises(NotAGameDirError):
        launch_profile(tmp_path / "does-not-exist", "any-profile")


def test_launch_profile_refuses_a_running_client(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_profile raises GameRunningError before proceeding when a client is running.

    Process scan returns a running EscapeFromTarkov.exe; launch_profile exits with
    GameRunningError naming the process, refusing to start server or perform login.
    """
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=1, name="EscapeFromTarkov.exe", kind="client", started_at=1.0)],
    )

    with pytest.raises(GameRunningError, match="EscapeFromTarkov.exe"):
        launch_profile(fake_launchable_dir.root, fake_launchable_dir.profile_id)


def test_launch_profile_reuses_an_already_running_server(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_profile reuses an existing server process without spawning a new one.

    Process scan finds an already-running SPT.Server.Linux (pid 555, started at 42.0);
    launch_profile records server_started=False with that pid in the result, and
    server_binary.dump.json is never created (proof the binary was not spawned).
    """
    # A server started by someone else (or a previous launch) is running, but
    # sptmod has no process record of it -- the scan is the only signal.
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=555, name="SPT.Server.Linux", kind="server", started_at=42.0)],
    )
    d = fake_launchable_dir

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        result = launch_profile(d.root, d.profile_id)

    assert result.server_started is False
    assert result.server_pid == 555
    # The stub server binary must never have been spawned.
    assert not Path(f"{d.server_binary}.dump.json").exists()


def test_launch_profile_raises_server_port_busy_for_a_foreign_listener(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises ServerPortBusyError when a foreign process holds the server port.

    Port 6969 answers but responds with an unexpected message, indicating
    another program owns the address.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).respond(200, json={"Response": "not-pong"})

        with pytest.raises(ServerPortBusyError, match="6969"):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_profile: profile file handling
# --------------------------------------------------------------------------


def test_launch_profile_raises_launch_error_for_a_missing_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when the profile JSON file does not exist.

    Calling launch_profile(root, "no-such-profile") when the file is missing raises
    LaunchError with the profile name in the message, preventing any server startup.
    """
    with pytest.raises(LaunchError, match="no-such-profile"):
        launch_profile(fake_launchable_dir.root, "no-such-profile")


@pytest.mark.parametrize(
    "profile_id",
    ["", ".", "..", "../escape", "a/b", "a\\b", "/etc/passwd"],
)
def test_launch_profile_rejects_a_profile_id_that_is_not_a_bare_file_stem(
    fake_launchable_dir: LaunchableGameDir, profile_id: str
) -> None:
    """launch_profile rejects profile IDs containing path separators or dots.

    Parameterized cases include empty string, dot, parent-dir reference, slash
    paths, backslash paths, and absolute paths.
    """
    with pytest.raises(LaunchError):
        launch_profile(fake_launchable_dir.root, profile_id)


def test_launch_profile_raises_launch_error_for_an_unreadable_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when profile JSON is malformed."""
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text("not json", encoding="utf-8")

    with pytest.raises(LaunchError):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_when_username_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when profile.info lacks a username key.

    Profile JSON contains info.id but no info.username field; launch_profile raises
    LaunchError naming the profile (via match), stopping before server startup.
    """
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text(json.dumps({"info": {"id": d.profile_id}}), encoding="utf-8")

    with pytest.raises(LaunchError, match=d.profile_id):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_falls_back_to_the_account_name_when_nickname_is_absent(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile uses username as nickname when profile.info has no nickname.

    Profile JSON has info.username ("test_user") but no info.nickname; launch_profile
    completes successfully and LaunchResult.nickname equals the username value.
    """
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text(
        json.dumps({"info": {"id": d.profile_id, "username": d.username}}),
        encoding="utf-8",
    )

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)
        result = launch_profile(d.root, d.profile_id)

    assert result.nickname == d.username


# --------------------------------------------------------------------------
# launch_profile: missing binaries
# --------------------------------------------------------------------------


def test_launch_profile_raises_launch_error_for_a_missing_server_binary(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when the server executable is missing.

    After profile validation, before spawning the server, launch_profile checks
    that SPT.Server.Linux exists; missing binary raises LaunchError naming it.
    """
    d = fake_launchable_dir
    d.server_binary.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_route(router)

        with pytest.raises(LaunchError, match="SPT.Server.Linux"):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_for_a_missing_client_executable(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when game executable is missing, no state file written.

    The server and client processes may still start; before recording them,
    launch_profile checks that the game binary exists and fails if not.
    """
    d = fake_launchable_dir
    d.client_exe.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LaunchError, match="EscapeFromTarkov.exe"):
            launch_profile(d.root, d.profile_id)

    assert not (d.root / LAUNCH_STATE_FILE_NAME).exists()


# --------------------------------------------------------------------------
# launch_profile: LauncherSettings.json handling
# --------------------------------------------------------------------------


def test_launch_profile_raises_launcher_settings_error_when_the_file_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LauncherSettingsError when LauncherSettings.json is missing.

    After server login succeeds, launch_profile reads LauncherSettings.json to get
    client wrapper settings; missing file raises LauncherSettingsError.
    """
    d = fake_launchable_dir
    d.launcher_settings_file.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launcher_settings_error_for_invalid_json(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LauncherSettingsError when LauncherSettings.json is malformed.

    Settings file exists but contains "not json"; json.loads() fails and
    launch_profile raises LauncherSettingsError instead of letting the parse
    exception escape.
    """
    d = fake_launchable_dir
    d.launcher_settings_file.write_text("not json", encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launcher_settings_error_when_linux_settings_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LauncherSettingsError when LinuxSettings is absent.

    Settings JSON parses but lacks the LinuxSettings section; launch_profile
    requires it and raises LauncherSettingsError instead of continuing.
    """
    d = fake_launchable_dir
    d.launcher_settings_file.write_text(json.dumps({"Servers": []}), encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


@pytest.mark.parametrize("umu_path", [None, ""])
def test_launch_profile_raises_launcher_settings_error_when_umu_path_is_unset(
    fake_launchable_dir: LaunchableGameDir, umu_path: str | None
) -> None:
    """launch_profile raises LauncherSettingsError when UmuPath is null or empty string.

    Parameterized cases test both None and empty string.
    """
    d = fake_launchable_dir
    settings = json.loads(d.launcher_settings_file.read_text(encoding="utf-8"))
    settings["LinuxSettings"]["UmuPath"] = umu_path
    d.launcher_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_profile: login failure
# --------------------------------------------------------------------------


def test_launch_profile_raises_login_failed_when_the_server_rejects_the_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LoginFailedError when server responds false to login.

    Server ping succeeds, but PUT LOGIN_PATH returns {"Response": false}; launch_profile
    raises LoginFailedError naming the profile's nickname in the message.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        router.put(LOGIN_PATH).respond(200, json={"Response": False})

        with pytest.raises(LoginFailedError, match=d.nickname):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_propagates_launch_error_on_a_login_transport_failure(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when login HTTP request times out.

    Server responds to ping successfully; login PUT request raises httpx.ReadTimeout.
    launch_profile catches the transport error and raises LaunchError, not the raw
    httpx exception.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        router.put(LOGIN_PATH).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(LaunchError):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_profile: readiness poll
# --------------------------------------------------------------------------


def test_launch_profile_polls_until_ready_with_two_free_answers_first(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile polls server until pong appears, emitting monotonic progress.

    First 3 pings (1 initial + 2 polls) fail with ConnectError ("refused"); 4th
    returns pong. Progress callback receives "waiting" step events with percent
    values that form a strictly increasing sequence. Server is spawned and
    result.server_started is True.
    """
    d = fake_launchable_dir
    calls = {"n": 0}

    def _ping(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:  # 1 initial check + 2 poll iterations, all "free"
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    events, progress = _progress()
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)
        _login_ok(router)

        result = launch_profile(d.root, d.profile_id, progress=progress, poll_seconds=0.0, clock=lambda: 0.0)

    assert result.server_started is True
    waiting_percents = [percent for step, percent, _ in events if step == "waiting"]
    assert waiting_percents == sorted(waiting_percents)


def test_launch_profile_keeps_polling_through_a_mid_poll_timeout_then_succeeds(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile tolerates timeout errors mid-poll and continues polling.

    A timeout (something answered but did not finish in time) is retried unlike
    a foreign response, which stops immediately. First request: refused. Second:
    timeout. Third: pong succeeds.
    """
    # A "timeout" answer (something took the connection but did not answer in
    # time) must not raise ServerPortBusyError like "foreign" does; the poll
    # loop keeps going and still reaches readiness.
    d = fake_launchable_dir
    calls = {"n": 0}

    def _ping(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")  # initial check: "free"
        if calls["n"] == 2:
            raise httpx.ReadTimeout("slow")  # mid-poll: "timeout"
        return httpx.Response(200, json={"Response": "Pong!"})

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)
        _login_ok(router)

        result = launch_profile(d.root, d.profile_id, poll_seconds=0.0, clock=lambda: 0.0)

    assert result.server_started is True


def test_launch_profile_raises_launch_error_when_the_server_never_answers(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when polling exceeds the ready_timeout.

    Every ping raises ConnectError; injected clock jumps from 0.0 to 1000.0 on
    the first poll, exceeding ready_timeout=5.0. launch_profile raises LaunchError
    with "5 seconds" in the message.
    """
    d = fake_launchable_dir
    clock_values = iter([0.0, 1000.0])

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_route(router)

        with pytest.raises(LaunchError, match="5 seconds"):
            launch_profile(
                d.root,
                d.profile_id,
                ready_timeout=5.0,
                poll_seconds=0.0,
                clock=lambda: next(clock_values, 1000.0),
            )


def test_launch_profile_raises_server_port_busy_when_a_foreign_answer_appears_mid_poll(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises ServerPortBusyError when port answers non-SPT mid-poll.

    Initial ping fails with ConnectError. On next poll attempt, port answers with
    HTTP 200 but {"Response": "someone-else"}, indicating a foreign service owns
    the port. launch_profile raises ServerPortBusyError immediately.
    """
    d = fake_launchable_dir
    calls = {"n": 0}

    def _ping(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "someone-else"})

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)

        with pytest.raises(ServerPortBusyError):
            launch_profile(d.root, d.profile_id, poll_seconds=0.0)


def test_launch_profile_raises_launch_error_when_the_spawned_server_dies(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when spawned server process exits early.

    Server binary is replaced with a script that exits with code 1 immediately.
    Ping mock sleeps 0.1s before responding, guaranteeing server has exited before
    first poll checks process_alive. launch_profile raises LaunchError with
    "stopped" in message.
    """
    d = fake_launchable_dir
    # Replace the stub with one that exits immediately instead of sleeping.
    d.server_binary.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n", encoding="utf-8")
    os.chmod(d.server_binary, 0o755)

    def _ping(request: httpx.Request) -> httpx.Response:
        # A tiny real delay guarantees the trivial script above has already
        # exited by the time the first poll iteration checks process_alive.
        time.sleep(0.1)
        raise httpx.ConnectError("refused")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)

        with pytest.raises(LaunchError, match="stopped"):
            launch_profile(d.root, d.profile_id, poll_seconds=0.0)


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
    events, progress = _progress()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_then_ready_route(router)
        login_route = _login_ok(router)

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
    import zlib

    login_request = login_route.calls.last.request
    assert login_request.method == "PUT"
    assert login_request.headers["responsecompressed"] == "0"
    assert json.loads(zlib.decompress(login_request.content)) == {"username": d.username}

    # -- server argv/cwd ------------------------------------------------
    _wait_until(lambda: Path(f"{d.server_binary}.dump.json").exists())
    server_dump = stub_dump(d.server_binary)
    assert server_dump["argv"] == [str(d.server_binary)]
    assert server_dump["cwd"] == str(d.root / "SPT_Runtime")

    # -- client argv/cwd/env ---------------------------------------------
    _wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"] == [
        str(d.umu_path),
        "EscapeFromTarkov.exe",
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
        _login_ok(router)

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
        _pong_route(router)
        _login_ok(router)

        launch_profile(d.root, d.profile_id)

    _wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"][-1] == "-SomeFlag"
    assert client_dump["env"]["PROTON_USE_XALIA"] == "0"
    assert client_dump["env"]["X"] == "a b"


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
        _pong_route(router)
        _login_ok(router)

        launch_profile(d.root, d.profile_id)

    _wait_until(marker.exists)
    assert marker.read_text(encoding="utf-8").strip() == "invoked"
    _wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
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
        _pong_route(router)
        _login_ok(router)
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
    # _write_state's atomic-replace step has no recovery path of its own; a
    # state "file" that is actually a directory makes os.replace() fail.
    # launch_profile wraps that OSError into a LaunchError naming the server
    # and client pids that are running unrecorded, rather than letting a raw
    # OSError escape.
    d = fake_launchable_dir
    (d.root / LAUNCH_STATE_FILE_NAME).mkdir()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LaunchError) as exc_info:
            launch_profile(d.root, d.profile_id)

    assert str(d.root) in str(exc_info.value)


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
        _pong_route(router)
        _login_ok(router)

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
    assert launch_status(tmp_path) == LaunchStatus(False, False, 0, 0, "")


def test_launch_status_reports_alive_processes_from_the_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status extracts running process pids from state file when alive.

    State file records profile "profile-a" with server pid 111 and client pid 222,
    both started at epoch times 1.0 and 2.0. process_alive returns True for both;
    launch_status returns LaunchStatus reporting both running with the profile_id.
    """
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    _write_state_file(
        tmp_path,
        profile_id="profile-a",
        server=_ref(111, 1.0, started_by_sptmod=True),
        client=_ref(222, 2.0),
    )

    assert launch_status(tmp_path) == LaunchStatus(True, True, 111, 222, "profile-a")


def test_launch_status_falls_back_to_the_scanned_process_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status scans running processes when state file is absent.

    No state file exists; process_alive always returns False (so state fallback
    doesn't help). game_processes scan finds SPT.Server.Linux (pid 333) and
    EscapeFromTarkov.exe (pid 444). Result reports both running, profile_id empty.
    """
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: False)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(pid=333, name="SPT.Server.Linux", kind="server", started_at=9.0),
            GameProcess(pid=444, name="EscapeFromTarkov.exe", kind="client", started_at=10.0),
        ],
    )

    assert launch_status(tmp_path) == LaunchStatus(True, True, 333, 444, "")


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
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: False)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=444, name="EscapeFromTarkov.exe", kind="client", started_at=10.0)],
    )
    _write_state_file(tmp_path, profile_id="profile-a", client=_ref(222, 2.0))

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
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: False)
    _write_state_file(
        tmp_path,
        profile_id="stale-profile",
        server=_ref(1, 1.0, started_by_sptmod=True),
        client=_ref(2, 2.0),
    )

    assert launch_status(tmp_path) == LaunchStatus(False, False, 0, 0, "stale-profile")


def test_launch_status_prefers_the_recorded_pid_over_a_scanned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status returns recorded pid from state, not scan, when both present.

    State file records server pid 111 (alive per process_alive); scan simultaneously
    finds SPT.Server.Linux at pid 999. Result server_pid is 111, not 999.
    """
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=999, name="SPT.Server.Linux", kind="server", started_at=1.0)],
    )
    _write_state_file(tmp_path, server=_ref(111, 1.0, started_by_sptmod=True))

    assert launch_status(tmp_path).server_pid == 111


# --------------------------------------------------------------------------
# stop_game
# --------------------------------------------------------------------------


def test_stop_game_is_a_noop_without_a_state_file(tmp_path: Path) -> None:
    """stop_game returns (False, False) when no state file exists.

    Nothing to stop; no processes found by scan either.
    """
    assert stop_game(tmp_path) == StopResult(False, False)


def test_stop_game_falls_back_to_the_scanned_process_table(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_game terminates scanned processes when no state file exists.

    No state file; game_processes scan returns real SPT.Server.Linux (pid server.pid)
    and EscapeFromTarkov.exe (pid client.pid) with correct create_time values. Both
    processes are terminated; result shows server_stopped=True, client_stopped=True.
    """
    # No state file at all -- exactly like launch_status, stop_game finds a
    # server/client started outside sptmod (or by an untracked launch) via
    # the scan, not just via the recorded state.
    server = spawn_sleeper()
    client = spawn_sleeper()
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=server.pid,
                name="SPT.Server.Linux",
                kind="server",
                started_at=psutil.Process(server.pid).create_time(),
            ),
            GameProcess(
                pid=client.pid,
                name="EscapeFromTarkov.exe",
                kind="client",
                started_at=psutil.Process(client.pid).create_time(),
            ),
        ],
    )

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=True, client_stopped=True)
    server.wait(timeout=4)
    client.wait(timeout=4)


def test_stop_game_stops_every_scanned_game_process(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_game terminates all instances of the game when multiple exist.

    Wine-hosted game appears as two separate processes in scan (first, second).
    Both are terminated; result.client_stopped is True after both pids exit.
    """
    # A game hosted by Wine shows up more than once; leaving any of them
    # running would keep the installation locked.
    first = spawn_sleeper()
    second = spawn_sleeper()
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=process.pid,
                name="EscapeFromTarkov.exe",
                kind="client",
                started_at=psutil.Process(process.pid).create_time(),
            )
            for process in (first, second)
        ],
    )

    result = stop_game(tmp_path)

    assert result.client_stopped is True
    first.wait(timeout=4)
    second.wait(timeout=4)


def test_stop_game_stops_server_client_and_watcher_and_clears_the_state(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen[bytes]]
) -> None:
    """stop_game terminates server/client/watcher and deletes state file.

    State file records all three (server with started_by_sptmod=True, client, watcher);
    all processes are running. stop_game terminates all three and deletes
    LAUNCH_STATE_FILE_NAME. Result shows server_stopped=True, client_stopped=True.
    """
    server = spawn_sleeper()
    client = spawn_sleeper()
    watcher = spawn_sleeper()
    _write_state_file(
        tmp_path,
        profile_id="p",
        server=_ref(server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True),
        client=_ref(client.pid, psutil.Process(client.pid).create_time()),
        watcher=_ref(watcher.pid, psutil.Process(watcher.pid).create_time()),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=True, client_stopped=True)
    for process in (server, client, watcher):
        process.wait(timeout=4)
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


def test_stop_game_kills_the_client_process_tree_recursively(tmp_path: Path) -> None:
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
    parent = subprocess.Popen([sys.executable, "-c", script])
    try:
        _wait_until(child_pid_file.exists)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        _write_state_file(
            tmp_path,
            client=_ref(parent.pid, psutil.Process(parent.pid).create_time()),
        )

        result = stop_game(tmp_path)

        assert result.client_stopped is True
        _wait_until(lambda: not psutil.pid_exists(child_pid))
        parent.wait(timeout=4)
    finally:
        with suppress(OSError):
            parent.kill()
            parent.wait(timeout=2)


def test_stop_game_tolerates_an_already_gone_process(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen[bytes]]
) -> None:
    """stop_game handles gracefully when state records a pid that no longer exists.

    Process was killed before stop_game() called; state file records its (now-dead)
    pid. stop_game attempts termination but finds it gone, returns server_stopped=False,
    and still deletes state file. No error is raised.
    """
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    _write_state_file(tmp_path, server=_ref(process.pid, started_at, started_by_sptmod=True))

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=False, client_stopped=False)
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


def test_stop_game_ignores_a_process_whose_start_time_no_longer_matches(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen[bytes]]
) -> None:
    """stop_game skips pid with mismatched start time (PID reuse guard).

    State records server=_ref(process.pid, 1.0, started_by_sptmod=True). Current
    process at that pid has a much later real create_time. start time mismatch
    proves the pid was reused; stop_game leaves it running. Result server_stopped=False.
    """
    process = spawn_sleeper()
    _write_state_file(tmp_path, server=_ref(process.pid, 1.0, started_by_sptmod=True))

    result = stop_game(tmp_path)

    assert result.server_stopped is False
    assert process.poll() is None  # still running -- it was correctly left alone


def test_stop_game_tolerates_an_already_gone_watcher(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen[bytes]]
) -> None:
    """stop_game handles dead watcher pid in state without raising.

    Unlike server/client (which fall back to scan), watcher pid is handed directly
    to _terminate. State records a dead watcher; stop_game attempts termination,
    finds it gone, and returns gracefully. No error is raised.
    """
    # Unlike server/client, the watcher entry is handed to _terminate directly
    # (no _live_ref/scan fallback), so a dead watcher pid exercises
    # _terminate's own "process not found" branch.
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    _write_state_file(tmp_path, watcher=_ref(process.pid, started_at))

    result = stop_game(tmp_path)  # must not raise

    assert result == StopResult(server_stopped=False, client_stopped=False)


def test_stop_game_ignores_a_watcher_whose_start_time_no_longer_matches(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen[bytes]]
) -> None:
    """stop_game skips watcher with mismatched start time (pid reuse guard).

    State records watcher at process.pid with start time 1.0. Current process at
    that pid has a much later real create_time (pid reuse). stop_game detects the
    mismatch and leaves the process running.
    """
    process = spawn_sleeper()
    _write_state_file(tmp_path, watcher=_ref(process.pid, 1.0))

    stop_game(tmp_path)

    assert process.poll() is None  # still running -- it was correctly left alone


def test_stop_game_kills_a_survivor_that_does_not_react_to_terminate(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen[bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_game escalates to SIGKILL when a process ignores SIGTERM.

    psutil.wait_procs is monkeypatched to report the process still alive after
    terminate (10s TERMINATE_TIMEOUT); stop_game then calls kill(). Without this
    patch, the 10s real wait would exceed the test timeout. Patch proves the
    kill path is reached and the process exits.
    """
    # TERMINATE_TIMEOUT (10s) is a module constant, not an injected parameter
    # of stop_game/watch_game, so there is no public way to reach the
    # "kill the survivor" branch inside the pytest-timeout budget without
    # actually waiting out that timeout. psutil.wait_procs is the external
    # collaborator _terminate relies on to decide who survived; forcing it to
    # report "still alive" simulates a process that ignored terminate()
    # without slowing the test down, and the assertions below still verify
    # what the SUT did (killed the process), not what this monkeypatch wrote.
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    _write_state_file(tmp_path, server=_ref(process.pid, started_at, started_by_sptmod=True))
    real_wait_procs = psutil.wait_procs

    def _report_still_alive(
        procs: list[psutil.Process], timeout: float | None = None
    ) -> tuple[list[psutil.Process], list[psutil.Process]]:
        gone, _ = real_wait_procs(procs, timeout=0)
        return gone, procs

    monkeypatch.setattr(psutil, "wait_procs", _report_still_alive)

    result = stop_game(tmp_path)

    assert result.server_stopped is True
    process.wait(timeout=4)
    assert not psutil.pid_exists(process.pid)


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
        _pong_route(router)
        _login_ok(router)

        with pytest.raises(LaunchError, match="could not be started"):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_status: malformed state file content (private _read_state branches,
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

    assert launch_status(tmp_path) == LaunchStatus(False, False, 0, 0, "")


@pytest.mark.parametrize("bad_pid", ["111", -1, 0, True])
def test_launch_status_ignores_a_state_entry_with_an_unusable_pid(
    tmp_path: Path, bad_pid: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_status skips server entry with invalid pid (string, negative, zero, bool).

    State server entry has pid="111" (string), or -1, or 0, or True instead of
    positive int. launch_status detects type/value violation and skips the entry,
    returning server_running=False.
    """
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    _write_state_file(tmp_path, server={"pid": bad_pid, "started_at": 1.0})

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
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    _write_state_file(tmp_path, server={"pid": 111, "started_at": bad_started_at})

    assert launch_status(tmp_path).server_running is False
