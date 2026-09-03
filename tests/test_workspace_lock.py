"""Tests for the apply lock and staging area (``sptmod.core.workspace``).

Covers ``staging_dir()`` (cache-dir resolution, on-demand creation, sweeping
of stale entries), the lock lifecycle around ``game_lock()`` (held for the
duration of the block, free again on success and on a raised exception, JSON
content naming the holder), leftover lock files never blocking anyone, the
timeout path with its exact user-facing message, the waiting progress
callback, and the service-layer guarantees the lock exists for: a second
operation waits instead of ever seeing the held lock as an unmanaged file,
read-only calls are never blocked, and the lock file never surfaces in
``status()``/``is_clean()``.
"""

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

from sptmod.core import game_dir, services, workspace
from sptmod.core.errors import OperationInProgressError
from sptmod.core.repo import Repo
from sptmod.core.workspace import LOCK_FILE_NAME, game_lock, is_locked, staging_dir


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


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
def _held(path: Path, raw_content: str) -> Iterator[Path]:
    """Really hold the lock on ``path`` with ``raw_content`` in its file."""
    lock = path / LOCK_FILE_NAME
    handle = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        assert workspace._try_lock(handle), "the lock was not free"
        os.truncate(handle, 0)
        os.write(handle, raw_content.encode("utf-8"))
        yield lock
    finally:
        workspace._unlock(handle)
        os.close(handle)


def _holder_content(operation: str, *, pid: int | None = None) -> str:
    return json.dumps(
        {"pid": os.getpid() if pid is None else pid, "operation": operation}
    )


# --- staging_dir ---------------------------------------------------------


def test_staging_dir_honors_xdg_cache_home_and_is_created_on_demand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    assert not cache_home.exists()

    result = staging_dir()

    assert result == cache_home / "sptmod" / "staging"
    assert result.is_dir()


def test_staging_dir_sweeps_entries_older_than_a_day_but_keeps_fresh_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


# --- is_locked -------------------------------------------------------------


def test_is_locked_false_without_a_lock_file(fake_game_dir: Path) -> None:
    assert is_locked(fake_game_dir) is False


def test_is_locked_true_while_another_operation_holds_the_lock(
    fake_game_dir: Path,
) -> None:
    with _held(fake_game_dir, _holder_content("installing SAIN")):
        assert is_locked(fake_game_dir) is True


def test_is_locked_false_for_a_lock_file_nobody_holds(fake_game_dir: Path) -> None:
    _write_lock(fake_game_dir, pid=os.getpid(), operation="crashed run")

    assert is_locked(fake_game_dir) is False


# --- game_lock lifecycle -----------------------------------------------


def test_game_lock_holds_the_lock_for_the_duration_of_the_block(
    clean_repo: Repo,
) -> None:
    assert is_locked(clean_repo.path) is False

    with game_lock(clean_repo.path, "testing"):
        assert is_locked(clean_repo.path) is True

    assert is_locked(clean_repo.path) is False


def test_game_lock_is_free_again_after_a_successful_block(clean_repo: Repo) -> None:
    with game_lock(clean_repo.path, "testing"):
        pass

    started = time.monotonic()
    with game_lock(clean_repo.path, "next run"):
        pass

    assert time.monotonic() - started < 1.0


def test_game_lock_is_free_again_when_the_body_raises(clean_repo: Repo) -> None:
    with pytest.raises(ValueError, match="boom"), game_lock(clean_repo.path, "testing"):
        raise ValueError("boom")

    assert is_locked(clean_repo.path) is False
    started = time.monotonic()
    with game_lock(clean_repo.path, "next run"):
        pass

    assert time.monotonic() - started < 1.0


def test_game_lock_file_content_records_pid_and_operation(clean_repo: Repo) -> None:
    lock_path = clean_repo.path / LOCK_FILE_NAME

    with game_lock(clean_repo.path, "installing SAIN"):
        content = json.loads(lock_path.read_text(encoding="utf-8"))

    assert content["pid"] == os.getpid()
    assert content["operation"] == "installing SAIN"


# --- lock files nobody holds never block ---------------------------------


def test_leftover_lock_file_of_a_dead_process_does_not_block(
    clean_repo: Repo,
) -> None:
    _write_lock(clean_repo.path, pid=_dead_pid(), operation="crashed run")
    started = time.monotonic()

    with game_lock(clean_repo.path, "new run"):
        content = json.loads(
            (clean_repo.path / LOCK_FILE_NAME).read_text(encoding="utf-8")
        )

    assert content["operation"] == "new run"
    assert time.monotonic() - started < 1.0


def test_leftover_lock_file_naming_a_live_pid_does_not_block(
    clean_repo: Repo,
) -> None:
    """Only actually holding the lock blocks, never what the file claims."""
    lock_path = _write_lock(clean_repo.path, pid=os.getpid(), operation="ancient run")
    started = time.monotonic()

    with game_lock(clean_repo.path, "new run"):
        content = json.loads(lock_path.read_text(encoding="utf-8"))

    assert content["operation"] == "new run"
    assert time.monotonic() - started < 1.0


# --- releasing disturbs nobody -------------------------------------------


def test_release_leaves_the_lock_file_and_its_content_in_place(
    clean_repo: Repo,
) -> None:
    lock_path = clean_repo.path / LOCK_FILE_NAME

    with game_lock(clean_repo.path, "installing SAIN"):
        held_content = lock_path.read_text(encoding="utf-8")

    assert lock_path.is_file()
    assert lock_path.read_text(encoding="utf-8") == held_content


def test_a_waiter_takes_the_lock_right_after_the_holder_releases(
    clean_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "_LOCK_POLL_SECONDS", 0.05)
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
    with game_lock(clean_repo.path, "new run"):
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
def test_invalid_or_empty_lock_content_of_a_holder_waits_then_times_out(
    clean_repo: Repo, monkeypatch: pytest.MonkeyPatch, raw_content: str
) -> None:
    monkeypatch.setattr(workspace, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(workspace, "_LOCK_POLL_SECONDS", 0.05)

    with (
        _held(clean_repo.path, raw_content),
        pytest.raises(OperationInProgressError) as excinfo,
        game_lock(clean_repo.path, "new run"),
    ):
        pass

    message = str(excinfo.value)
    assert message == "Another operation is still running. Try again when it finishes."
    assert "git" not in message.lower()
    assert "commit" not in message.lower()


def test_timeout_error_message_names_the_blocking_operation(
    clean_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(workspace, "_LOCK_POLL_SECONDS", 0.05)

    with (
        _held(clean_repo.path, _holder_content("installing SAIN")),
        pytest.raises(OperationInProgressError) as excinfo,
        game_lock(clean_repo.path, "installing something else"),
    ):
        pass

    assert str(excinfo.value) == (
        "Another operation is still running (installing SAIN). "
        "Try again when it finishes."
    )


# --- waiting progress ------------------------------------------------------


def test_waiting_for_a_held_lock_emits_a_progress_event(
    clean_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(workspace, "_LOCK_POLL_SECONDS", 0.05)
    calls: list[tuple[str, int, str]] = []

    with (
        _held(clean_repo.path, _holder_content("installing SAIN")),
        pytest.raises(OperationInProgressError),
        game_lock(
            clean_repo.path,
            "installing something else",
            progress=lambda step, percent, message: calls.append(
                (step, percent, message)
            ),
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
    calls: list[tuple[str, int, str]] = []

    with game_lock(
        clean_repo.path,
        "installing SAIN",
        progress=lambda step, percent, message: calls.append((step, percent, message)),
    ):
        pass

    assert calls == []


# --- service-layer contention: waits, never sees a dirty-tree error --------


def test_install_waits_for_a_held_lock_instead_of_raising_dirty_tree(
    clean_repo: Repo, make_archive
) -> None:
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


def test_two_installs_of_different_mods_at_once_both_succeed(
    clean_repo: Repo, make_archive
) -> None:
    archive_a = make_archive({"BepInEx/plugins/ModA.dll": b"a-bytes"}, "zip")
    archive_b = make_archive({"BepInEx/plugins/ModB.dll": b"b-bytes"}, "zip")
    results: dict[str, services.InstallResult] = {}
    errors: dict[str, BaseException] = {}

    def run(key: str, archive: Path) -> None:
        try:
            results[key] = services.install_from_archive(clean_repo.path, archive)
        except BaseException as error:  # noqa: BLE001 -- surfaced below
            errors[key] = error

    thread_a = threading.Thread(target=run, args=("a", archive_a))
    thread_b = threading.Thread(target=run, args=("b", archive_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert errors == {}
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
    clean_repo: Repo, install_commit
) -> None:
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
    assert status_report.manual_mods == []


# --- the lock file itself never counts as an unmanaged change -------------


def test_lock_file_never_appears_in_status_and_tree_stays_clean(
    clean_repo: Repo,
) -> None:
    (clean_repo.path / LOCK_FILE_NAME).write_text("{}", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.manual_mods == []
    assert report.other_untracked == []
    assert report.other_modified == []
    assert clean_repo.is_clean() is True
