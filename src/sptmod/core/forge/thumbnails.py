"""Persistent cache for the mod thumbnails shown next to a catalog entry.

The image host answers every request that does not carry the Forge's own
origin as ``Referer`` with a refusal, and a browser view can never send that
header for an ``<img>`` element. The images are therefore fetched here, with
the header set, and handed on as data URLs.

Layout below the cache directory, keyed by the digest of the image URL::

    <key>                the image bytes
    <key>.json           {"url", "media_type"}
    <key>.<token>.part   an image one process is currently writing

The cache is disposable: every read, write or clean-up failure degrades to
"no thumbnail" instead of failing the view it belongs to.
"""

import base64
import contextlib
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from sptmod.core import workspace
from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.forge.client import ForgeClient, forge_base_url
from sptmod.core.models import Json

MAX_IMAGE_BYTES: Final = 512 * 1024
MAX_CACHE_BYTES: Final = 128 * 1024**2

_IMAGE_MEDIA_TYPES: Final = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"}
)

_META_SUFFIX: Final = ".json"
_PART_SUFFIX: Final = ".part"


def load(
    urls: Mapping[int, str],
    *,
    client: ForgeClient,
    cache_dir: Path | None = None,
) -> dict[int, str | None]:
    """Return the thumbnail of every entry of ``urls`` as a data URL.

    Cached images are read from disk; the rest is fetched once and kept for
    the next run. The least recently used entries are dropped when the cache
    grew past :data:`MAX_CACHE_BYTES`, but only after something was written.

    Args:
        urls: The image URL of each mod, keyed by whatever the caller counts
            the images by.
        client: The Forge client the images are fetched with.
        cache_dir: Directory the images are kept in; defaults to
            :func:`sptmod.core.workspace.thumbnail_cache_dir`.

    Returns:
        The data URL of each key of ``urls``, or None where the image is
        unreadable, too large or the host refused to hand it out.
    """
    directory = _directory(cache_dir)
    referer = _referer()
    loaded: dict[int, str | None] = {}
    written = False
    for mod_id, url in urls.items():
        key = _key(url)
        cached = _cached(directory, key, url)
        if cached is not None:
            loaded[mod_id] = cached
            continue
        fetched = _fetch(client, url, referer)
        if fetched is None:
            loaded[mod_id] = None
            continue
        media_type, payload = fetched
        written = _store(directory, key, url, media_type, payload) or written
        loaded[mod_id] = _encode(media_type, payload)
    if written:
        _sweep(directory)
    return loaded


def clear(cache_dir: Path | None = None) -> None:
    """Remove every cached thumbnail, which is fetched again when it is shown."""
    for child in _children(_directory(cache_dir)):
        _remove(child)


def _directory(cache_dir: Path | None) -> Path:
    return workspace.thumbnail_cache_dir() if cache_dir is None else cache_dir


def _referer() -> str:
    """Return the origin the image host expects, taken from the Forge base URL."""
    parts = urlsplit(forge_base_url())
    return f"{parts.scheme}://{parts.netloc}/"


def _key(url: str) -> str:
    """Return the file name an image is cached under.

    The whole URL is part of the digest, so a mod whose image moves misses the
    cache instead of being shown the previous picture.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _encode(media_type: str, payload: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _cached(directory: Path, key: str, url: str) -> str | None:
    """Return the stored image for ``url``, or None when there is none."""
    meta = _read_meta(directory / f"{key}{_META_SUFFIX}")
    media_type = meta.get("media_type")
    if meta.get("url") != url or not isinstance(media_type, str):
        return None
    image = directory / key
    try:
        payload = image.read_bytes()
    except OSError:
        return None
    with contextlib.suppress(OSError):
        os.utime(image)
    return _encode(media_type, payload)


def _fetch(client: ForgeClient, url: str, referer: str) -> tuple[str, bytes] | None:
    """Download the image at ``url``, or None when it cannot be shown."""
    try:
        with client.stream(url, headers={"Referer": referer}) as stream:
            media_type = _media_type(stream.headers.get("Content-Type"))
            payload = None if media_type is None else _bounded(stream.chunks)
    except ForgeUnavailableError:
        return None
    if media_type is None or payload is None:
        return None
    return media_type, payload


def _media_type(content_type: str | None) -> str | None:
    """Return the media type of a Content-Type header, if it names a picture.

    Only the raster formats a browser draws are accepted, so nothing the host
    declares can reach the view as markup it would run.
    """
    if content_type is None:
        return None
    media_type = content_type.split(";")[0].strip().lower()
    return media_type if media_type in _IMAGE_MEDIA_TYPES else None


def _bounded(chunks: Iterator[bytes]) -> bytes | None:
    """Read at most :data:`MAX_IMAGE_BYTES`, or None when the image is larger."""
    payload = bytearray()
    for chunk in chunks:
        payload += chunk
        if len(payload) > MAX_IMAGE_BYTES:
            return None
    return bytes(payload)


def _store(
    directory: Path, key: str, url: str, media_type: str, payload: bytes
) -> bool:
    """Keep an image for the next run, reporting whether it was written.

    The image is written under a name unique to this process and moved into
    place, so two processes fetching the same picture never write into the same
    file and a reader never sees half a picture; the description naming it is
    written afterwards, hence a torn write is a miss and not a broken image.
    """
    token = f"{os.getpid()}-{secrets.token_hex(4)}"
    work = directory / f"{key}.{token}{_PART_SUFFIX}"
    try:
        work.write_bytes(payload)
        os.replace(work, directory / key)
        (directory / f"{key}{_META_SUFFIX}").write_text(
            json.dumps({"url": url, "media_type": media_type}), encoding="utf-8"
        )
    except OSError:
        with contextlib.suppress(OSError):
            work.unlink(missing_ok=True)
        return False
    return True


def _read_meta(path: Path) -> dict[str, Json]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        content: Json = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return content if isinstance(content, dict) else {}


def _children(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except OSError:
        return []


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _sweep(directory: Path) -> None:
    """Drop the least recently used images until the cache fits its limit."""
    images: list[tuple[float, int, Path]] = []
    total = 0
    for child in _children(directory):
        if child.name.endswith((_META_SUFFIX, _PART_SUFFIX)):
            continue
        try:
            status = child.stat()
        except OSError:
            continue
        if not stat.S_ISREG(status.st_mode):
            continue
        images.append((status.st_mtime, status.st_size, child))
        total += status.st_size
    for _used, size, image in sorted(images):
        if total <= MAX_CACHE_BYTES:
            return
        _remove(image)
        _remove(image.with_name(f"{image.name}{_META_SUFFIX}"))
        total -= size
