"""In-process ``sptmod rpc launch_profile|launch_status|stop_game`` tests:
these are the only tests exercising ``services.launch_profile``,
``services.launch_status`` and ``services.stop_game`` (the RPC contract's
thin wrappers around ``sptmod.core.launch``) and the four new error codes'
mapping through the real dispatcher. Behaviour of the underlying functions
themselves is covered exhaustively in ``tests/test_launch.py``.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import respx
from typer.testing import CliRunner

from conftest import LaunchableGameDir, invoke_rpc
from sptmod.core import game_dir
from sptmod.core.game_dir import GameProcess
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH

runner = CliRunner()


def _pong(router: respx.MockRouter) -> None:
    """Mock GET PING_PATH to respond with {\"Response\": \"Pong!\"} (HTTP 200).

    This stub is used to signal server ready state during launch flow tests.
    """
    router.get(PING_PATH).respond(200, json={"Response": "Pong!"})


def _free_then_ready(router: respx.MockRouter) -> None:
    """The initial check answers "free" once; every later poll answers "spt"."""
    calls = {"n": 0}

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    router.get(PING_PATH).mock(side_effect=_side_effect)


def _login(router: respx.MockRouter, ok: bool = True) -> None:
    """Mock PUT LOGIN_PATH to respond with {\"Response\": ok} (success when ok=True).

    Controls whether the launch flow succeeds or fails at the login step.
    """
    router.put(LOGIN_PATH).respond(200, json={"Response": ok})


def _not_running(monkeypatch: Any) -> None:
    """Stub game_dir.game_processes() to return [] (no running processes).

    Used to avoid interference from actual game processes during tests.
    """
    def no_game_processes() -> list[GameProcess]:
        return []

    monkeypatch.setattr(game_dir, "game_processes", no_game_processes)


def test_launch_profile_rpc_reports_the_four_phases_and_the_result(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile reports progress through server startup, client launch,
    and login phases before returning launch result."""
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_then_ready(router)
        _login(router)

        exit_code, lines = invoke_rpc("launch_profile", {"path": str(d.root), "profile_id": d.profile_id})

    assert exit_code == 0
    events = [doc["event"] for doc in lines]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
    steps = [doc["step"] for doc in lines[:-1]]
    assert steps[0] == "server"
    assert "waiting" in steps
    assert steps[-3:] == ["login", "client", "done"]
    result = lines[-1]["data"]
    assert result["profile_id"] == d.profile_id
    assert result["nickname"] == d.nickname
    assert result["server_started"] is True
    assert result["server_pid"] > 0
    assert result["client_pid"] > 0
    operation_id = result["operation_id"]
    assert all(doc.get("operation_id") == operation_id for doc in lines[:-1])


def test_launch_profile_rpc_maps_game_running_to_its_existing_code(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """When EscapeFromTarkov.exe is running, launch_profile returns GAME_RUNNING error.

    The RPC error code maps to error_code(GameRunningError) from the ERROR_MAPPING.
    """
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=1, name="EscapeFromTarkov.exe", kind="client", started_at=1.0)],
    )
    d = fake_launchable_dir

    exit_code, lines = invoke_rpc("launch_profile", {"path": str(d.root), "profile_id": d.profile_id})

    assert exit_code == 1
    assert lines[-1]["event"] == "error"
    assert lines[-1]["data"]["code"] == "GAME_RUNNING"


def test_launch_profile_rpc_maps_server_port_busy(fake_launchable_dir: LaunchableGameDir, monkeypatch: Any) -> None:
    """When PING_PATH answers with non-pong, launch_profile returns SERVER_PORT_BUSY.

    The detect detects a foreign service (not SPT) on the port; this error code
    prevents blindly killing an unrelated process.
    """
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).respond(200, json={"Response": "not-pong"})

        exit_code, lines = invoke_rpc("launch_profile", {"path": str(d.root), "profile_id": d.profile_id})

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "SERVER_PORT_BUSY"


def test_launch_profile_rpc_maps_login_failed(fake_launchable_dir: LaunchableGameDir, monkeypatch: Any) -> None:
    """When _login mocks ok=False, launch_profile returns LOGIN_FAILED error.

    The server responds to the login request with {Response: false}.
    """
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router, ok=False)

        exit_code, lines = invoke_rpc("launch_profile", {"path": str(d.root), "profile_id": d.profile_id})

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LOGIN_FAILED"


def test_launch_profile_rpc_maps_launcher_settings_missing(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """When launcher settings file is deleted, launch_profile returns LAUNCHER_SETTINGS_MISSING.

    The client launcher needs settings from LauncherSettingsFile; its absence
    raises LauncherSettingsError before attempting to start the game.
    """
    _not_running(monkeypatch)
    d = fake_launchable_dir
    d.launcher_settings_file.unlink()
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router)

        exit_code, lines = invoke_rpc("launch_profile", {"path": str(d.root), "profile_id": d.profile_id})

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LAUNCHER_SETTINGS_MISSING"


def test_launch_profile_rpc_maps_launch_failed(fake_launchable_dir: LaunchableGameDir, monkeypatch: Any) -> None:
    """When profile_id=\"no-such-profile\" is passed, launch_profile returns LAUNCH_FAILED.

    The service finds no profile file and raises LaunchError before attempting
    subprocess spawn.
    """
    _not_running(monkeypatch)
    d = fake_launchable_dir

    exit_code, lines = invoke_rpc("launch_profile", {"path": str(d.root), "profile_id": "no-such-profile"})

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LAUNCH_FAILED"


def test_launch_profile_rpc_forwards_stop_server_on_exit_false(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile(stop_server_on_exit=False) writes false to .sptmod-launch.json.

    The state file persists the parameter for later use by watch_game to decide
    whether to stop the server when the game exits.
    """
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router)

        exit_code, _lines = invoke_rpc(
            "launch_profile",
            {
                "path": str(d.root),
                "profile_id": d.profile_id,
                "stop_server_on_exit": False,
            },
        )

    assert exit_code == 0
    state = json.loads((d.root / ".sptmod-launch.json").read_text(encoding="utf-8"))
    assert state["stop_server_on_exit"] is False


def test_launch_status_rpc_reports_nothing_running_for_an_untouched_directory(
    tmp_path: Path,
) -> None:
    """launch_status on a fresh directory returns all running=False and pid=0.

    No .sptmod-launch.json file exists and no game processes are running.
    """
    exit_code, lines = invoke_rpc("launch_status", {"path": str(tmp_path)})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": {
                "server_running": False,
                "client_running": False,
                "server_pid": 0,
                "client_pid": 0,
                "profile_id": "",
            },
        }
    ]


def test_stop_game_rpc_is_a_noop_without_a_prior_launch(tmp_path: Path) -> None:
    """stop_game on a fresh directory returns stopped=False for both server and client.

    No state file exists and no game processes are running, so the operation
    completes without error but reports nothing was stopped.
    """
    exit_code, lines = invoke_rpc("stop_game", {"path": str(tmp_path)})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": {"server_stopped": False, "client_stopped": False},
        }
    ]
