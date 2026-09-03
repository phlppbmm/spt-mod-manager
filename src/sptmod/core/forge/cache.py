"""Persistent cache for downloaded mod archives.

Layout below the cache directory, keyed by the Forge version id::

    <key>/meta.json          {"name", "size", "url"}
    <key>/<name>             the complete archive
    <key>.part               an unfinished download nobody works on
    <key>.part.json          {"name", "expected_length", "url"}
    <key>.<token>.part       an unfinished download claimed by one process
    <key>.<token>.part.json  its metadata

A partial download is claimed by renaming it, so two processes downloading the
same archive never write into the same file; whoever finishes first publishes,
the other one simply overwrites the identical payload. The cache is
disposable: every read or clean-up failure degrades to "no entry" or "nothing
freed" instead of failing the download it belongs to.
"""

import contextlib
import json
import math
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sptmod.core import workspace
from sptmod.core.errors import ArchiveError

MAX_GB_ENV_VAR: Final = "SPTMOD_DOWNLOAD_CACHE_MAX_GB"
DEFAULT_MAX_BYTES: Final = 20 * 1024**3
PART_MAX_AGE_SECONDS: Final = 7 * 24 * 60 * 60

_META_FILE_NAME: Final = "meta.json"
_PART_SUFFIX: Final = ".part"


@dataclass(frozen=True)
class PartState:
    """The unfinished download one process has taken responsibility for."""

    work: Path
    offset: int
    name: str | None
    expected_length: int | None
    url: str


def max_bytes() -> int:
    """Return the size the cached archives may occupy together.

    Read from the environment on every call so a changed limit takes effect
    without a restart; anything unreadable or not positive means the default.
    """
    raw = os.environ.get(MAX_GB_ENV_VAR)
    if raw is None:
        return DEFAULT_MAX_BYTES
    try:
        gigabytes = float(raw)
    except ValueError:
        return DEFAULT_MAX_BYTES
    if not math.isfinite(gigabytes) or gigabytes <= 0:
        return DEFAULT_MAX_BYTES
    limit = int(gigabytes * 1024**3)
    return limit if limit > 0 else DEFAULT_MAX_BYTES


def find(
    key: str, expected_length: int | None = None, cache_dir: Path | None = None
) -> Path | None:
    """Return the complete archive stored for ``key``, or None if there is none.

    An archive whose size differs from ``expected_length`` is removed and
    reported as missing, so a damaged file is downloaded again exactly once.
    A hit counts as a use and refreshes the entry's age.
    """
    entry = _directory(cache_dir) / key
    archive = _archive(entry)
    if archive is None:
        return None
    try:
        size = archive.stat().st_size
    except OSError:
        return None
    if expected_length is not None and size != expected_length:
        _remove(entry)
        return None
    with contextlib.suppress(OSError):
        os.utime(entry)
    return archive


def claim(
    key: str,
    expected_length: int | None,
    url: str,
    cache_dir: Path | None = None,
) -> PartState:
    """Take responsibility for the unfinished download of ``key``.

    An unclaimed partial file is taken over by renaming it under a name unique
    to this process; the rename fails when another process was faster, and
    then this download starts from the beginning. A partial file that was
    downloaded for a different size is discarded as well.
    """
    directory = _directory(cache_dir)
    token = f"{os.getpid()}-{secrets.token_hex(4)}"
    work = directory / f"{key}.{token}{_PART_SUFFIX}"
    unclaimed = directory / f"{key}{_PART_SUFFIX}"
    try:
        os.replace(unclaimed, work)
        os.replace(_sidecar(unclaimed), _sidecar(work))
    except OSError:
        pass
    name, recorded = _read_part(_sidecar(work))
    offset = _size(work)
    if offset == 0 or (
        expected_length is not None
        and recorded is not None
        and recorded != expected_length
    ):
        _remove(work)
        _remove(_sidecar(work))
        return PartState(
            work=work,
            offset=0,
            name=None,
            expected_length=expected_length,
            url=url,
        )
    return PartState(
        work=work,
        offset=offset,
        name=name,
        expected_length=expected_length,
        url=url,
    )


def keep(state: PartState, name: str, key: str, cache_dir: Path | None = None) -> Path:
    """Publish the finished download as the cache entry for ``key``.

    Returns:
        The path of the archive inside the cache.

    Raises:
        ArchiveError: The finished download cannot be moved into the cache; the
            unfinished download stays where it is and is handed back by
            ``release``.
    """
    entry = _directory(cache_dir) / key
    entry.mkdir(parents=True, exist_ok=True)
    archive = entry / name
    try:
        os.replace(state.work, archive)
    except OSError as error:
        raise ArchiveError(f"{name} cannot be kept for later runs: {error}") from error
    _remove(_sidecar(state.work))
    _write(
        entry / _META_FILE_NAME,
        {"name": name, "size": _size(archive), "url": state.url},
    )
    return archive


def release(state: PartState, key: str, cache_dir: Path | None = None) -> None:
    """Hand an interrupted download back so the next run can continue it.

    Nothing is kept when another process meanwhile left its own unfinished
    download for the same key behind.
    """
    unclaimed = _directory(cache_dir) / f"{key}{_PART_SUFFIX}"
    if unclaimed.exists() or _size(state.work) == 0:
        _remove(state.work)
        _remove(_sidecar(state.work))
        return
    _write(
        _sidecar(state.work),
        {
            "name": state.name,
            "expected_length": state.expected_length,
            "url": state.url,
        },
    )
    try:
        os.replace(state.work, unclaimed)
        os.replace(_sidecar(state.work), _sidecar(unclaimed))
    except OSError:
        _remove(state.work)
        _remove(_sidecar(state.work))


def sweep(keep_key: str | None = None, cache_dir: Path | None = None) -> int:
    """Drop forgotten partial downloads and the least recently used archives.

    The entry named by ``keep_key`` stays, however large the cache is.

    Returns:
        The number of bytes freed.
    """
    directory = _directory(cache_dir)
    freed = _sweep_parts(directory)
    entries = _entries(directory)
    total = sum(size for _used, size, _entry in entries)
    limit = max_bytes()
    for _used, size, entry in sorted(entries):
        if total <= limit:
            break
        if entry.name == keep_key:
            continue
        if _remove(entry):
            total -= size
            freed += size
    return freed


def clear(cache_dir: Path | None = None) -> int:
    """Remove every cached archive and partial download.

    Returns:
        The number of bytes freed.
    """
    freed = 0
    for child in _children(_directory(cache_dir)):
        size = _size(child) if child.is_file() else _tree_size(child)
        if _remove(child):
            freed += size
    return freed


def _directory(cache_dir: Path | None) -> Path:
    return workspace.download_cache_dir() if cache_dir is None else cache_dir


def _sidecar(part: Path) -> Path:
    return part.with_name(f"{part.name}.json")


def _children(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except OSError:
        return []


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree_size(directory: Path) -> int:
    return sum(_size(item) for item in directory.rglob("*") if item.is_file())


def _remove(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _write(path: Path, content: dict[str, str | int | None]) -> None:
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(content), encoding="utf-8")


def _read(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        content: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return content if isinstance(content, dict) else {}


def _archive(entry: Path) -> Path | None:
    """Return the archive an entry's metadata names, if it lies there."""
    name = _read(entry / _META_FILE_NAME).get("name")
    if not isinstance(name, str) or name != Path(name).name:
        return None
    archive = entry / name
    return archive if archive.is_file() else None


def _read_part(sidecar: Path) -> tuple[str | None, int | None]:
    content = _read(sidecar)
    name = content.get("name")
    expected_length = content.get("expected_length")
    return (
        name if isinstance(name, str) else None,
        expected_length if isinstance(expected_length, int) else None,
    )


def _sweep_parts(directory: Path) -> int:
    deadline = time.time() - PART_MAX_AGE_SECONDS
    freed = 0
    for child in _children(directory):
        if child.is_dir() or _PART_SUFFIX not in child.suffixes:
            continue
        try:
            if child.stat().st_mtime >= deadline:
                continue
        except OSError:
            continue
        size = _size(child)
        if _remove(child):
            freed += size
    return freed


def _entries(directory: Path) -> list[tuple[float, int, Path]]:
    entries: list[tuple[float, int, Path]] = []
    for child in _children(directory):
        if not child.is_dir():
            continue
        try:
            used = child.stat().st_mtime
        except OSError:
            continue
        entries.append((used, _tree_size(child), child))
    return entries
