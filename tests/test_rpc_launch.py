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

from conftest import LaunchableGameDir
from sptmod.cli import app
from sptmod.core import game_dir
from sptmod.core.game_dir import GameProcess
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH

runner = CliRunner()


def _invoke(name: str, request: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """Run ``rpc <name>`` and return ``(exit_code, parsed stdout lines)``."""
    result = runner.invoke(app, ["rpc", name], input=json.dumps(request))
    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    return result.exit_code, lines


def _pong(router: respx.MockRouter) -> None:
    """Mock the SPT server to respond successfully to ping requests."""
    router.get(PING_PATH).respond(200, json={"Response": "Pong!"})


def _free_then_ready(router: respx.MockRouter) -> None:
    """The initial check answers "free" once; every later poll answers "spt"."""
    calls = {"n": 0}

    def _side_effect(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    router.get(PING_PATH).mock(side_effect=_side_effect)


def _login(router: respx.MockRouter, ok: bool = True) -> None:
    """Mock the SPT server login endpoint to succeed or fail based on ``ok``."""
    router.put(LOGIN_PATH).respond(200, json={"Response": ok})


def _not_running(monkeypatch: Any) -> None:
    """Mock ``game_processes()`` to report no running processes."""
    monkeypatch.setattr(game_dir, "game_processes", lambda: [])


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

        exit_code, lines = _invoke(
            "launch_profile", {"path": str(d.root), "profile_id": d.profile_id}
        )

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
    """launch_profile returns GAME_RUNNING error if the game process is
    already running."""
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [
            GameProcess(
                pid=1, name="EscapeFromTarkov.exe", kind="client", started_at=1.0
            )
        ],
    )
    d = fake_launchable_dir

    exit_code, lines = _invoke(
        "launch_profile", {"path": str(d.root), "profile_id": d.profile_id}
    )

    assert exit_code == 1
    assert lines[-1]["event"] == "error"
    assert lines[-1]["data"]["code"] == "GAME_RUNNING"


def test_launch_profile_rpc_maps_server_port_busy(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile returns SERVER_PORT_BUSY error when the server port is
    already in use."""
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).respond(200, json={"Response": "not-pong"})

        exit_code, lines = _invoke(
            "launch_profile", {"path": str(d.root), "profile_id": d.profile_id}
        )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "SERVER_PORT_BUSY"


def test_launch_profile_rpc_maps_login_failed(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile returns LOGIN_FAILED error when server login is
    rejected."""
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router, ok=False)

        exit_code, lines = _invoke(
            "launch_profile", {"path": str(d.root), "profile_id": d.profile_id}
        )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LOGIN_FAILED"


def test_launch_profile_rpc_maps_launcher_settings_missing(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile returns LAUNCHER_SETTINGS_MISSING error when client
    launcher settings are not found."""
    _not_running(monkeypatch)
    d = fake_launchable_dir
    d.launcher_settings_file.unlink()
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router)

        exit_code, lines = _invoke(
            "launch_profile", {"path": str(d.root), "profile_id": d.profile_id}
        )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LAUNCHER_SETTINGS_MISSING"


def test_launch_profile_rpc_maps_launch_failed(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile returns LAUNCH_FAILED error when launching the client
    process fails."""
    _not_running(monkeypatch)
    d = fake_launchable_dir

    exit_code, lines = _invoke(
        "launch_profile", {"path": str(d.root), "profile_id": "no-such-profile"}
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LAUNCH_FAILED"


def test_launch_profile_rpc_forwards_stop_server_on_exit_false(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: Any
) -> None:
    """launch_profile with stop_server_on_exit=false persists the setting to
    the launch state file."""
    _not_running(monkeypatch)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router)

        exit_code, _lines = _invoke(
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
    """launch_status reports no running processes when none have been
    launched."""
    exit_code, lines = _invoke("launch_status", {"path": str(tmp_path)})

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
    """stop_game does nothing when no processes have been launched."""
    exit_code, lines = _invoke("stop_game", {"path": str(tmp_path)})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": {"server_stopped": False, "client_stopped": False},
        }
    ]
