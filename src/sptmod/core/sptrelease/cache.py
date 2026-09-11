"""The stored copy of the published SPT releases.

A listing costs one anonymous request per release source against a budget of
sixty an hour, so it is fetched once and read from here afterwards until a
caller asks for a fresh one. The same copy is what keeps the release panel
populated while the sources refuse to answer. It is disposable: anything that
cannot be read counts as nothing stored, and a copy that cannot be written
changes nothing.
"""

import hashlib
import json
import os
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from sptmod.core.models import Json
from sptmod.core.sptrelease.client import SptRelease
from sptmod.core.workspace import spt_release_cache_dir

_KEY_LENGTH: Final = 16
_PART_SUFFIX: Final = ".part"


@dataclass(frozen=True)
class CachedReleases:
    """The stored release listing and when it was downloaded."""

    releases: list[SptRelease]
    fetched_at: datetime


def read_cached_releases(urls: Sequence[str]) -> CachedReleases | None:
    """Return the listing stored for the release sources ``urls``.

    Args:
        urls: The release source URLs the listing was fetched from; they key
            the stored copy, so a mirror never answers for the default
            sources.

    Returns:
        The stored listing, or None when nothing usable is stored -- a
        missing, unreadable, damaged or half-written copy all say the same.
    """
    try:
        stored: Json = json.loads(_entry_path(urls).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict):
        return None
    entries = stored.get("releases")
    releases = [
        release
        for item in (entries if isinstance(entries, list) else [])
        if (release := _release(item)) is not None
    ]
    fetched_at = _time(stored.get("fetched_at"))
    if not releases or fetched_at is None:
        return None
    return CachedReleases(releases=releases, fetched_at=fetched_at)


def write_cached_releases(urls: Sequence[str], releases: Sequence[SptRelease]) -> None:
    """Store ``releases`` as the listing of the sources ``urls``.

    The copy is written beside its place, under a name of this process alone,
    and moved over it: a process that ends mid-write leaves the previous copy
    whole, and two processes writing at once never share a half-written file.
    A copy that cannot be written at all is skipped silently: the caller keeps
    the listing it just fetched, and the next call fetches again.
    """
    payload: Json = {
        "fetched_at": datetime.now().astimezone().isoformat(),
        "releases": [_entry(release) for release in releases],
    }
    with suppress(OSError):
        path = _entry_path(urls)
        half_written = path.with_name(f"{path.name}.{os.getpid()}{_PART_SUFFIX}")
        half_written.write_text(json.dumps(payload), encoding="utf-8")
        half_written.replace(path)


def _entry_path(urls: Sequence[str]) -> Path:
    """Return the file the listing of ``urls`` is stored in, one file per set of sources."""
    key = hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest()[:_KEY_LENGTH]
    return spt_release_cache_dir() / f"{key}.json"


def _entry(release: SptRelease) -> Json:
    """Describe one release the way it is stored."""
    return {
        "version": release.version,
        "client_build": release.client_build,
        "published_at": release.published_at.isoformat(),
        "url": release.url,
        "download_url": release.download_url,
        "archive_name": release.archive_name,
        "content_length": release.content_length,
    }


def _release(item: Json) -> SptRelease | None:
    """Read back one stored release, or return None when the entry is unusable."""
    if not isinstance(item, dict):
        return None
    version = item.get("version")
    published_at = _time(item.get("published_at"))
    if not isinstance(version, str) or not version or published_at is None:
        return None
    return SptRelease(
        version=version,
        client_build=_text(item.get("client_build")),
        published_at=published_at,
        url=_text(item.get("url")),
        download_url=_text(item.get("download_url")),
        archive_name=_text(item.get("archive_name")),
        content_length=_size(item.get("content_length")),
    )


def _time(value: Json) -> datetime | None:
    """Return the moment ``value`` names, or None when it names none."""
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _text(value: Json) -> str:
    """Return ``value`` when it is text, the empty string otherwise."""
    return value if isinstance(value, str) else ""


def _size(value: Json) -> int | None:
    """Return ``value`` when it is a whole number of bytes, None otherwise."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None
