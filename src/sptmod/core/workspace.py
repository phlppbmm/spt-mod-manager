"""Shared staging area and the exclusive lock guarding game directory changes.

Downloads and extractions of parallel operations happen side by side in the
staging area; only the short final step that changes the game directory is
exclusive, guarded by :func:`game_lock`. Exclusivity is an operating-system
lock held on the file ``.sptmod-lock`` in the game directory: the file is
created once and then stays where it is, only the lock on it comes and goes,
and the system drops that lock as soon as the holding process ends, however
it ends. A file left behind therefore never blocks anyone.
"""

import json
import os
import shutil
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final

import platformdirs

from sptmod.core.errors import OperationInProgressError
from sptmod.core.models import Json, Progress

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

LOCK_FILE_NAME: Final = ".sptmod-lock"

_APP_NAME: Final = "sptmod"
_STAGING_DIR_NAME: Final = "staging"
_DOWNLOAD_CACHE_DIR_NAME: Final = "downloads"
_STAGING_MAX_AGE_SECONDS: Final = 24 * 60 * 60
_LOCK_TIMEOUT_SECONDS: Final = 300.0
_LOCK_POLL_SECONDS: Final = 0.2
_WAIT_NOTICE_SECONDS: Final = 5.0


def staging_dir() -> Path:
    """Return the directory downloads and extractions are unpacked into.

    Lives in the user's cache directory, never in the game directory, and is
    created on demand. Leftovers of a killed process older than a day are
    swept on the way, so the cache cannot grow without bound.
    """
    path = platformdirs.user_cache_path(_APP_NAME, appauthor=False)
    staging = path / _STAGING_DIR_NAME
    staging.mkdir(parents=True, exist_ok=True)
    _sweep(staging)
    return staging


def download_cache_dir() -> Path:
    """Return the directory downloaded mod archives are kept in.

    Lives next to the staging area in the user's cache directory and is
    created on demand. Its contents survive across runs so an archive is
    downloaded once; size is bounded by the cache's own housekeeping, not by
    age, hence nothing is swept here.
    """
    path = platformdirs.user_cache_path(_APP_NAME, appauthor=False)
    downloads = path / _DOWNLOAD_CACHE_DIR_NAME
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def is_locked(game_dir: Path) -> bool:
    """Return whether another operation currently holds ``game_dir``."""
    lock = game_dir / LOCK_FILE_NAME
    if not lock.exists():
        return False
    try:
        handle = os.open(lock, os.O_RDWR)
    except OSError:
        return False
    try:
        if not _try_lock(handle):
            return True
        _unlock(handle)
    finally:
        os.close(handle)
    return False


@contextmanager
def game_lock(
    game_dir: Path,
    operation: str,
    progress: Progress | None = None,
    *,
    timeout: float | None = None,
    poll_interval: float | None = None,
) -> Generator[None]:
    """Hold the exclusive right to change ``game_dir`` for the block.

    ``operation`` describes the work in plain language, for example
    ``installing SAIN``; a second process waiting for the lock reports it.
    Waiting is the normal case and takes seconds; an operation that ended
    abruptly holds nothing back, because the system releases its lock.

    Args:
        game_dir: The game directory to guard.
        operation: The plain-language description of the current work.
        progress: Callback informed while waiting for another operation.
        timeout: Maximum seconds to wait for another operation to finish.
        poll_interval: Seconds between two attempts to take the lock.

    Raises:
        OperationInProgressError: another operation still held the game
            directory after the maximum waiting time.
    """
    handle = _acquire(
        game_dir / LOCK_FILE_NAME,
        operation,
        progress,
        _LOCK_TIMEOUT_SECONDS if timeout is None else timeout,
        _LOCK_POLL_SECONDS if poll_interval is None else poll_interval,
    )
    try:
        yield
    finally:
        _unlock(handle)
        os.close(handle)


def _acquire(
    lock: Path,
    operation: str,
    progress: Progress | None,
    timeout: float,
    poll_interval: float,
) -> int:
    """Take the lock, returning the open file the lock is held on."""
    deadline = time.monotonic() + timeout
    announced: float | None = None
    handle = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            if _try_lock(handle):
                _record_holder(handle, operation)
                return handle
            now = time.monotonic()
            if now >= deadline:
                raise OperationInProgressError(_holder(lock))
            if progress is not None and (
                announced is None or now - announced >= _WAIT_NOTICE_SECONDS
            ):
                announced = now
                holder = _holder(lock) or "unknown"
                progress(
                    step="check",
                    percent=0,
                    message=f"waiting for another operation to finish ({holder})",
                )
            time.sleep(poll_interval)
    except BaseException:
        os.close(handle)
        raise


def _try_lock(handle: int) -> bool:
    """Take the exclusive lock on ``handle``, reporting whether it was free."""
    try:
        if sys.platform == "win32":
            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: int) -> None:
    """Give up the lock held on ``handle``, leaving the file in place."""
    if sys.platform == "win32":
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)


def _record_holder(handle: int, operation: str) -> None:
    """Write down who holds the lock, so waiters can name the operation."""
    content = json.dumps(
        {
            "pid": os.getpid(),
            "operation": operation,
            "started": datetime.now().astimezone().isoformat(),
        }
    )
    os.lseek(handle, 0, os.SEEK_SET)
    os.truncate(handle, 0)
    os.write(handle, content.encode("utf-8"))


def _holder(lock: Path) -> str:
    """Return the operation named in ``lock``, empty if it names none.

    A half-written or damaged lock file yields no name rather than an error,
    so the caller keeps waiting for the process holding the lock.
    """
    try:
        raw = lock.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    try:
        content: Json = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(content, dict):
        return ""
    operation = content.get("operation")
    return operation if isinstance(operation, str) else ""


def _sweep(staging: Path) -> None:
    """Remove staging leftovers of processes that were killed."""
    deadline = time.time() - _STAGING_MAX_AGE_SECONDS
    for entry in staging.iterdir():
        try:
            if entry.stat().st_mtime >= deadline:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            continue
