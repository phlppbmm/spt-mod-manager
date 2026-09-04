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
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH

pytestmark = pytest.mark.timeout(5)


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test here from whatever this sandbox happens to run.

    Individual tests override this again through the same ``monkeypatch``
    fixture when they need a specific scan result.
    """
    monkeypatch.setattr(game_dir, "game_processes", lambda: [])


def _progress() -> tuple[list[tuple[str, int, str]], Callable[[str, int, str], None]]:
    events: list[tuple[str, int, str]] = []

    def _capture(step: str, percent: int, message: str) -> None:
        events.append((step, percent, message))

    return events, _capture


def _pong_route(router: respx.MockRouter) -> respx.Route:
    return router.get(PING_PATH).respond(200, json={"Response": "Pong!"})


def _free_route(router: respx.MockRouter) -> respx.Route:
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
    return router.put(LOGIN_PATH).respond(200, json={"Response": True})


def _wait_until(predicate: Callable[[], bool], timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was never met")


def _state_payload(root: Path) -> dict[str, Any]:
    return json.loads((root / LAUNCH_STATE_FILE_NAME).read_text(encoding="utf-8"))


def _write_state_file(
    root: Path,
    *,
    profile_id: str = "",
    stop_server_on_exit: bool = True,
    server: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
    watcher: dict[str, Any] | None = None,
) -> None:
    payload = {
        "profile_id": profile_id,
        "stop_server_on_exit": stop_server_on_exit,
        "server": server,
        "client": client,
        "watcher": watcher,
    }
    (root / LAUNCH_STATE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _ref(pid: int, started_at: float, *, started_by_sptmod: bool | None = None) -> dict:
    payload: dict[str, Any] = {"pid": pid, "started_at": started_at}
    if started_by_sptmod is not None:
        payload["started_by_sptmod"] = started_by_sptmod
    return payload


@pytest.fixture
def spawn_sleeper() -> Iterator[Callable[[], subprocess.Popen]]:
    """Factory spawning a throwaway real process that just sleeps."""
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
    assert tokenize_launch_settings(text) == expected


# --------------------------------------------------------------------------
# launch_profile: refusal before any HTTP call
# --------------------------------------------------------------------------


def test_launch_profile_raises_not_a_game_dir_for_a_bad_path(tmp_path: Path) -> None:
    with pytest.raises(NotAGameDirError):
        launch_profile(tmp_path / "does-not-exist", "any-profile")


def test_launch_profile_refuses_a_running_client(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=1, name="EscapeFromTarkov.exe", kind="client", started_at=1.0
            )
        ],
    )

    with pytest.raises(GameRunningError, match="EscapeFromTarkov.exe"):
        launch_profile(fake_launchable_dir.root, fake_launchable_dir.profile_id)


def test_launch_profile_reuses_an_already_running_server(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A server started by someone else (or a previous launch) is running, but
    # sptmod has no process record of it -- the scan is the only signal.
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=555, name="SPT.Server.Linux", kind="server", started_at=42.0
            )
        ],
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
    with pytest.raises(LaunchError, match="no-such-profile"):
        launch_profile(fake_launchable_dir.root, "no-such-profile")


@pytest.mark.parametrize(
    "profile_id",
    ["", ".", "..", "../escape", "a/b", "a\\b", "/etc/passwd"],
)
def test_launch_profile_rejects_a_profile_id_that_is_not_a_bare_file_stem(
    fake_launchable_dir: LaunchableGameDir, profile_id: str
) -> None:
    with pytest.raises(LaunchError):
        launch_profile(fake_launchable_dir.root, profile_id)


def test_launch_profile_raises_launch_error_for_an_unreadable_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text("not json", encoding="utf-8")

    with pytest.raises(LaunchError):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_when_username_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text(
        json.dumps({"info": {"id": d.profile_id}}), encoding="utf-8"
    )

    with pytest.raises(LaunchError, match=d.profile_id):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_falls_back_to_the_account_name_when_nickname_is_absent(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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
    d = fake_launchable_dir
    d.server_binary.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_route(router)

        with pytest.raises(LaunchError, match="SPT.Server.Linux"):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_for_a_missing_client_executable(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        router.put(LOGIN_PATH).respond(200, json={"Response": False})

        with pytest.raises(LoginFailedError, match=d.nickname):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_propagates_launch_error_on_a_login_transport_failure(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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

        result = launch_profile(
            d.root, d.profile_id, progress=progress, poll_seconds=0.0, clock=lambda: 0.0
        )

    assert result.server_started is True
    waiting_percents = [percent for step, percent, _ in events if step == "waiting"]
    assert waiting_percents == sorted(waiting_percents)


def test_launch_profile_keeps_polling_through_a_mid_poll_timeout_then_succeeds(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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

        result = launch_profile(
            d.root, d.profile_id, poll_seconds=0.0, clock=lambda: 0.0
        )

    assert result.server_started is True


def test_launch_profile_raises_launch_error_when_the_server_never_answers(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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
    d = fake_launchable_dir
    # Replace the stub with one that exits immediately instead of sleeping.
    d.server_binary.write_text(
        f"#!{sys.executable}\nimport sys\nsys.exit(1)\n", encoding="utf-8"
    )
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
    assert json.loads(zlib.decompress(login_request.content)) == {
        "username": d.username
    }

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
        f"-config={{'BackendUrl':'{d.base_url}',"
        f"'Version':'live','MatchingVersion':'live'}}",
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
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_then_ready_route(router)
        _login_ok(router)

        launch_profile(d.root, d.profile_id, False, poll_seconds=0.0)

    assert _state_payload(d.root)["stop_server_on_exit"] is False


def test_launch_profile_tokenizes_launch_settings_with_quotes_and_extra_flags(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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
    gamemoderun.write_text(
        f'#!/bin/sh\necho invoked > "{marker}"\nexec "$@"\n', encoding="utf-8"
    )
    gamemoderun.chmod(gamemoderun.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        launch_profile(d.root, d.profile_id)

    _wait_until(lambda: marker.exists())
    assert marker.read_text(encoding="utf-8").strip() == "invoked"
    _wait_until(lambda: Path(f"{d.umu_path}.dump.json").exists())
    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"][0] == str(d.umu_path)


def test_launch_profile_records_zero_started_at_when_the_process_vanishes_instantly(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    d = fake_launchable_dir
    (d.root / LAUNCH_LOCK_FILE_NAME).touch()

    with pytest.raises(LaunchError, match="already in progress"):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_when_the_lock_file_cannot_be_created(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong_route(router)
        _login_ok(router)

        launch_profile(d.root, d.profile_id)

    assert not (d.root / LAUNCH_LOCK_FILE_NAME).exists()


def test_launch_profile_removes_the_lock_file_after_a_failed_launch(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
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
    assert launch_status(tmp_path) == LaunchStatus(False, False, 0, 0, "")


def test_launch_status_reports_alive_processes_from_the_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: False)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=333, name="SPT.Server.Linux", kind="server", started_at=9.0
            ),
            GameProcess(
                pid=444, name="EscapeFromTarkov.exe", kind="client", started_at=10.0
            ),
        ],
    )

    assert launch_status(tmp_path) == LaunchStatus(True, True, 333, 444, "")


def test_launch_status_reports_the_scanned_game_when_the_recorded_pid_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Linux case: the process the launch spawned was only the wrapper and
    # is gone, while the game itself still runs under its own name.
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: False)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=444, name="EscapeFromTarkov.exe", kind="client", started_at=10.0
            )
        ],
    )
    _write_state_file(tmp_path, profile_id="profile-a", client=_ref(222, 2.0))

    status = launch_status(tmp_path)

    assert status.client_running is True
    assert status.client_pid == 444


def test_launch_status_keeps_the_recorded_profile_id_even_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(pid=999, name="SPT.Server.Linux", kind="server", started_at=1.0)
        ],
    )
    _write_state_file(tmp_path, server=_ref(111, 1.0, started_by_sptmod=True))

    assert launch_status(tmp_path).server_pid == 111


# --------------------------------------------------------------------------
# stop_game
# --------------------------------------------------------------------------


def test_stop_game_is_a_noop_without_a_state_file(tmp_path: Path) -> None:
    assert stop_game(tmp_path) == StopResult(False, False)


def test_stop_game_falls_back_to_the_scanned_process_table(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    spawn_sleeper: Callable[[], subprocess.Popen],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    server = spawn_sleeper()
    client = spawn_sleeper()
    watcher = spawn_sleeper()
    _write_state_file(
        tmp_path,
        profile_id="p",
        server=_ref(
            server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True
        ),
        client=_ref(client.pid, psutil.Process(client.pid).create_time()),
        watcher=_ref(watcher.pid, psutil.Process(watcher.pid).create_time()),
    )

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=True, client_stopped=True)
    for process in (server, client, watcher):
        process.wait(timeout=4)
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


def test_stop_game_kills_the_client_process_tree_recursively(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen(['sleep', '30'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(30)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script])
    try:
        _wait_until(lambda: child_pid_file.exists())
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
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    process = spawn_sleeper()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    _write_state_file(
        tmp_path, server=_ref(process.pid, started_at, started_by_sptmod=True)
    )

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=False, client_stopped=False)
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


def test_stop_game_ignores_a_process_whose_start_time_no_longer_matches(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    process = spawn_sleeper()
    _write_state_file(tmp_path, server=_ref(process.pid, 1.0, started_by_sptmod=True))

    result = stop_game(tmp_path)

    assert result.server_stopped is False
    assert process.poll() is None  # still running -- it was correctly left alone


def test_stop_game_tolerates_an_already_gone_watcher(
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
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
    tmp_path: Path, spawn_sleeper: Callable[[], subprocess.Popen]
) -> None:
    process = spawn_sleeper()
    _write_state_file(tmp_path, watcher=_ref(process.pid, 1.0))

    stop_game(tmp_path)

    assert process.poll() is None  # still running -- it was correctly left alone


def test_stop_game_kills_a_survivor_that_does_not_react_to_terminate(
    tmp_path: Path,
    spawn_sleeper: Callable[[], subprocess.Popen],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    _write_state_file(
        tmp_path, server=_ref(process.pid, started_at, started_by_sptmod=True)
    )
    real_wait_procs = psutil.wait_procs

    def _report_still_alive(procs: list, timeout: float | None = None) -> tuple:
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
    (tmp_path / LAUNCH_STATE_FILE_NAME).write_text(
        json.dumps([1, 2, 3]), encoding="utf-8"
    )

    assert launch_status(tmp_path) == LaunchStatus(False, False, 0, 0, "")


@pytest.mark.parametrize("bad_pid", ["111", -1, 0, True])
def test_launch_status_ignores_a_state_entry_with_an_unusable_pid(
    tmp_path: Path, bad_pid: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    _write_state_file(tmp_path, server={"pid": bad_pid, "started_at": 1.0})

    assert launch_status(tmp_path).server_running is False


@pytest.mark.parametrize("bad_started_at", ["1.0", True])
def test_launch_status_ignores_a_state_entry_with_an_unusable_started_at(
    tmp_path: Path, bad_started_at: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(game_dir, "process_alive", lambda pid, started_at: True)
    _write_state_file(tmp_path, server={"pid": 111, "started_at": bad_started_at})

    assert launch_status(tmp_path).server_running is False
