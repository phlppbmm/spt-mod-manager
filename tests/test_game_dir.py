"""Tests for game directory detection, SPT version lookup and process check.

CONTRACT CHANGE (spt-mod-manager-fyx): ``validate_game_dir`` now returns the
detected ``GameLayout`` instead of ``None`` (still raising ``NotAGameDirError``
for the same bad-input cases, so the tests below are otherwise unchanged), and
the free ``spt_version(path)`` function was removed in favor of the layout- and
git-repo-aware version chain (``read_core_json_version``, ``read_pe_file_version``,
``resolve_spt_version``). Its former core.json-reading coverage moved to
``tests/test_game_layout.py::test_read_core_json_version_*``.
"""

import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path

import psutil
import pytest

from fixture_types import SpawnProcess
from sptmod.core.errors import GameRunningError, NotAGameDirError
from sptmod.core.game_dir import (
    PROCESS_SCAN_ENV,
    GameProcess,
    GameProcessKind,
    ProcessSighting,
    ensure_game_not_running,
    game_process_kind,
    game_processes,
    is_game_dir,
    is_game_process_name,
    process_alive,
    running_game_processes,
    spt_process_sightings,
    validate_game_dir,
)


@pytest.fixture(autouse=True)
def real_process_table(real_process_scan: None) -> None:
    """Undo the suite-wide process-scan switch for the scanner's own tests.

    This module is the unit test of the scan itself, so every test here has to
    reach the real implementation the rest of the suite is protected from.
    Everything below only reads the process table -- no test in this module
    starts, stops or signals a process it did not spawn itself, which
    ``tests/test_process_scan_isolation.py`` pins -- so reaching the real table
    is safe here. The tests of the off switch set the variable again themselves.
    """
    del real_process_scan


def _refuse_to_scan() -> Iterator[psutil.Process]:
    """Stand in for ``psutil.process_iter``; fails when the scan asks at all."""
    raise AssertionError("the process table was scanned while the scan was switched off")


def _make_game_dir(root: Path) -> Path:
    """Create a directory structure with SPT and BepInEx folders."""
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)
    return root


# --- is_game_dir / validate_game_dir ---------------------------------------


def test_is_game_dir_true_for_valid_layout(tmp_path: Path) -> None:
    """is_game_dir() returns True for a directory with SPT and BepInEx.

    Creates the required subdirectories; must return True.
    """
    game_dir = _make_game_dir(tmp_path / "game")

    assert is_game_dir(game_dir) is True


@pytest.mark.parametrize("present_dir", ["BepInEx", "SPT"])
def test_is_game_dir_false_when_a_required_dir_is_missing(tmp_path: Path, present_dir: str) -> None:
    """Missing required directories (SPT or BepInEx) are rejected by is_game_dir()."""
    game_dir = tmp_path / "game"
    (game_dir / present_dir).mkdir(parents=True)

    assert is_game_dir(game_dir) is False


def test_is_game_dir_false_when_path_is_a_file(tmp_path: Path) -> None:
    """is_game_dir() returns False when the path is a file, not a directory.

    Creates a file at the path; must return False.
    """
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")

    assert is_game_dir(file_path) is False


def test_is_game_dir_false_when_path_does_not_exist(tmp_path: Path) -> None:
    """is_game_dir() returns False for non-existent paths.

    Passes a path that doesn't exist; must return False (not raise).
    """
    assert is_game_dir(tmp_path / "does-not-exist") is False


def test_validate_game_dir_accepts_valid_layout(tmp_path: Path) -> None:
    """validate_game_dir() does not raise for a valid directory layout.

    Creates SPT and BepInEx; must return without raising.
    """
    game_dir = _make_game_dir(tmp_path / "game")

    validate_game_dir(game_dir)  # must not raise


@pytest.mark.parametrize("present_dir", ["BepInEx", "SPT"])
def test_validate_game_dir_raises_when_a_required_dir_is_missing(tmp_path: Path, present_dir: str) -> None:
    """NotAGameDirError is raised when required directories are missing."""
    game_dir = tmp_path / "game"
    (game_dir / present_dir).mkdir(parents=True)

    with pytest.raises(NotAGameDirError):
        validate_game_dir(game_dir)


def test_validate_game_dir_raises_when_path_is_a_file(tmp_path: Path) -> None:
    """NotAGameDirError is raised when the path is a file, not a directory."""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(NotAGameDirError):
        validate_game_dir(file_path)


def test_validate_game_dir_raises_when_path_does_not_exist(tmp_path: Path) -> None:
    """NotAGameDirError is raised when the path does not exist."""
    with pytest.raises(NotAGameDirError):
        validate_game_dir(tmp_path / "does-not-exist")


# --- is_game_process_name ---------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "SPT.Server.exe",
        "EscapeFromTarkov.exe",
        "spt.server.exe",
        "escapefromtarkov.exe",
        "SPT.Server",
        "EscapeFromTarkov",
        "/some/wine/path/EscapeFromTarkov.exe",
    ],
)
def test_is_game_process_name_matches_game_processes(name: str) -> None:
    """Game process names (server/client) are recognized."""
    assert is_game_process_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "explorer.exe",
        "steam.exe",
        "python.exe",
        "",
        "notepad.exe",
    ],
)
def test_is_game_process_name_rejects_unrelated_processes(name: str) -> None:
    """is_game_process_name() returns False for non-game process names.

    Parametrized over explorer, steam, python, empty string, and notepad;
    each must return False.
    """
    assert is_game_process_name(name) is False


# --- ensure_game_not_running -------------------------------------------------


def test_ensure_game_not_running_passes_when_no_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_game_not_running() does not raise when no processes detected.

    Patches running_game_processes() to return an empty list; must return
    without raising.
    """
    def no_running_processes() -> list[str]:
        return []

    monkeypatch.setattr("sptmod.core.game_dir.running_game_processes", no_running_processes)

    ensure_game_not_running()  # must not raise


def test_ensure_game_not_running_raises_when_processes_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GameRunningError is raised when game processes are detected."""
    monkeypatch.setattr(
        "sptmod.core.game_dir.running_game_processes",
        lambda: ["SPT.Server.exe"],
    )

    with pytest.raises(GameRunningError):
        ensure_game_not_running()


def test_running_game_processes_scans_the_real_process_table_without_crashing() -> None:
    """Exercises the real, non-monkeypatched implementation: this sandboxed
    test environment never runs SPT.Server or EscapeFromTarkov, so none of
    the real processes on this machine are ever reported."""
    names = running_game_processes()

    assert isinstance(names, list)
    assert all(is_game_process_name(name) for name in names)


class _VanishedProcess:
    """A stand-in for a real ``psutil.Process`` that died mid-scan."""

    pid = 999999

    def name(self) -> str:
        """Simulate process death by raising NoSuchProcess."""
        raise psutil.NoSuchProcess(pid=999999)

    def cmdline(self) -> list[str]:
        """Simulate process death by raising NoSuchProcess."""
        raise psutil.NoSuchProcess(pid=999999)


class _SecretiveProcess:
    """A process whose name reads but whose command line stays unreadable.

    The operating system refuses the command line of processes owned by other
    users, which is a genuine ``psutil.Error`` the scan has to live with.
    """

    pid = 999998

    def __init__(self, name: str) -> None:
        """Remember the name this process reports."""
        self._name = name

    def name(self) -> str:
        """Return the configured process name."""
        return self._name

    def cmdline(self) -> list[str]:
        """Refuse the command line the way the operating system does."""
        raise psutil.AccessDenied(pid=999998)


def test_running_game_processes_skips_a_process_that_vanishes_mid_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``psutil.process_iter()`` is the real OS process-table boundary this
    module reads (mirroring how the test suite mocks the HTTP/subprocess
    boundaries elsewhere); a process that exits between being listed and
    ``.name()`` being called on it raises a genuine ``psutil.Error``, which
    ``running_game_processes`` must swallow rather than propagate."""
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter([_VanishedProcess()]),
    )

    assert running_game_processes() == []


def test_ensure_game_not_running_message_names_the_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GameRunningError message lists all detected running game processes."""
    monkeypatch.setattr(
        "sptmod.core.game_dir.running_game_processes",
        lambda: ["SPT.Server.exe", "EscapeFromTarkov.exe"],
    )

    with pytest.raises(GameRunningError) as exc_info:
        ensure_game_not_running()

    assert "SPT.Server.exe" in str(exc_info.value)
    assert "EscapeFromTarkov.exe" in str(exc_info.value)


def test_running_game_processes_reports_a_real_process_named_like_the_server(
    tmp_path: Path,
    spawn_process: SpawnProcess,
) -> None:
    """A process whose executable is named like the SPT server is detected
    through the real process table and blocks operations."""
    sleep = shutil.which("sleep")
    assert sleep is not None
    fake_server = tmp_path / "SPT.Server.Linux"
    shutil.copy(sleep, fake_server)
    spawn_process([str(fake_server), "30"])

    assert "SPT.Server.Linux" in running_game_processes()
    with pytest.raises(GameRunningError):
        ensure_game_not_running()


# --- game_process_kind -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["SPT.Server.exe", "spt.server.exe", "SPT.Server.Linux", "SPT.Server"],
)
def test_game_process_kind_matches_the_server(name: str) -> None:
    """game_process_kind() returns "server" for SPT.Server processes.

    Parametrized over case variations and platform names; each must return
    "server".
    """
    assert game_process_kind(name) == "server"


@pytest.mark.parametrize(
    "name",
    [
        "EscapeFromTarkov.exe",
        "escapefromtarkov.exe",
        "/some/wine/path/EscapeFromTarkov.exe",
    ],
)
def test_game_process_kind_matches_the_client(name: str) -> None:
    """game_process_kind() returns "client" for EscapeFromTarkov processes.

    Parametrized over case variations and paths with wine; each must return
    "client".
    """
    assert game_process_kind(name) == "client"


@pytest.mark.parametrize("name", ["explorer.exe", "steam.exe", ""])
def test_game_process_kind_returns_none_for_unrelated_processes(name: str) -> None:
    """game_process_kind() returns None for non-game process names.

    Parametrized over explorer, steam, and empty string; each must return None.
    """
    assert game_process_kind(name) is None


# --- game_processes -----------------------------------------------------------


class _FakeProcess:
    """A mock psutil.Process for testing process discovery and filtering."""

    def __init__(self, pid: int, name: str, created: float, cmdline: Sequence[str] = ()) -> None:
        """Initialize with pid, process name, creation timestamp and command line."""
        self.pid = pid
        self._name = name
        self._created = created
        self._cmdline = list(cmdline)

    def name(self) -> str:
        """Return the configured process name."""
        return self._name

    def cmdline(self) -> list[str]:
        """Return the configured command line."""
        return list(self._cmdline)

    def create_time(self) -> float:
        """Return the configured creation timestamp."""
        return self._created


def test_game_processes_reports_server_and_client_with_pid_and_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """game_processes() returns GameProcess instances with pid, name, kind, and started_at."""
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter(
            [
                _FakeProcess(111, "SPT.Server.Linux", 1000.0),
                _FakeProcess(222, "EscapeFromTarkov.exe", 2000.5),
                _FakeProcess(333, "explorer.exe", 3000.0),
            ]
        ),
    )

    assert game_processes() == [
        GameProcess(pid=111, name="SPT.Server.Linux", kind="server", started_at=1000.0),
        GameProcess(pid=222, name="EscapeFromTarkov.exe", kind="client", started_at=2000.5),
    ]


def test_game_processes_skips_a_process_that_vanishes_mid_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Processes that die mid-scan (before name() is called) are silently skipped."""
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter([_VanishedProcess()]),
    )

    assert not game_processes()


def test_running_game_processes_is_the_name_projection_of_game_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """running_game_processes() extracts names from the GameProcess sequence."""
    monkeypatch.setattr(
        "sptmod.core.game_dir.game_processes",
        lambda: [
            GameProcess(pid=1, name="SPT.Server.Linux", kind="server", started_at=1.0),
            GameProcess(pid=2, name="EscapeFromTarkov.exe", kind="client", started_at=2.0),
        ],
    )

    assert running_game_processes() == ["SPT.Server.Linux", "EscapeFromTarkov.exe"]


@pytest.mark.parametrize(
    "name,cmdline,kind",
    [
        pytest.param(
            "EscapeFromTarko",
            ["X:\\Games\\SPT\\EscapeFromTarkov.exe", "-force-gfx-jobs", "native", "-token=abc"],
            "client",
            id="proton-client-truncated-name-and-backslash-command-line",
        ),
        pytest.param(
            "wine64-preloader",
            ["Z:\\Games\\SPT\\EscapeFromTarkov.exe"],
            "client",
            id="client-recognisable-only-by-its-command-line",
        ),
        pytest.param(
            "python3",
            ["/home/player/SPT/EscapeFromTarkov"],
            "client",
            id="client-command-line-without-the-exe-suffix",
        ),
        pytest.param(
            "SPT.Server.Linux",
            ["/home/player/SPT/SPT_Runtime/SPT.Server.Linux"],
            "server",
            id="linux-server-by-name",
        ),
        pytest.param(
            "node",
            ["C:\\SPT\\SPT_Runtime\\SPT.Server.exe"],
            "server",
            id="windows-server-by-command-line",
        ),
        pytest.param(
            "umu-run",
            ["/usr/bin/umu-run", "EscapeFromTarkov.exe", "-token=abc"],
            None,
            id="wrapper-naming-the-game-only-in-a-later-argument",
        ),
        pytest.param(
            "EscapeFromTark",
            ["/opt/EscapeFromTark"],
            None,
            id="fourteen-character-prefix-of-the-game-name",
        ),
        pytest.param(
            "EscapeFrom",
            ["/opt/EscapeFrom"],
            None,
            id="short-prefix-of-the-game-name",
        ),
        pytest.param("bash", [], None, id="process-without-a-command-line"),
    ],
)
def test_game_processes_classifies_by_name_and_by_command_line(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    cmdline: list[str],
    kind: GameProcessKind | None,
) -> None:
    """game_processes() classifies a process by its name or by its command line.

    Each case feeds one fake process into the process table. A Proton-hosted
    client reports the 15-character comm ``EscapeFromTarko`` and a Windows
    command line, which no name substring and no POSIX basename match; the
    command line therefore has to be split on both separators. Names that only
    share a shorter prefix, a wrapper that merely mentions the game in a later
    argument and a process without a command line must stay unclassified, so
    the scan does not report strangers as the game.
    """
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter([_FakeProcess(4242, name, 7.5, cmdline)]),
    )

    expected = [] if kind is None else [GameProcess(pid=4242, name=name, kind=kind, started_at=7.5)]
    assert game_processes() == expected


def test_game_processes_skips_a_process_whose_command_line_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """game_processes() skips a process whose command line the system withholds.

    The fake process reports a name that classifies as neither half and then
    refuses its command line with psutil.AccessDenied, which is what a process
    of another user does. The scan must swallow that and report nothing rather
    than propagate the error.
    """
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter([_SecretiveProcess("wineserver")]),
    )

    assert not game_processes()


# --- spt_process_sightings -----------------------------------------------------


def test_spt_process_sightings_reports_processes_mentioning_spt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spt_process_sightings() reports name and program of every SPT mention.

    The fake table holds a wrapper that names the game only in a later
    argument, a server, a truncated client without any command line and an
    unrelated process. The first three are reported with their first command
    line token (empty when there is none) because a failed launch has to show
    what the process table really held; the unrelated one is left out.
    """
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter(
            [
                _FakeProcess(1, "umu-run", 1.0, ["/usr/bin/umu-run", "EscapeFromTarkov.exe"]),
                _FakeProcess(2, "SPT.Server.Linux", 2.0, ["./SPT.Server.Linux"]),
                _FakeProcess(3, "EscapeFromTarko", 3.0, []),
                _FakeProcess(4, "firefox", 4.0, ["/usr/bin/firefox"]),
            ]
        ),
    )

    assert spt_process_sightings() == [
        ProcessSighting(pid=1, name="umu-run", command="/usr/bin/umu-run"),
        ProcessSighting(pid=2, name="SPT.Server.Linux", command="./SPT.Server.Linux"),
        ProcessSighting(pid=3, name="EscapeFromTarko", command=""),
    ]


def test_spt_process_sightings_skips_processes_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spt_process_sightings() skips processes that vanish or withhold their data.

    One fake process dies before its name is read, the other refuses its
    command line. Both raise a genuine psutil.Error, which the diagnostic scan
    must swallow: a launch log is never worth an exception.
    """
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter([_VanishedProcess(), _SecretiveProcess("EscapeFromTarko")]),
    )

    assert not spt_process_sightings()


def test_spt_process_sightings_scans_the_real_process_table_without_crashing() -> None:
    """spt_process_sightings() runs against the real, unpatched process table.

    Exercises the real psutil scan the way the launch log calls it, on a
    machine whose process table the test does not control. The scan must walk
    it end to end without raising and hand back readable entries, whatever it
    finds.
    """
    sightings = spt_process_sightings()

    assert isinstance(sightings, list)
    for sighting in sightings:
        assert sighting.pid > 0
        assert isinstance(sighting.name, str)
        assert isinstance(sighting.command, str)


# --- SPTMOD_PROCESS_SCAN -------------------------------------------------------


def test_game_processes_reports_nothing_while_the_scan_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """game_processes() answers nothing before it looks at the process table.

    A process table holding the server and the game is prepared, and
    SPTMOD_PROCESS_SCAN is set to off. The scan must answer with an empty list
    without asking psutil at all: this is the seam every process a launch
    starts inherits, so a machine that plays SPT can still run the test suite.
    """
    monkeypatch.setenv(PROCESS_SCAN_ENV, "off")
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        _refuse_to_scan,
    )

    assert not game_processes()


def test_spt_process_sightings_reports_nothing_while_the_scan_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spt_process_sightings() answers nothing before it looks at the table.

    The diagnostic scan of the launch log carries the same off switch as
    game_processes(), asserted the same way: psutil must not be asked at all.
    """
    monkeypatch.setenv(PROCESS_SCAN_ENV, "off")
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        _refuse_to_scan,
    )

    assert not spt_process_sightings()


@pytest.mark.parametrize("value", ["", "on", "OFF", "0"], ids=["empty", "on", "uppercase", "zero"])
def test_the_scan_runs_for_every_value_that_is_not_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Only the exact value "off" switches the scan off; anything else scans.

    The same fake table is offered under four other values of the variable,
    including the uppercase spelling. Each must be scanned and report the
    server, so a typo in the variable silently disabling the running-game check
    would fail here.
    """
    monkeypatch.setenv(PROCESS_SCAN_ENV, value)
    monkeypatch.setattr(
        "sptmod.core.game_dir.psutil.process_iter",
        lambda: iter([_FakeProcess(4711, "SPT.Server.Linux", 5.0)]),
    )

    assert game_processes() == [GameProcess(pid=4711, name="SPT.Server.Linux", kind="server", started_at=5.0)]


# --- process_alive -------------------------------------------------------------


def test_process_alive_true_for_a_running_process_with_the_matching_start_time(
    spawn_process: SpawnProcess,
) -> None:
    """process_alive() returns True when the process exists and creation time matches."""
    sleep = shutil.which("sleep")
    assert sleep is not None
    process = spawn_process([sleep, "30"])

    started_at = psutil.Process(process.pid).create_time()
    assert process_alive(process.pid, started_at) is True


def test_process_alive_false_after_the_process_exits(spawn_process: SpawnProcess) -> None:
    """process_alive() returns False after the process has exited."""
    sleep = shutil.which("sleep")
    assert sleep is not None
    process = spawn_process([sleep, "30"])
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait()

    assert process_alive(process.pid, started_at) is False


def test_process_alive_false_when_the_start_time_no_longer_matches(
    spawn_process: SpawnProcess,
) -> None:
    """process_alive() returns False when the process exists but creation time differs."""
    sleep = shutil.which("sleep")
    assert sleep is not None
    process = spawn_process([sleep, "30"])

    assert process_alive(process.pid, 1.0) is False


class _ZombieProcess:
    """A mock psutil.Process that reports zombie status to test cleanup logic."""

    def __init__(self, created: float) -> None:
        """Initialize with creation timestamp."""
        self._created = created

    def is_running(self) -> bool:
        """Return True (zombie still in process table)."""
        return True

    def status(self) -> str:
        """Return the zombie status code."""
        return psutil.STATUS_ZOMBIE

    def create_time(self) -> float:
        """Return the configured creation timestamp."""
        return self._created


def test_process_alive_false_for_a_zombie_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_alive() returns False for a zombie process.

    Patches psutil.Process to return a _ZombieProcess with matching creation
    time; must return False (zombie is not alive).
    """
    def zombie_process(_pid: int) -> _ZombieProcess:
        return _ZombieProcess(5.0)

    monkeypatch.setattr("sptmod.core.game_dir.psutil.Process", zombie_process)

    assert process_alive(4242, 5.0) is False
