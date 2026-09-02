"""Streaming download of mod archives into a destination directory."""

import os
import tempfile
from collections.abc import Mapping
from email.message import Message
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import urlparse

from sptmod.core.errors import ArchiveError
from sptmod.core.forge.client import ForgeClient
from sptmod.core.models import Progress

DOWNLOAD_STEP: Final = "download"
FALLBACK_FILE_NAME: Final = "download"
TEMP_PREFIX: Final = ".sptmod-"
TEMP_SUFFIX: Final = ".part"


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
            total = _total_bytes(response.headers)
            name = _file_name(url, response.headers.get("Content-Disposition"))
            downloaded = 0
            for chunk in response.chunks:
                temp_file.write(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    progress(
                        DOWNLOAD_STEP,
                        _percent(downloaded, total),
                        f"{name}: {downloaded} bytes",
                    )
        if expected_length is not None and downloaded != expected_length:
            raise ArchiveError(
                f"{name} has {downloaded} bytes, expected {expected_length}"
            )
        target = dest_dir / name
        temp_path.replace(target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return target
