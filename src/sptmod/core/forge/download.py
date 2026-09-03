"""Streaming download of mod archives into a destination directory."""

import os
import tempfile
from collections.abc import Mapping
from email.message import Message
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final
from urllib.parse import urlparse

from sptmod.core.errors import ArchiveError
from sptmod.core.forge import cache
from sptmod.core.forge.client import RANGE_NOT_SATISFIABLE, DownloadStream, ForgeClient
from sptmod.core.models import Progress

DOWNLOAD_STEP: Final = "download"
FALLBACK_FILE_NAME: Final = "download"
TEMP_PREFIX: Final = ".sptmod-"
TEMP_SUFFIX: Final = ".part"
CACHE_HIT_MESSAGE: Final = "Using the downloaded file"

_PARTIAL_CONTENT: Final = 206


def _plain_name(candidate: str | None) -> str | None:
    """Reduce a candidate file name to a plain name inside the target directory."""
    if not candidate:
        return None
    name = PurePosixPath(candidate.replace("\\", "/")).name
    if name in ("", ".", ".."):
        return None
    return name


def _file_name(url: str, content_disposition: str | None) -> str:
    """Derive the file name from Content-Disposition header or URL path.

    Tries the header first, then the URL's last path component, falling
    back to FALLBACK_FILE_NAME if neither yields a valid name. Sanitizes
    path separators and traversal segments via _plain_name.
    """
    header = Message()
    if content_disposition is not None:
        header["Content-Disposition"] = content_disposition
    from_header = _plain_name(header.get_filename())
    if from_header is not None:
        return from_header
    from_url = _plain_name(urlparse(url).path)
    return FALLBACK_FILE_NAME if from_url is None else from_url


def _total_bytes(headers: Mapping[str, str]) -> int | None:
    declared = headers.get("Content-Length")
    if declared is None:
        return None
    try:
        total = int(declared)
    except ValueError:
        return None
    return total if total > 0 else None


def _percent(downloaded: int, total: int | None) -> int:
    if total is None:
        return 0
    return min(100, round(downloaded * 100 / total))


def _range_total(header: str | None, offset: int) -> int | None:
    """Return the full size a ``Content-Range`` reports for a resumed download.

    Yields None when the header is missing, unreadable, of unknown size or
    does not continue exactly where the partial file ends.
    """
    if header is None:
        return None
    span, _, size = header.removeprefix("bytes").strip().partition("/")
    start, _, _end = span.partition("-")
    try:
        if int(start) != offset:
            return None
        return int(size)
    except ValueError:
        return None


def _pump(
    response: DownloadStream,
    sink: BinaryIO,
    url: str,
    progress: Progress | None,
    downloaded: int,
    total: int | None,
) -> tuple[str, int]:
    """Write the arriving chunks into ``sink``, reporting how far they got.

    Returns:
        The file name the response stands for and the number of bytes the
        file holds once the body is through, continuing at ``downloaded``.
    """
    name = _file_name(url, response.headers.get("Content-Disposition"))
    for chunk in response.chunks:
        sink.write(chunk)
        downloaded += len(chunk)
        if progress is not None:
            progress(
                DOWNLOAD_STEP,
                _percent(downloaded, total),
                f"{name}: {downloaded} bytes",
            )
    return name, downloaded


def _verify(name: str, downloaded: int, expected_length: int | None) -> None:
    if expected_length is not None and downloaded != expected_length:
        raise ArchiveError(f"{name} has {downloaded} bytes, expected {expected_length}")


def download(
    client: ForgeClient,
    url: str,
    dest_dir: Path,
    progress: Progress | None = None,
    expected_length: int | None = None,
) -> Path:
    """Download an archive to ``dest_dir``, streaming through a temp file.

    The response is streamed chunk by chunk into a temporary file inside
    ``dest_dir`` and only renamed to its final name once the download is
    complete and verified, so a failed download never leaves a usable-looking
    archive behind.

    Args:
        client: The Forge client whose HTTP machinery performs the streaming.
        url: The direct download URL; Forge links point at external hosts and
            need no authentication.
        dest_dir: The directory to place the archive in; it must exist.
        progress: Optional callback invoked while bytes arrive, with
            monotonically growing percentages between 0 and 100. The percentage
            stays at 0 while the total size is unknown.
        expected_length: The size in bytes the finished file must have, or None
            to skip the size check. Forge reports ``content_length`` as NULL for
            some versions, hence the check is optional.

    Returns:
        The path of the downloaded archive inside ``dest_dir``. Its name comes
        from the ``Content-Disposition`` header when the server sends a usable
        one and from the last path segment of the URL otherwise; path
        separators and traversal segments are stripped either way.

    Raises:
        ForgeUnavailableError: The URL is unreachable or answered with an HTTP
            error status.
        ArchiveError: The downloaded size differs from ``expected_length``; the
            temporary file is removed before raising.
    """
    descriptor, temp_name = tempfile.mkstemp(
        dir=dest_dir, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as temp_file, client.stream(url) as response:
            name, downloaded = _pump(
                response,
                temp_file,
                url,
                progress,
                0,
                _total_bytes(response.headers),
            )
        _verify(name, downloaded, expected_length)
        target = dest_dir / name
        temp_path.replace(target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return target


def download_cached(
    client: ForgeClient,
    url: str,
    key: str,
    *,
    progress: Progress | None = None,
    expected_length: int | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Download an archive once and keep it for later runs.

    An archive already in the cache is reported as complete and returned
    without touching the network. Otherwise the download continues where an
    interrupted earlier attempt stopped, if the server supports it, and the
    finished file joins the cache; the oldest archives make room for it when
    the cache outgrows its size limit.

    Args:
        client: The Forge client whose HTTP machinery performs the streaming.
        url: The direct download URL.
        key: The identity of the archive in the cache, the Forge version id.
        progress: Optional callback invoked while bytes arrive, with
            monotonically growing percentages between 0 and 100 that count a
            continued download from where it stopped.
        expected_length: The size in bytes the finished file must have, or
            None to skip the size check.
        cache_dir: The cache to use; defaults to the user's download cache.

    Returns:
        The path of the archive inside the cache.

    Raises:
        ForgeUnavailableError: The URL is unreachable or answered with an HTTP
            error status.
        ArchiveError: The downloaded size differs from ``expected_length``.
    """
    cached = cache.find(key, expected_length, cache_dir)
    if cached is not None:
        if progress is not None:
            progress(DOWNLOAD_STEP, 100, CACHE_HIT_MESSAGE)
        return cached
    state = cache.claim(key, expected_length, url, cache_dir)
    try:
        name, downloaded = _fetch(client, url, state, progress, expected_length)
        _verify(name, downloaded, expected_length)
        archive = cache.keep(state, name, key, cache_dir)
    except BaseException:
        cache.release(state, key, cache_dir)
        raise
    cache.sweep(key, cache_dir)
    return archive


def _fetch(
    client: ForgeClient,
    url: str,
    state: cache.PartState,
    progress: Progress | None,
    expected_length: int | None,
) -> tuple[str, int]:
    """Fill the work file of ``state`` with the archive behind ``url``."""
    if state.offset > 0:
        expected = (
            expected_length if expected_length is not None else (state.expected_length)
        )
        continued = _continue(client, url, state, progress, expected)
        if continued is not None:
            return continued
    with client.stream(url) as response, state.work.open("wb") as sink:
        return _pump(response, sink, url, progress, 0, _total_bytes(response.headers))


def _continue(
    client: ForgeClient,
    url: str,
    state: cache.PartState,
    progress: Progress | None,
    expected: int | None,
) -> tuple[str, int] | None:
    """Ask for the rest of an interrupted download and take what comes.

    Returns:
        The file name and size once the body is through, or None when the
        server answered with a part that does not fit the file on disk or
        rejected the asked-for range, and the download has to start over.
    """
    headers = {"Range": f"bytes={state.offset}-"}
    with client.stream(url, headers=headers) as response:
        if response.status_code == RANGE_NOT_SATISFIABLE:
            return None
        if response.status_code != _PARTIAL_CONTENT:
            with state.work.open("wb") as sink:
                return _pump(
                    response, sink, url, progress, 0, _total_bytes(response.headers)
                )
        total = _range_total(response.headers.get("Content-Range"), state.offset)
        if total is None or (expected is not None and total != expected):
            return None
        with state.work.open("r+b") as sink:
            sink.seek(state.offset)
            sink.truncate(state.offset)
            return _pump(response, sink, url, progress, state.offset, total)
