"""Launch and process fixtures for the sptmod test suite.

Registered through ``pytest_plugins`` in :mod:`conftest`.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import pytest

from fixture_types import JsonDict, SpawnProcess, SpawnSleeper
from sptmod.core.game_dir import detect_layout
from sptmod.core.launch import LAUNCH_STATE_FILE_NAME
from sptmod.core.models import Json
from sptmod.core.repo import Repo

# A stub server/client "binary": on start it dumps its own argv, cwd and a
# snapshot of its environment to "<its own script path>.dump.json", then
# sleeps. It never opens a socket and never talks to the network -- readiness
# is always simulated through respx on the ping endpoint. Deriving the dump
# path from ``sys.argv[0]`` (rather than an inherited env var) keeps the dump
# unambiguous even though the client stub's environment is deliberately
# reshaped by launch.py before it is spawned.
_STUB_SCRIPT = f"""#!{sys.executable}
import json
import os
import sys
import time

dump_path = sys.argv[0] + ".dump.json"
with open(dump_path, "w", encoding="utf-8") as handle:
    json.dump(
        {{
            "pid": os.getpid(),
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "env": dict(os.environ),
        }},
        handle,
    )
time.sleep(30)
"""


def _write_stub_binary(path: Path) -> None:
    """Write an executable stub process to ``path`` (parent must exist)."""
    path.write_text(_STUB_SCRIPT, encoding="utf-8")
    os.chmod(path, 0o755)


def stub_dump(binary_path: Path) -> JsonDict:
    """Read back the argv/cwd/env a stub process at ``binary_path`` recorded.

    Raises ``FileNotFoundError`` until the stub has actually started and
    wound; callers poll on it after ``launch_profile`` returns.
    """
    dump: JsonDict = json.loads(Path(f"{binary_path}.dump.json").read_text(encoding="utf-8"))
    return dump


@dataclass(frozen=True)
class LaunchableGameDir:
    """A :func:`fake_launchable_dir` and everything a test needs to drive it."""

    root: Path
    profile_id: str
    username: str
    nickname: str
    base_url: str
    server_binary: Path
    umu_path: Path
    client_exe: Path
    http_config_file: Path
    launcher_settings_file: Path


def kill_launchable_processes(root: Path) -> None:
    """Kill every stub/watcher process a launch test may have started.

    Every process ``launch_profile`` starts (server, client, watcher) is a
    direct child of this test process at the moment it is spawned -- detached
    only means it gets its own session, not a different parent -- so walking
    ``children(recursive=True)`` of the current pid finds them reliably even
    when a test asserts a failure before any dump file or state file was
    written (those are only a secondary source, kept as a fallback for a
    process that was somehow reparented). Safe to call when nothing was ever
    started.
    """
    pids: set[int] = set()
    with suppress(psutil.Error):
        for child in psutil.Process(os.getpid()).children(recursive=True):
            with suppress(psutil.Error):
                if str(child.cwd()).startswith(str(root)):
                    pids.add(child.pid)
    for dump in root.rglob("*.dump.json"):
        with suppress(OSError, ValueError, KeyError):
            pids.add(json.loads(dump.read_text(encoding="utf-8"))["pid"])
    state_file = root / ".sptmod-launch.json"
    if state_file.is_file():
        with suppress(OSError, ValueError):
            state: Json = json.loads(state_file.read_text(encoding="utf-8"))
            for key in ("server", "client", "watcher"):
                entry = state.get(key) if isinstance(state, dict) else None
                pid = entry.get("pid") if isinstance(entry, dict) else None
                if isinstance(pid, int):
                    pids.add(pid)
    processes: list[psutil.Process] = []
    for pid in pids:
        with suppress(psutil.Error):
            process = psutil.Process(pid)
            for child in process.children(recursive=True):
                with suppress(psutil.Error):
                    child.kill()
            process.kill()
            processes.append(process)
    if processes:
        psutil.wait_procs(processes, timeout=2)


@pytest.fixture
def fake_launchable_dir(fake_game_dir_runtime: Path) -> Iterator[LaunchableGameDir]:
    """A :func:`fake_game_dir_runtime` ready for :mod:`sptmod.core.launch`.

    Adds ``sptLogger.json``, ``http.json`` (default ``127.0.0.1:6969``),
    ``LauncherSettings.json`` (verbatim live shape, ``GameMode`` off), a
    profile with ``info.username``/``characters.pmc.Info.Nickname``, an
    executable stub server at the real ``server_binary`` path and an
    executable stub "umu" at the ``UmuPath`` named in the settings. Nothing
    binds a port; nothing talks to the network.
    """
    root = fake_game_dir_runtime
    runtime = root / "SPT_Runtime"

    (runtime / "sptLogger.json").write_text("{}", encoding="utf-8")

    (runtime / "SPT_Data" / "configs" / "http.json").write_text(
        json.dumps(
            {
                "ip": "127.0.0.1",
                "port": 6969,
                "backendIp": "127.0.0.1",
                "backendPort": 6969,
                "logRequests": True,
                "serverImagePathOverride": {},
                "webAuthenticationConfig": {
                    "enabled": True,
                    "enableDefaultUser": True,
                    "allowDefaultUserFromAnyIp": True,
                    "requireCredentialsOnLocalhost": False,
                    "defaultUser": {
                        "username": "spt",
                        "password": "spt",
                        "isAdministrator": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    launcher_dir = runtime / "user" / "Launcher"
    launcher_dir.mkdir(parents=True)
    umu_path = root / "stub-umu.py"
    _write_stub_binary(umu_path)
    (launcher_dir / "LauncherSettings.json").write_text(
        json.dumps(
            {
                "Servers": [],
                "StartLocation": {"X": 0, "Y": 0},
                "StartSize": {"Height": 1381, "Width": 2522},
                "FirstRun": False,
                "EnableGamePath": False,
                "GamePath": str(root),
                "CloseToTray": False,
                "MinimizeOnLaunch": True,
                "AlwaysTop": False,
                "UseBackground": True,
                "ClearCacheOnLaunch": False,
                "AutoConnectLastProfile": False,
                "PreferredProfile": None,
                "ExcludeFromCleanup": [],
                "Language": "en",
                "LinuxSettings": {
                    "PrefixPath": str(root / "prefix"),
                    "UmuPath": str(umu_path),
                    "LaunchSettings": "PROTON_USE_XALIA=0 PROTON_MEDIA_USE_GST=1",
                    "ProtonVersion": "GE-Proton11-5",
                    "ProtonPaths": [],
                    "GameMode": False,
                    "DefaultEnv": 'WINEDLLOVERRIDES="winhttp=n,b"',
                },
                "DebugLogging": False,
            }
        ),
        encoding="utf-8",
    )

    profile_id = "6a986df8b3c3f533ed2cd26a"
    username = "0Sullivan"
    nickname = "TesterNick"
    (runtime / "user" / "profiles" / f"{profile_id}.json").write_text(
        json.dumps(
            {
                "info": {"id": profile_id, "username": username},
                "characters": {"pmc": {"Info": {"Nickname": nickname}}},
            }
        ),
        encoding="utf-8",
    )

    layout = detect_layout(root)
    server_binary = root / layout.server_binary
    server_binary.parent.mkdir(parents=True, exist_ok=True)
    _write_stub_binary(server_binary)

    client_exe = root / layout.client_exe
    client_exe.write_text("stub client executable placeholder", encoding="utf-8")

    try:
        yield LaunchableGameDir(
            root=root,
            profile_id=profile_id,
            username=username,
            nickname=nickname,
            base_url="https://127.0.0.1:6969",
            server_binary=server_binary,
            umu_path=umu_path,
            client_exe=client_exe,
            http_config_file=root / layout.http_config_file,
            launcher_settings_file=root / layout.launcher_settings_file,
        )
    finally:
        kill_launchable_processes(root)


@pytest.fixture
def launchable_repo(fake_launchable_dir: LaunchableGameDir) -> tuple[LaunchableGameDir, Repo]:
    """A :func:`fake_launchable_dir` initialized as a clean git repository.

    Mirrors how :func:`clean_repo` wraps ``fake_game_dir``: ``Repo.init`` writes the
    whitelist, then one commit stages everything the whitelist allows, leaving a clean
    tree. Mod set launch tests use ``install_commit`` on the returned ``Repo`` to install
    mods before calling ``services.launch_profile``.
    """
    root = fake_launchable_dir.root
    repo = Repo.init(detect_layout(root))
    repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    return fake_launchable_dir, repo


@pytest.fixture
def mod_set_store(tmp_path: Path) -> Path:
    """A mod set store file path under ``tmp_path``, never the real config directory.

    Passed as ``store_path`` to every mod set service/RPC call in the mod set test
    suites, keeping them off the developer's real ``modsets.json``.
    """
    return tmp_path / "modsets.json"


@pytest.fixture
def fika_store(tmp_path: Path) -> Path:
    """A Fika settings file path under ``tmp_path``, never the real config directory.

    Passed as ``fika_path`` to every Fika service/RPC call in the Fika test suites,
    keeping them off the developer's real ``fika.json``.
    """
    return tmp_path / "fika.json"


def _open_process(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start ``argv``; the ``spawn_process`` fixture owns and kills the handle."""
    return subprocess.Popen(list(argv))


@pytest.fixture
def spawn_process() -> Iterator[SpawnProcess]:
    """Factory starting throwaway real processes, all killed on teardown."""
    spawned: list[subprocess.Popen[bytes]] = []

    def _spawn(argv: Sequence[str]) -> subprocess.Popen[bytes]:
        spawned.append(_open_process(argv))
        return spawned[-1]

    yield _spawn
    for process in spawned:
        with suppress(OSError):
            process.kill()
            process.wait(timeout=2)


@pytest.fixture
def spawn_sleeper(spawn_process: SpawnProcess) -> SpawnSleeper:
    """Factory spawning a throwaway real process that just sleeps."""
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None

    def _spawn() -> subprocess.Popen[bytes]:
        return spawn_process([sleep_bin, "30"])

    return _spawn


def write_launch_state(
    root: Path,
    *,
    profile_id: str = "",
    stop_server_on_exit: bool = True,
    server: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
    watcher: dict[str, Any] | None = None,
    mode: str = "",
) -> None:
    """Write LAUNCH_STATE_FILE_NAME to the game directory with given process state.

    Creates the JSON state file recording the profile, process PIDs and start times
    (server, client, watcher), the stop_server_on_exit flag and the launch mode
    (empty for a launch on the local server, "client" for one on a Fika host).
    Keyword args allow each process reference and the flag to be individually set;
    defaults are empty profile and true for stop_server_on_exit.
    """
    payload = {
        "profile_id": profile_id,
        "stop_server_on_exit": stop_server_on_exit,
        "server": server,
        "client": client,
        "watcher": watcher,
        "mode": mode,
    }
    (root / LAUNCH_STATE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def process_ref(pid: int, started_at: float, *, started_by_sptmod: bool | None = None) -> dict[str, Any]:
    """Build a process reference dict for the launch state file.

    Returns a dict with pid and started_at (float timestamp); if started_by_sptmod
    is not None, it is also included. This object is assigned to server, client, or
    watcher fields in the state file.
    """
    payload: dict[str, Any] = {"pid": pid, "started_at": started_at}
    if started_by_sptmod is not None:
        payload["started_by_sptmod"] = started_by_sptmod
    return payload
