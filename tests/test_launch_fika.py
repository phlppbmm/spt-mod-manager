"""Tests for launching a game on another player's server (Fika client mode).

Everything runs against the stub binaries of ``fake_launchable_dir`` and a
respx-mocked host at the documentation address ``192.0.2.10``; the process
table is injected by an autouse fixture, so no test looks at, starts or
signals anything but the throwaway processes it created itself.
"""

import json
import subprocess
import zlib
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
import respx

from conftest import (
    LaunchableGameDir,
    kill_launchable_processes,
    process_ref,
    stub_dump,
    write_launch_state,
)
from fixture_types import SpawnSleeper
from sptmod.core import game_dir, services
from sptmod.core.errors import FikaHostUnreachableError, LaunchError, LoginFailedError
from sptmod.core.fika import FikaSettings, save_settings
from sptmod.core.game_dir import GameProcess
from sptmod.core.launch import (
    LAUNCH_LOG_FILE_NAME,
    LAUNCH_STATE_FILE_NAME,
    LaunchResult,
    LaunchStatus,
    StopResult,
    launch_profile,
    launch_status,
    stop_game,
    watch_game,
)
from sptmod.core.models import Progress
from sptmod.core.modsets import store_key
from sptmod.core.repo import Repo
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH, PROFILES_PATH

pytestmark = pytest.mark.timeout(5)

HOST = "https://192.0.2.10:6969"
HOST_PROFILE_ID = "aaa111hostprofile"
HOST_ACCOUNT = "HostAccount"
HOST_NICKNAME = "HostNick"


def _no_game_processes() -> list[GameProcess]:
    """Stand in for ``game_dir.game_processes`` when nothing runs."""
    return []


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject an empty process table for every test in this module.

    The developer may be playing SPT while these tests run; the launch guard
    and every process lookup must see only what a test put there, never the
    real game.
    """
    monkeypatch.setattr(game_dir, "game_processes", _no_game_processes)


def _progress() -> tuple[list[tuple[str, int, str]], Progress]:
    """Return the recorded progress events and the callback filling them."""
    events: list[tuple[str, int, str]] = []

    def _capture(step: str, percent: int, message: str) -> None:
        events.append((step, percent, message))

    return events, _capture


def _host_profile(
    profile_id: str = HOST_PROFILE_ID, *, username: str = HOST_ACCOUNT, nickname: str = HOST_NICKNAME
) -> dict[str, Any]:
    """One mini profile as the host's launcher API lists it."""
    return {
        "profileId": profile_id,
        "username": username,
        "nickname": nickname,
        "currlvl": 42,
        "side": "Usec",
        "hasPassword": False,
    }


def _host_answers(
    router: respx.MockRouter, *, profiles: list[dict[str, Any]] | None = None, login: bool = True
) -> tuple[respx.Route, respx.Route]:
    """Let the host answer as a running SPT server and return its two routes."""
    router.get(PING_PATH).respond(200, json={"Response": "Pong!"})
    listed = [_host_profile()] if profiles is None else profiles
    profiles_route = router.get(PROFILES_PATH).respond(200, json={"Response": listed})
    login_route = router.put(LOGIN_PATH).respond(200, json={"Response": login})
    return profiles_route, login_route


def _local_server_answers(router: respx.MockRouter) -> None:
    """Let the local address answer "free" once and as an SPT server after.

    That is what a launch on this machine's own server sees: nothing listens,
    so the server is started here, and every later poll finds it answering.
    """
    calls = {"n": 0}

    def _ping(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    router.get(PING_PATH).mock(side_effect=_ping)
    router.put(LOGIN_PATH).respond(200, json={"Response": True})


def _state_payload(root: Path) -> dict[str, Any]:
    """Return the parsed launch state file of the game directory."""
    payload: dict[str, Any] = json.loads((root / LAUNCH_STATE_FILE_NAME).read_text(encoding="utf-8"))
    return payload


def _log_text(root: Path) -> str:
    """Return the launch log of a game directory, empty when there is none."""
    log_file = root / LAUNCH_LOG_FILE_NAME
    return log_file.read_text(encoding="utf-8") if log_file.exists() else ""


def _live_ref(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    """Build a state-file process reference for a running throwaway process."""
    return process_ref(process.pid, psutil.Process(process.pid).create_time())


def _scanned_server(process: subprocess.Popen[bytes]) -> GameProcess:
    """Describe a throwaway process as the process table describes a server."""
    return GameProcess(
        pid=process.pid,
        name="SPT.Server.Linux",
        kind="server",
        started_at=psutil.Process(process.pid).create_time(),
    )


# --- the launch on another player's server ----------------------------------


def test_a_launch_on_a_host_starts_only_the_game_and_points_it_at_that_host(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """If the client branch were dropped, this fails: a local server would be
    started, the game would be pointed at ``127.0.0.1`` and the player name
    would come from a local profile file the joining machine does not have.
    The profile played here exists only on the host, so every piece of it --
    account name, nickname, token -- must come from the host's answer."""
    d = fake_launchable_dir
    events, progress = _progress()

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        profiles_route, login_route = _host_answers(router)

        result = launch_profile(d.root, HOST_PROFILE_ID, True, progress, fika_host=HOST)

    assert result == LaunchResult(
        profile_id=HOST_PROFILE_ID,
        nickname=HOST_NICKNAME,
        server_pid=0,
        client_pid=result.client_pid,
        server_started=False,
    )
    assert result.client_pid > 0
    assert events == [
        ("host", 5, f"Contacting the host at {HOST}"),
        ("host", 40, "Reading the profiles on the host"),
        ("login", 80, f"Signing in as {HOST_NICKNAME}"),
        ("client", 95, "Starting the game"),
        ("done", 100, "The game is starting"),
    ]
    assert profiles_route.called
    assert json.loads(zlib.decompress(login_route.calls.last.request.content)) == {"username": HOST_ACCOUNT}
    assert not Path(f"{d.server_binary}.dump.json").exists()

    client_dump = stub_dump(d.umu_path)
    assert client_dump["argv"] == [
        str(d.umu_path),
        str(d.client_exe),
        "-force-gfx-jobs",
        "native",
        f"-token={HOST_PROFILE_ID}",
        f"-config={{'BackendUrl':'{HOST}','Version':'live','MatchingVersion':'live'}}",
    ]

    state = _state_payload(d.root)
    assert state["mode"] == "client"
    assert state["server"] is None
    assert state["client"]["pid"] == result.client_pid
    assert isinstance(state["watcher"]["pid"], int)

    log = _log_text(d.root)
    assert f"Connecting to the host at {HOST}." in log
    assert f"The host signed {HOST_NICKNAME} in." in log

    kill_launchable_processes(d.root)


def test_after_a_launch_on_a_host_no_server_is_reported_as_running(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """If a server were remembered for a launch on a host, this fails: the
    app would offer to close a server that belongs to another player, and the
    launch state would claim a server this machine never started."""
    d = fake_launchable_dir

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        _host_answers(router)

        result = launch_profile(d.root, HOST_PROFILE_ID, fika_host=HOST)

    assert launch_status(d.root) == LaunchStatus(
        server_running=False,
        client_running=True,
        server_pid=0,
        client_pid=result.client_pid,
        profile_id=HOST_PROFILE_ID,
    )

    kill_launchable_processes(d.root)


# --- the host does not answer as an SPT server ------------------------------


@pytest.mark.parametrize(
    "answer,expected",
    [
        pytest.param(
            httpx.ConnectError("refused"),
            f"No SPT server answers at {HOST}. Ask the host to start their server, then try again.",
            id="nothing-answers-at-the-address",
        ),
        pytest.param(
            httpx.ReadTimeout("slow"),
            f"The SPT server at {HOST} did not answer in time. Check the address and the connection to the host.",
            id="the-host-does-not-answer-in-time",
        ),
        pytest.param(
            httpx.Response(200, json={"Response": "not-pong"}),
            f"Something at {HOST} answered, but it is not an SPT server. Check the address.",
            id="something-else-holds-the-address",
        ),
    ],
)
def test_a_host_that_is_no_spt_server_refuses_the_launch_with_its_own_reason(
    fake_launchable_dir: LaunchableGameDir, answer: Any, expected: str
) -> None:
    """If the three cases shared one message, this fails: a player would be
    told to check the address when the host simply has not started their
    server yet. Nothing is started and nothing is remembered, so a second
    attempt begins from the same state."""
    d = fake_launchable_dir

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        if isinstance(answer, httpx.Response):
            router.get(PING_PATH).mock(return_value=answer)
        else:
            router.get(PING_PATH).mock(side_effect=answer)

        with pytest.raises(FikaHostUnreachableError) as failure:
            launch_profile(d.root, HOST_PROFILE_ID, fika_host=HOST)

    assert str(failure.value) == expected
    assert not (d.root / LAUNCH_STATE_FILE_NAME).exists()
    assert not Path(f"{d.server_binary}.dump.json").exists()
    assert not Path(f"{d.umu_path}.dump.json").exists()


def test_a_profile_the_host_does_not_offer_refuses_the_launch(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """If the lookup accepted any answer, this fails: the game would start
    with a token the host does not know, ending in a raid that cannot be
    joined instead of a message naming the host."""
    d = fake_launchable_dir

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        _host_answers(router, profiles=[_host_profile("someone-else")])

        with pytest.raises(LaunchError, match="has no profile with this id"):
            launch_profile(d.root, HOST_PROFILE_ID, fika_host=HOST)

    assert not Path(f"{d.umu_path}.dump.json").exists()


def test_a_host_refusing_the_sign_in_names_the_profile_and_the_password(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """If the sign-in answer were ignored, this fails: the game would start
    although the host did not accept the profile. The message names the
    profile and the one reason a host refuses one."""
    d = fake_launchable_dir

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        _host_answers(router, login=False)

        with pytest.raises(LoginFailedError, match=f"did not accept the profile {HOST_NICKNAME}"):
            launch_profile(d.root, HOST_PROFILE_ID, fika_host=HOST)

    assert not Path(f"{d.umu_path}.dump.json").exists()


@pytest.mark.parametrize(
    "mode,server_is_ours",
    [
        pytest.param("client", False, id="a-game-played-on-another-players-server"),
        pytest.param("", True, id="a-game-played-on-the-server-of-this-machine"),
    ],
)
def test_a_server_in_the_process_table_belongs_to_the_host_while_a_game_joins_one(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    server_is_ours: bool,
) -> None:
    """If the mode were ignored while reading the status, this fails: the same
    process table would report a running server for both modes, so a joining
    player would be offered a "stop the server" button for a server that is
    not theirs. The state file remembers no server at all, so the process
    table is the only source -- and only the local launch may use it."""
    server = spawn_sleeper()
    monkeypatch.setattr(game_dir, "game_processes", lambda: [_scanned_server(server)])
    write_launch_state(tmp_path, profile_id=HOST_PROFILE_ID, mode=mode)

    assert launch_status(tmp_path) == LaunchStatus(
        server_running=server_is_ours,
        client_running=False,
        server_pid=server.pid if server_is_ours else 0,
        client_pid=0,
        profile_id=HOST_PROFILE_ID,
    )


# --- stopping a game that was played on another player's server -------------


def test_stopping_a_game_played_on_a_host_closes_the_game_and_no_server(
    tmp_path: Path, spawn_sleeper: SpawnSleeper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the mode were ignored while stopping, this fails: the server found
    in the process table would be closed -- on a machine that joins a session
    that is the server of the player who is hosting it, or one this player
    runs for a different session."""
    server = spawn_sleeper()
    client = spawn_sleeper()
    monkeypatch.setattr(game_dir, "game_processes", lambda: [_scanned_server(server)])
    write_launch_state(tmp_path, profile_id=HOST_PROFILE_ID, client=_live_ref(client), mode="client")

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=False, client_stopped=True)
    client.wait(timeout=4)
    assert server.poll() is None
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("", id="a-launch-on-the-local-server"),
        pytest.param(None, id="a-state-file-from-before-fika"),
        pytest.param(42, id="a-mode-that-is-no-text"),
    ],
)
def test_stopping_a_game_played_here_still_closes_the_local_server(
    tmp_path: Path, spawn_sleeper: SpawnSleeper, monkeypatch: pytest.MonkeyPatch, mode: Any
) -> None:
    """If every launch counted as one on a host, this fails: the local server
    would be left running after the game was closed. A state file written
    before Fika existed, or holding an unreadable mode, has to keep behaving
    like the local launch it describes."""
    server = spawn_sleeper()
    client = spawn_sleeper()
    monkeypatch.setattr(game_dir, "game_processes", lambda: [_scanned_server(server)])
    payload: dict[str, Any] = {
        "profile_id": HOST_PROFILE_ID,
        "stop_server_on_exit": True,
        "server": None,
        "client": _live_ref(client),
        "watcher": None,
    }
    if mode is not None:
        payload["mode"] = mode
    (tmp_path / LAUNCH_STATE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")

    result = stop_game(tmp_path)

    assert result == StopResult(server_stopped=True, client_stopped=True)
    server.wait(timeout=4)
    client.wait(timeout=4)


def test_the_watcher_of_a_game_played_on_a_host_closes_no_server(
    tmp_path: Path, spawn_sleeper: SpawnSleeper
) -> None:
    """If the mode guard were dropped, this fails: the watcher would close the
    server named in the state although it belongs to another player, and the
    log would claim it closed 'the SPT server' instead of saying there was
    nothing here to close."""
    server = spawn_sleeper()
    client = spawn_sleeper()
    write_launch_state(
        tmp_path,
        profile_id=HOST_PROFILE_ID,
        stop_server_on_exit=True,
        server=process_ref(server.pid, psutil.Process(server.pid).create_time(), started_by_sptmod=True),
        client=_live_ref(client),
        mode="client",
    )

    def _sleep(_seconds: float) -> None:
        client.kill()
        client.wait(timeout=4)

    watch_game(tmp_path, poll_seconds=0.0, grace_seconds=0.0, sleep=_sleep, clock=lambda: 0.0)

    assert "The game was played on another player's server; nothing to close here." in _log_text(tmp_path)
    assert "The SPT server was closed." not in _log_text(tmp_path)
    assert server.poll() is None
    assert not (tmp_path / LAUNCH_STATE_FILE_NAME).exists()


# --- the role stored for the game directory ---------------------------------


def test_a_directory_that_joins_a_host_is_launched_on_that_host(
    fake_launchable_dir: LaunchableGameDir, fika_store: Path, mod_set_store: Path
) -> None:
    """If the stored role were not read, this fails: the launch would start a
    local server and sign in the local profile instead of playing on the host
    a player picked in the settings."""
    d = fake_launchable_dir
    services.set_fika_settings(d.root, "client", "192.0.2.10", fika_path=fika_store)

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        profiles_route, _login_route = _host_answers(router)

        result = services.launch_profile(
            d.root, HOST_PROFILE_ID, store_path=mod_set_store, fika_path=fika_store
        )

    assert result.nickname == HOST_NICKNAME
    assert result.server_started is False
    assert result.server_pid == 0
    assert profiles_route.called
    assert not Path(f"{d.server_binary}.dump.json").exists()
    assert _state_payload(d.root)["mode"] == "client"

    kill_launchable_processes(d.root)


def test_joining_a_host_whose_stored_address_is_unusable_refuses_before_asking_anyone(
    fake_launchable_dir: LaunchableGameDir, fika_store: Path, mod_set_store: Path
) -> None:
    """If the guard were missing, this fails: the launch would ask the empty
    address and end in a connection error, or fall back to the local server
    and play the wrong session. Nothing is asked and nothing is started."""
    d = fake_launchable_dir
    save_settings(fika_store, store_key(d.root), FikaSettings(role="client", host_address="two words"))

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        ping_route = router.get(PING_PATH).respond(200, json={"Response": "Pong!"})

        with pytest.raises(LaunchError, match="No usable address is set for the Fika host"):
            services.launch_profile(d.root, d.profile_id, store_path=mod_set_store, fika_path=fika_store)

    assert not ping_route.called
    assert not (d.root / LAUNCH_STATE_FILE_NAME).exists()
    assert not Path(f"{d.server_binary}.dump.json").exists()


@pytest.mark.parametrize(
    "role,address",
    [
        pytest.param("none", "", id="playing-alone"),
        pytest.param("host", "192.0.2.10", id="hosting-the-session-for-others"),
    ],
)
def test_a_directory_that_does_not_join_a_host_launches_on_its_own_server(
    fake_launchable_dir: LaunchableGameDir,
    fika_store: Path,
    mod_set_store: Path,
    role: services.FikaRole,
    address: str,
) -> None:
    """If the host branch were taken for every stored address, this fails: the
    player hosting the session would be sent to their own server as a client
    and never start it. Hosting means running the local server, so both roles
    launch exactly as before Fika existed."""
    d = fake_launchable_dir
    services.set_fika_settings(d.root, role, address, fika_path=fika_store)
    events, progress = _progress()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _local_server_answers(router)

        result = services.launch_profile(
            d.root, d.profile_id, progress=progress, store_path=mod_set_store, fika_path=fika_store
        )

    assert result.nickname == d.nickname
    assert result.server_started is True
    assert result.server_pid > 0
    assert events[0][0] == "server"
    assert _state_payload(d.root)["mode"] == ""

    kill_launchable_processes(d.root)


def test_a_bound_mod_set_is_applied_before_the_host_is_contacted(
    launchable_repo: tuple[LaunchableGameDir, Repo],
    fika_store: Path,
    mod_set_store: Path,
) -> None:
    """If the mod set step were skipped for a launch on a host, this fails:
    the joining player would enter the session with different mods than the
    set demands, which is exactly what a shared set exists to prevent."""
    d, _repo = launchable_repo
    services.set_fika_settings(d.root, "client", "192.0.2.10", fika_path=fika_store)
    services.save_mod_set(d.root, "Fika night", entries=[], store_path=mod_set_store)
    services.bind_profile_set(d.root, HOST_PROFILE_ID, "Fika night", store_path=mod_set_store)
    events, progress = _progress()

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        _host_answers(router)

        services.launch_profile(
            d.root, HOST_PROFILE_ID, progress=progress, store_path=mod_set_store, fika_path=fika_store
        )

    steps = [step for step, _percent, _message in events]
    assert steps[0] == "modset"
    assert [step for step in steps if step != "modset"] == ["host", "host", "login", "client", "done"]

    kill_launchable_processes(d.root)
