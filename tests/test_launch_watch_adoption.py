"""Tests for the game the watcher notes as belonging to one launch.

On Linux the process a launch starts only hosts the game: the game shows up
below it seconds later and often outlives it. The watcher therefore notes that
game in the launch state file under its own number, and what is noted there is
closed later -- so it may only ever note a game that came out of this launch.
These tests drive ``watch_game`` against a genuine process table made of real
throwaway processes: a copy of ``sleep`` carrying the game's file name, started
either below the process the launch recorded or beside it. No test starts a
real SPT server or game, and every process a test starts it also ends.
"""

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
import pytest

from fixture_types import SpawnProcess, SpawnSleeper
from fixtures_launch import process_ref, write_launch_state
from launch_helpers import ticking, wait_until
from sptmod.core import game_dir
from sptmod.core.launch import LAUNCH_STATE_FILE_NAME, StopResult, stop_game, watch_game

pytestmark = pytest.mark.timeout(5)

NOTHING_LEFT_BUT_A_GAME = (
    "Nothing sptmod started here is still running. The game is still running; close it yourself."
)


def _state_file(root: Path) -> Path:
    """Return the launch state file of the game directory at ``root``."""
    return root / LAUNCH_STATE_FILE_NAME


def _live_ref(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    """Note a running process the way a launch does: number and real start time."""
    return process_ref(process.pid, psutil.Process(process.pid).create_time())


def _dead_ref(spawn: SpawnSleeper) -> dict[str, Any]:
    """Note a process, then end it: the process a launch started and lost."""
    process = spawn()
    started_at = psutil.Process(process.pid).create_time()
    process.kill()
    process.wait(timeout=4)
    return process_ref(process.pid, started_at)


def _watch_and_note(root: Path, *, polls: int, finish: Callable[[], None]) -> list[Any]:
    """Watch the game of ``root`` for ``polls`` polls and return what was noted each time.

    Reads the ``adopted_client`` entry of the state file on every poll and ends
    the processes of the test on the last one through ``finish``, because the
    watcher deletes that file as soon as the game is gone: while it runs is the
    only moment the note can be read at all. The fake clock makes every poll
    immediate, so ``polls`` is a count and never a wait.
    """
    seen: list[Any] = []

    def _on_tick(tick: int) -> None:
        if tick <= polls:
            payload: dict[str, Any] = json.loads(_state_file(root).read_text(encoding="utf-8"))
            seen.append(payload["adopted_client"])
        if tick == polls:
            finish()

    sleep, clock = ticking(_on_tick)
    watch_game(root, poll_seconds=3.0, grace_seconds=30.0, sleep=sleep, clock=clock)
    return seen


def _end(*processes: subprocess.Popen[bytes]) -> Callable[[], None]:
    """Return a callback that ends and reaps ``processes``.

    Reaping matters: a killed process that nobody waits for stays in the
    process table as a zombie under its old name, which the watcher would go on
    reporting as a running game forever.
    """

    def _finish() -> None:
        for process in processes:
            process.kill()
            process.wait(timeout=4)

    return _finish


def _host_the_game(
    spawn_process: SpawnProcess,
    client_executable: Path,
    pid_file: Path,
    *,
    games: int = 1,
) -> tuple[subprocess.Popen[bytes], list[int]]:
    """Start a process that starts ``games`` copies of the game below itself.

    The shape a launch really has on Linux, where the process it starts only
    hosts the game. The hosted processes report their numbers through
    ``pid_file`` and are started a hundredth of a second apart, the resolution
    of the start times they are told apart by. The host waits for them
    afterwards, so ending the games ends the host too and reaps them with it.
    """
    script = (
        "import subprocess, time\n"
        "games = []\n"
        f"for _ in range({games}):\n"
        f"    games.append(subprocess.Popen([{str(client_executable)!r}, '30']))\n"
        "    time.sleep(0.05)\n"
        f"open({str(pid_file)!r}, 'w').write(' '.join(str(game.pid) for game in games))\n"
        "for game in games: game.wait()\n"
    )
    host = spawn_process([sys.executable, "-c", script])
    wait_until(lambda: pid_file.exists() and len(pid_file.read_text(encoding="utf-8").split()) == games)
    return host, [int(part) for part in pid_file.read_text(encoding="utf-8").split()]


def _end_the_host(host: subprocess.Popen[bytes], game_pids: list[int]) -> Callable[[], None]:
    """Return a callback that ends the hosted games and, with them, their host.

    The games are killed first and the host, which waits for them, ends by
    itself and reaps them on the way out -- no orphan keeps running after the
    test and no zombie keeps showing up as a game.
    """

    def _finish() -> None:
        for pid in game_pids:
            psutil.Process(pid).kill()
        host.wait(timeout=4)

    return _finish


class _StaleChild(psutil.Process):
    """A live process reporting the start time of whoever had its number before.

    A recycled process number cannot be brought about on demand, so only the
    tree below the recorded process is faked, for this one process; the process
    itself and the start time the real scan reads for it stay genuine, so the
    assertion still says what the watcher decided.
    """

    def create_time(self) -> float:
        """Report a start time no living process has."""
        return 1.0


def _stale_number(game_pid: int) -> list[psutil.Process]:
    """Report the game's number below the launch, with a foreign start time."""
    return [_StaleChild(game_pid)]


def _tree_gone(game_pid: int) -> list[psutil.Process]:
    """Fail the way a process tree that vanished between two looks does."""
    raise psutil.NoSuchProcess(game_pid)


def _no_start_time(_started_at: float) -> float:
    """The start time a launch notes when it could not read one at spawn time."""
    return 0.0


def _another_process(started_at: float) -> float:
    """A start time that names some other process on the same number."""
    return started_at + 1.0


@pytest.mark.usefixtures("own_processes_only")
def test_the_game_below_the_process_the_launch_started_is_noted_with_that_process(
    tmp_path: Path,
    spawn_process: SpawnProcess,
    client_executable: Path,
) -> None:
    """The watcher writes the hosted game into the state file under its own number.

    A process the launch recorded hosts a game the scan classifies; the watcher
    polls twice before both end. Both polls must read back the game's real
    number and start time together with the number of the process it hangs off,
    because that note is the only handle a later stop_game has once the host is
    gone -- and reading the same note twice shows the second poll leaves the
    first one standing instead of writing it again.
    """
    host, (game_pid,) = _host_the_game(spawn_process, client_executable, tmp_path / "games.pid")
    write_launch_state(tmp_path, profile_id="p", client=_live_ref(host))
    noted = process_ref(game_pid, psutil.Process(game_pid).create_time(), descendant_of=host.pid)

    seen = _watch_and_note(tmp_path, polls=2, finish=_end_the_host(host, [game_pid]))

    assert seen == [noted, noted]
    assert not _state_file(tmp_path).exists()


@pytest.mark.usefixtures("own_processes_only")
def test_the_youngest_game_below_the_process_the_launch_started_is_the_one_noted(
    tmp_path: Path,
    spawn_process: SpawnProcess,
    client_executable: Path,
) -> None:
    """Of several games below the launch, the one that started last is noted.

    The recorded process hosts two games a hundredth of a second apart, and the
    older of the two is the one the process table reports first. The note must
    name the younger one, because that is the game this launch is playing: the
    older one is the raid the player left and closing it would be closing a
    game somebody else's launch is responsible for.
    """
    host, (older, younger) = _host_the_game(spawn_process, client_executable, tmp_path / "games.pid", games=2)
    scanned = [process.pid for process in game_dir.game_processes() if process.kind == "client"]
    assert scanned.index(older) < scanned.index(younger)
    write_launch_state(tmp_path, profile_id="p", client=_live_ref(host))
    noted = process_ref(younger, psutil.Process(younger).create_time(), descendant_of=host.pid)

    seen = _watch_and_note(tmp_path, polls=1, finish=_end_the_host(host, [older, younger]))

    assert seen == [noted]


@pytest.mark.usefixtures("own_processes_only")
def test_a_game_that_was_already_running_beside_the_launch_is_never_noted(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """A game running next to the launch, from before it, is not this launch's game.

    The game is started first and the process the launch records a hundredth of
    a second later, beside it rather than above it. Every poll must leave the
    state file without a note, because closing a raid the player started before
    this launch is exactly what may never happen.
    """
    game = spawn_client()
    time.sleep(0.05)
    launched = spawn_sleeper()
    write_launch_state(tmp_path, profile_id="p", client=_live_ref(launched))

    seen = _watch_and_note(tmp_path, polls=2, finish=_end(launched, game))

    assert seen == [None, None]
    assert not _state_file(tmp_path).exists()


@pytest.mark.usefixtures("own_processes_only")
def test_a_game_started_beside_the_launch_afterwards_is_never_noted(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """A game that appears after the launch but not below it stays untouched.

    The process the launch started is alive and a game the scan classifies
    starts a hundredth of a second later beside it -- the player opening a
    second game, or another launcher's. It is younger than the launch and looks
    exactly like the game that was expected, and it must still never be noted,
    because the note is what stop_game closes and this game came from somewhere
    else.
    """
    launched = spawn_sleeper()
    write_launch_state(tmp_path, profile_id="p", client=_live_ref(launched))
    time.sleep(0.05)
    game = spawn_client()

    seen = _watch_and_note(tmp_path, polls=2, finish=_end(launched, game))

    assert seen == [None, None]
    assert not _state_file(tmp_path).exists()


@pytest.mark.usefixtures("own_processes_only")
def test_no_game_is_noted_once_the_process_the_launch_started_is_gone(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """With the process the launch started gone, no running game is noted any more.

    The recorded process has ended and a game the scan classifies runs on its
    own. Every poll must leave the state file without a note: nothing is left
    that could tie that game to this launch, and a game that cannot be
    attributed is left to the player rather than claimed.
    """
    write_launch_state(tmp_path, profile_id="p", client=_dead_ref(spawn_sleeper))
    time.sleep(0.05)
    game = spawn_client()

    seen = _watch_and_note(tmp_path, polls=2, finish=_end(game))

    assert seen == [None, None]
    assert not _state_file(tmp_path).exists()


@pytest.mark.usefixtures("own_processes_only")
def test_stop_game_says_a_game_it_never_noted_is_still_running_and_leaves_it(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
) -> None:
    """A game the watcher refused to note survives the stop and is named in the answer.

    The state file records only the process the launch started, which has
    ended, while a game the scan classifies keeps running. stop_game must
    report nothing closed, tell the player in plain words that a game is still
    running, and leave that game alive -- the price of refusing to adopt is
    paid here, and it is deliberately the harmless one.
    """
    game = spawn_client()
    write_launch_state(tmp_path, profile_id="p", client=_dead_ref(spawn_sleeper))

    result = stop_game(tmp_path)

    assert result == StopResult(False, False, NOTHING_LEFT_BUT_A_GAME)
    assert game.poll() is None


@pytest.mark.parametrize(
    "recorded_start",
    [
        pytest.param(_no_start_time, id="no-start-time-was-read-at-spawn"),
        pytest.param(_another_process, id="start-time-of-another-process"),
    ],
)
@pytest.mark.usefixtures("own_processes_only")
def test_a_game_below_a_process_the_note_does_not_identify_is_never_noted(
    tmp_path: Path,
    spawn_process: SpawnProcess,
    client_executable: Path,
    recorded_start: Callable[[float], float],
) -> None:
    """A game is only noted while the recorded process is provably the recorded one.

    The game really is below the process named in the state file, but the start
    time noted with that number does not identify it: it was never read, or it
    belongs to a process that has since been replaced on the same number. The
    state file must stay without a note, because a number alone is no proof
    that this tree is the tree the launch started.
    """
    host, (game_pid,) = _host_the_game(spawn_process, client_executable, tmp_path / "games.pid")
    started_at = psutil.Process(host.pid).create_time()
    write_launch_state(tmp_path, profile_id="p", client=process_ref(host.pid, recorded_start(started_at)))

    seen = _watch_and_note(tmp_path, polls=1, finish=_end_the_host(host, [game_pid]))

    assert seen == [None]


@pytest.mark.parametrize(
    "below_the_launch",
    [
        pytest.param(_stale_number, id="number-of-a-process-that-ended"),
        pytest.param(_tree_gone, id="tree-vanished-between-two-looks"),
    ],
)
@pytest.mark.usefixtures("own_processes_only")
def test_a_game_the_tree_does_not_confirm_under_its_own_start_time_is_never_noted(
    tmp_path: Path,
    spawn_sleeper: SpawnSleeper,
    spawn_client: SpawnSleeper,
    monkeypatch: pytest.MonkeyPatch,
    below_the_launch: Callable[[int], list[psutil.Process]],
) -> None:
    """A game is only noted when the tree names its number and its start time both.

    The tree below the recorded process is replaced: once by that same number
    reported with the start time of a process that ended, which is what a
    recycled number looks like, and once by a tree that cannot be read at all.
    The running game beside the launch must be noted in neither case, because a
    number that matches while the start time does not names a stranger, and a
    tree nobody could read proves nothing.
    """
    launched = spawn_sleeper()
    time.sleep(0.05)
    game = spawn_client()
    write_launch_state(tmp_path, profile_id="p", client=_live_ref(launched))
    real_children = psutil.Process.children

    def _children(self: psutil.Process, recursive: bool = False) -> list[psutil.Process]:
        if self.pid == launched.pid:
            return below_the_launch(game.pid)
        return real_children(self, recursive=recursive)

    with monkeypatch.context() as context:
        context.setattr(psutil.Process, "children", _children)
        seen = _watch_and_note(tmp_path, polls=1, finish=_end(launched, game))

    assert seen == [None]
