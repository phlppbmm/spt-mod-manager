"""Tests for the apply lock and staging area (``sptmod.core.workspace``).

Covers ``staging_dir()`` (cache-dir resolution, on-demand creation, sweeping
of stale entries), the lock lifecycle around ``game_lock()`` (held for the
duration of the block, free again on success and on a raised exception, JSON
content naming the holder), leftover lock files never blocking anyone, the
timeout path with its exact user-facing message, the waiting progress
callback, and the service-layer guarantees the lock exists for: a second
operation waits instead of ever seeing the held lock as an unmanaged file,
read-only calls are never blocked, the lock file never surfaces in
``status()``/``is_clean()``, and a path that is no game directory is refused
in plain language before any lock file is written.
"""

import fcntl
import json
import os
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

from fixture_types import InstallCommit, MakeArchive
from sptmod.core import services
from sptmod.core.errors import NotAGameDirError, OperationInProgressError
from sptmod.core.repo import Repo
from sptmod.core.workspace import (
    LOCK_FILE_NAME,
    download_cache_dir,
    game_lock,
    is_locked,
    staging_dir,
)


def _dead_pid() -> int:
    """Return a process id that is guaranteed not to exist right now."""
    candidate = 999_999
    while psutil.pid_exists(candidate):
        candidate -= 1
    return candidate


def _write_lock(path: Path, *, pid: int, operation: str = "") -> Path:
    """Leave a lock file behind that nobody holds the operating-system lock on."""
    lock = path / LOCK_FILE_NAME
    lock.write_text(
        json.dumps({"pid": pid, "operation": operation, "started": "x"}),
        encoding="utf-8",
    )
    return lock


@contextmanager
def _held(path: Path, raw_content: str) -> Generator[Path]:
    """Really hold the lock on ``path`` with ``raw_content`` in its file.

    Takes the same operating-system lock the production code takes, directly
    on the lock file, so the lock content can be an attacker-chosen raw
    string the public API would never write.
    """
    lock = path / LOCK_FILE_NAME
    handle = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.truncate(handle, 0)
        os.write(handle, raw_content.encode("utf-8"))
        yield lock
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def _holder_content(operation: str, *, pid: int | None = None) -> str:
    return json.dumps({"pid": os.getpid() if pid is None else pid, "operation": operation})


def _no_lock_file(path: Path) -> None:
    """Leave the game directory without any lock file."""
    assert not (path / LOCK_FILE_NAME).exists()


def _unheld_lock_file(path: Path) -> None:
    """Leave a lock file naming this process that nobody holds the OS lock on."""
    _write_lock(path, pid=os.getpid(), operation="crashed run")


def _unopenable_lock_path(path: Path) -> None:
    """Put a directory where the lock file belongs, so it cannot be opened."""
    (path / LOCK_FILE_NAME).mkdir()


# --- staging_dir ---------------------------------------------------------


def test_staging_dir_honors_xdg_cache_home_and_is_created_on_demand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """staging_dir respects XDG_CACHE_HOME and creates directories on demand.

    XDG_CACHE_HOME is set to non-existent tmp_path/cache; staging_dir() creates
    the full path $XDG_CACHE_HOME/sptmod/staging and returns it. Result is_dir()
    confirms it was created.
    """
    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    assert not cache_home.exists()

    result = staging_dir()

    assert result == cache_home / "sptmod" / "staging"
    assert result.is_dir()


def test_staging_dir_sweeps_entries_older_than_a_day_but_keeps_fresh_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """staging_dir removes entries older than 24h; keeps fresh ones on each call.

    Creates: old file, old dir, fresh file (old timestamps are 36h ago). Second
    staging_dir() call sweeps; only fresh.txt remains (verified by iterdir).
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    staging = staging_dir()
    old_file = staging / "old-leftover.txt"
    old_file.write_text("stale", encoding="utf-8")
    old_dir = staging / "old-dir"
    old_dir.mkdir()
    (old_dir / "inner.txt").write_text("stale", encoding="utf-8")
    fresh_file = staging / "fresh.txt"
    fresh_file.write_text("fresh", encoding="utf-8")

    day_and_a_half_ago = time.time() - (36 * 60 * 60)
    os.utime(old_file, (day_and_a_half_ago, day_and_a_half_ago))
    os.utime(old_dir, (day_and_a_half_ago, day_and_a_half_ago))

    result = staging_dir()

    assert sorted(entry.name for entry in result.iterdir()) == ["fresh.txt"]


def test_staging_dir_sweep_skips_an_unstattable_entry_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """staging_dir() sweep skips dangling symlinks without crashing.

    Sweep encounters both a dangling symlink (Path.stat() raises OSError) and
    a stale file. Sweep skips the symlink, removes the stale file, and returns
    successfully.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    staging = staging_dir()
    broken_link = staging / "broken-link"
    broken_link.symlink_to(staging / "does-not-exist-as-a-target")
    old_file = staging / "old-leftover.txt"
    old_file.write_text("stale", encoding="utf-8")
    day_and_a_half_ago = time.time() - (36 * 60 * 60)
    os.utime(old_file, (day_and_a_half_ago, day_and_a_half_ago))

    result = staging_dir()

    assert broken_link.is_symlink()
    assert sorted(entry.name for entry in result.iterdir()) == ["broken-link"]


# --- download_cache_dir -----------------------------------------------


def test_download_cache_dir_honors_xdg_cache_home_and_is_created_on_demand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """download_cache_dir respects XDG_CACHE_HOME and creates directories on demand.

    XDG_CACHE_HOME is non-existent tmp_path/cache; download_cache_dir() creates
    the full path $XDG_CACHE_HOME/sptmod/downloads and returns it. Result.is_dir()
    confirms creation.
    """
    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    assert not cache_home.exists()

    result = download_cache_dir()

    assert result == cache_home / "sptmod" / "downloads"
    assert result.is_dir()


# --- is_locked -------------------------------------------------------------


def test_is_locked_true_while_another_operation_holds_the_lock(
    fake_game_dir: Path,
) -> None:
    """is_locked returns True while another process holds the OS lock.

    Lock file with current pid and "installing SAIN" operation is held via fcntl;
    is_locked() checks for fcntl lock, finds it, returns True.
    """
    with _held(fake_game_dir, _holder_content("installing SAIN")):
        assert is_locked(fake_game_dir) is True


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(_no_lock_file, id="no-lock-file"),
        pytest.param(_unheld_lock_file, id="lock-file-nobody-holds"),
        pytest.param(_unopenable_lock_path, id="lock-path-is-a-directory"),
    ],
)
def test_is_locked_false_when_nobody_holds_the_operating_system_lock(
    fake_game_dir: Path, setup: Callable[[Path], None]
) -> None:
    """is_locked returns False whenever no process holds the OS lock.

    The game directory has no lock file at all, a leftover lock file naming this
    process that nobody holds, or a directory where the lock file belongs, which
    cannot be opened. Only the OS lock counts as held, so all three report False.
    """
    setup(fake_game_dir)

    assert is_locked(fake_game_dir) is False


# --- game_lock lifecycle -----------------------------------------------


def test_game_lock_holds_the_lock_for_the_duration_of_the_block(
    clean_repo: Repo,
) -> None:
    """game_lock acquires lock on entry and releases on exit.

    is_locked() false before block, true during (fcntl held), false after context
    exit. Lock is immediately released.
    """
    assert is_locked(clean_repo.path) is False

    with game_lock(clean_repo.path, "testing"):
        assert is_locked(clean_repo.path) is True

    assert is_locked(clean_repo.path) is False


def test_game_lock_is_free_again_after_a_successful_block(clean_repo: Repo) -> None:
    """Second game_lock call acquires immediately after first releases.

    After first lock block exits successfully, second caller acquires lock within
    1 second (not waiting).
    """
    with game_lock(clean_repo.path, "testing"):
        pass

    started = time.monotonic()
    with game_lock(clean_repo.path, "next run"):
        pass

    assert time.monotonic() - started < 1.0


def test_game_lock_is_free_again_when_the_body_raises(clean_repo: Repo) -> None:
    """game_lock releases lock even when block raises exception.

    Block raises ValueError("boom"); lock is cleaned up via finally. Next caller
    acquires lock immediately (< 1s).
    """
    with pytest.raises(ValueError, match="boom"), game_lock(clean_repo.path, "testing"):
        raise ValueError("boom")

    assert is_locked(clean_repo.path) is False
    started = time.monotonic()
    with game_lock(clean_repo.path, "next run"):
        pass

    assert time.monotonic() - started < 1.0


def test_game_lock_file_content_records_pid_and_operation(clean_repo: Repo) -> None:
    """Lock file contains JSON dict with pid and operation during lock hold.

    While lock is held, LOCK_FILE_NAME contains JSON with current pid and
    operation="installing SAIN".
    """
    lock_path = clean_repo.path / LOCK_FILE_NAME

    with game_lock(clean_repo.path, "installing SAIN"):
        content = json.loads(lock_path.read_text(encoding="utf-8"))

    assert content["pid"] == os.getpid()
    assert content["operation"] == "installing SAIN"


# --- lock files nobody holds never block ---------------------------------


@pytest.mark.parametrize(
    "pid_source",
    [
        pytest.param(_dead_pid, id="dead-pid"),
        pytest.param(os.getpid, id="live-pid"),
    ],
)
def test_a_leftover_lock_file_nobody_holds_does_not_block(
    clean_repo: Repo, pid_source: Callable[[], int]
) -> None:
    """game_lock ignores a leftover lock file whatever process it names.

    The file names a process that no longer exists, or this very process, but
    nobody holds the operating-system lock on it. Only that lock blocks, so the
    new operation gets the lock at once (under a second) and its own name ends
    up in the file.
    """
    lock_path = _write_lock(clean_repo.path, pid=pid_source(), operation="old run")
    started = time.monotonic()

    with game_lock(clean_repo.path, "new run"):
        content = json.loads(lock_path.read_text(encoding="utf-8"))

    assert content["operation"] == "new run"
    assert time.monotonic() - started < 1.0


# --- releasing disturbs nobody -------------------------------------------


def test_release_leaves_the_lock_file_and_its_content_in_place(
    clean_repo: Repo,
) -> None:
    """game_lock release drops OS lock but leaves file unchanged.

    Lock file and its JSON content persist after block exits. OS fcntl lock is
    dropped but the file remains with identical content.
    """
    lock_path = clean_repo.path / LOCK_FILE_NAME

    with game_lock(clean_repo.path, "installing SAIN"):
        held_content = lock_path.read_text(encoding="utf-8")

    assert lock_path.is_file()
    assert lock_path.read_text(encoding="utf-8") == held_content


def test_a_waiter_takes_the_lock_right_after_the_holder_releases(
    clean_repo: Repo,
) -> None:
    """Concurrent waiter acquires lock when holder releases it.

    Holder thread holds lock for 0.3s then releases; waiter thread polls (0.05s
    interval) until lock is free. Waiter acquisition time is between 0.15s and
    1.3s (allowing for polling delays).
    """
    release_after = 0.3
    holder_ready = threading.Event()

    def hold() -> None:
        with game_lock(clean_repo.path, "installing OtherMod"):
            holder_ready.set()
            time.sleep(release_after)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_ready.wait(timeout=1.0)

    started = time.monotonic()
    with game_lock(clean_repo.path, "new run", poll_interval=0.05):
        elapsed = time.monotonic() - started
    holder.join(timeout=1.0)

    assert elapsed >= release_after * 0.5
    assert elapsed < release_after + 1.0


# --- timeout path and its message ---------------------------------------


@pytest.mark.parametrize(
    "raw_content",
    ["not json at all", "{}", "[1, 2, 3]", "", "\xff\xfe binary"],
    ids=["invalid-json", "empty-object", "json-array", "empty-file", "invalid-utf8"],
)
def test_invalid_or_empty_lock_content_of_a_holder_waits_then_times_out(clean_repo: Repo, raw_content: str) -> None:
    """Timeout on held lock with unparseable content raises generic message.

    Lock file is held but contains unparseable JSON; waiter times out and gets
    a generic error message (no operation name). Parameterized bad contents:
    invalid JSON, empty object, JSON array, empty file, invalid UTF-8.
    """
    with (
        _held(clean_repo.path, raw_content),
        pytest.raises(OperationInProgressError) as excinfo,
        game_lock(clean_repo.path, "new run", timeout=0.3, poll_interval=0.05),
    ):
        pass

    message = str(excinfo.value)
    assert message == "Another operation is still running. Try again when it finishes."
    assert "git" not in message.lower()
    assert "commit" not in message.lower()


def test_timeout_message_omits_the_operation_when_the_lock_file_vanishes_mid_wait(
    clean_repo: Repo,
) -> None:
    """Timeout message is generic when lock file disappears during wait (race condition).

    Lock is held initially, but file is deleted mid-wait by a background thread.
    _holder() handles the OSError gracefully and returns no operation name;
    timeout raises generic message.
    """
    with _held(clean_repo.path, _holder_content("installing SAIN")) as lock:

        def _delete_soon() -> None:
            time.sleep(0.1)
            lock.unlink()

        deleter = threading.Thread(target=_delete_soon)
        deleter.start()

        with (
            pytest.raises(OperationInProgressError) as excinfo,
            game_lock(clean_repo.path, "new run", timeout=0.6, poll_interval=0.02),
        ):
            pass
        deleter.join(timeout=1.0)

    assert str(excinfo.value) == ("Another operation is still running. Try again when it finishes.")


def test_timeout_error_message_names_the_blocking_operation(
    clean_repo: Repo,
) -> None:
    """game_lock timeout message includes the blocking operation name.

    Lock is held (operation="installing SAIN") beyond timeout=0.3; game_lock
    raises OperationInProgressError with message naming the blocker.
    """
    with (
        _held(clean_repo.path, _holder_content("installing SAIN")),
        pytest.raises(OperationInProgressError) as excinfo,
        game_lock(
            clean_repo.path,
            "installing something else",
            timeout=0.3,
            poll_interval=0.05,
        ),
    ):
        pass

    assert str(excinfo.value) == ("Another operation is still running (installing SAIN). Try again when it finishes.")


# --- waiting progress ------------------------------------------------------


def test_waiting_for_a_held_lock_emits_a_progress_event(
    clean_repo: Repo,
) -> None:
    """game_lock emits progress event while waiting for a held lock.

    Lock is held (operation="installing SAIN"); waiter polls with timeout=0.3s.
    Progress callback receives (step="check", percent=0, message with blocker name).
    """
    calls: list[tuple[str, int, str]] = []

    with (
        _held(clean_repo.path, _holder_content("installing SAIN")),
        pytest.raises(OperationInProgressError),
        game_lock(
            clean_repo.path,
            "installing something else",
            progress=lambda step, percent, message: calls.append((step, percent, message)),
            timeout=0.3,
            poll_interval=0.05,
        ),
    ):
        pass

    assert calls, "no progress event was reported while waiting"
    step, percent, message = calls[0]
    assert step == "check"
    assert percent == 0
    assert "waiting for another operation to finish" in message
    assert "installing SAIN" in message


def test_no_progress_is_emitted_when_the_lock_is_free(clean_repo: Repo) -> None:
    """game_lock emits no progress when lock is free (immediate acquisition).

    Lock is not held; game_lock acquires immediately without polling. Progress
    callback is not invoked.
    """
    calls: list[tuple[str, int, str]] = []

    with game_lock(
        clean_repo.path,
        "installing SAIN",
        progress=lambda step, percent, message: calls.append((step, percent, message)),
    ):
        pass

    assert not calls


# --- service-layer contention: waits, never sees a dirty-tree error --------


def test_install_waits_for_a_held_lock_instead_of_raising_dirty_tree(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """install_from_archive waits for held lock instead of raising dirty tree.

    Holder thread holds lock for 0.3s ("installing OtherMod"); install waits and
    acquires after release. Install succeeds without raising DirtyWorkingTreeError.
    Lock is free after both complete.
    """
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    release_after = 0.3
    holder_ready = threading.Event()

    def hold() -> None:
        with game_lock(clean_repo.path, "installing OtherMod"):
            holder_ready.set()
            time.sleep(release_after)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_ready.wait(timeout=1.0)

    started = time.monotonic()
    result = services.install_from_archive(clean_repo.path, archive)
    elapsed = time.monotonic() - started
    holder.join(timeout=1.0)

    assert result.mod.name == "CoolPlugin"
    assert elapsed >= release_after * 0.5
    assert is_locked(clean_repo.path) is False


def test_two_installs_of_different_mods_at_once_both_succeed(clean_repo: Repo, make_archive: MakeArchive) -> None:
    """Two concurrent installs both succeed; lock serializes them.

    Two threads call install_from_archive concurrently (ModA and ModB). Lock
    ensures no collision; both complete, both mods exist, both trailers in
    history, no errors, lock is free after.
    """
    archive_a = make_archive({"BepInEx/plugins/ModA.dll": b"a-bytes"}, "zip")
    archive_b = make_archive({"BepInEx/plugins/ModB.dll": b"b-bytes"}, "zip")
    results: dict[str, services.InstallResult] = {}
    errors: dict[str, BaseException] = {}

    def run(key: str, archive: Path) -> None:
        try:
            results[key] = services.install_from_archive(clean_repo.path, archive)
        except BaseException as error:
            errors[key] = error
            raise

    thread_a = threading.Thread(target=run, args=("a", archive_a))
    thread_b = threading.Thread(target=run, args=("b", archive_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not errors
    assert set(results) == {"a", "b"}
    assert results["a"].mod.name == "ModA"
    assert results["b"].mod.name == "ModB"
    assert (clean_repo.path / "BepInEx/plugins/ModA.dll").is_file()
    assert (clean_repo.path / "BepInEx/plugins/ModB.dll").is_file()
    mod_trailers = {commit.trailers.get("Mod") for commit in clean_repo.log()}
    assert {"ModA", "ModB"} <= mod_trailers
    assert is_locked(clean_repo.path) is False


# --- read-only calls ignore the lock -------------------------------------


def test_read_only_calls_ignore_a_held_lock_and_return_quickly(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Read-only operations (status, list, history) bypass the lock entirely."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})

    with _held(clean_repo.path, _holder_content("installing SomethingElse")):
        started = time.monotonic()
        status_report = services.status(clean_repo.path)
        mods = services.list_mods(clean_repo.path)
        commits = services.history(clean_repo.path)
        files = services.mod_files(clean_repo.path, "ModA")
        elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert [mod.name for mod in mods] == ["ModA"]
    assert files
    assert commits
    assert not status_report.manual_mods


# --- the lock file itself never counts as an unmanaged change -------------


def test_lock_file_never_appears_in_status_and_tree_stays_clean(
    clean_repo: Repo,
) -> None:
    """Lock file is hidden from status; tree stays clean.

    LOCK_FILE_NAME exists (written to disk); status() doesn't report it as
    untracked or modified. is_clean() returns True (locked status is invisible).
    """
    (clean_repo.path / LOCK_FILE_NAME).write_text("{}", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert not report.manual_mods
    assert not report.other_untracked
    assert not report.other_modified
    assert clean_repo.is_clean() is True


# --- a path that is no game directory is refused before the lock is taken --

LOCK_TAKING_CALLS = [
    services.clean_profile,
    services.remove_mod,
    services.disable_mod,
    services.enable_mod,
]
LOCK_TAKING_CALL_IDS = ["clean_profile", "remove_mod", "disable_mod", "enable_mod"]


@pytest.mark.parametrize("call", LOCK_TAKING_CALLS, ids=LOCK_TAKING_CALL_IDS)
def test_a_missing_path_is_refused_in_plain_language_by_every_locking_call(
    tmp_path: Path,
    call: Callable[
        [Path, str], services.CleanupResult | services.RemoveResult | services.ToggleResult
    ],
) -> None:
    """Every operation taking the lock refuses a missing directory as no game directory.

    Calls the service function with a path that does not exist and asserts a
    NotAGameDirError naming the path, whose message mentions neither the lock
    file nor an operating-system errno, and that nothing was created at the
    path. Reason: the lock file used to be opened before any check, so a
    missing directory surfaced as a raw OSError the adapters could only report
    as an internal error.
    """
    missing = tmp_path / "gone"

    with pytest.raises(NotAGameDirError) as error:
        call(missing, "ServerMod")

    message = str(error.value)
    assert str(missing) in message
    assert LOCK_FILE_NAME not in message
    assert "Errno" not in message
    assert missing.exists() is False


def test_a_directory_that_is_no_spt_installation_keeps_its_content(tmp_path: Path) -> None:
    """A locking call on an ordinary directory refuses it and leaves no lock file.

    Calls clean_profile on an empty directory that is no SPT installation and
    asserts NotAGameDirError plus an unchanged, still empty directory. Reason:
    taking the lock first dropped a .sptmod-lock file into whatever directory
    a caller passed, including ones sptmod has no business writing to.
    """
    with pytest.raises(NotAGameDirError):
        services.clean_profile(tmp_path, "ServerMod")

    assert not list(tmp_path.iterdir())
