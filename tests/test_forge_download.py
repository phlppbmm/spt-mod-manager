"""Tests for forge/download.py: streaming download with progress and size checks."""

from pathlib import Path

import httpx
import pytest
import respx

from sptmod.core.errors import ArchiveError, ForgeUnavailableError
from sptmod.core.forge.download import FALLBACK_FILE_NAME, download
from test_forge_client_fixtures import (
    ClientFactory,
    client_factory,
    fake_clock,
    recording_sleep,
)

__all__ = ["client_factory", "fake_clock", "recording_sleep"]

DOWNLOAD_URL = "https://cdn.example.com/releases/download/v1/mod-archive.zip"


class RecordingProgress:
    """A ``Progress`` callback stand-in recording every call as a tuple."""

    def __init__(self) -> None:
        """Initialize with empty calls list."""
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, step: str, percent: int, message: str) -> None:
        """Append (step, percent, message) to calls log for test assertion.

        Each invocation by download() is recorded, preserving the order and exact
        values (step name, percentage int, status message string).
        """
        self.calls.append((step, percent, message))


@pytest.fixture
def dest_dir(tmp_path: Path) -> Path:
    """Empty temporary directory handed to every test as the download destination."""
    target = tmp_path / "dest"
    target.mkdir()
    return target


def test_download_progress_is_monotonic_and_reaches_100_with_known_length(
    client_factory: ClientFactory, dest_dir: Path
) -> None:
    """download() reports progress percentages monotonically increasing to exactly 100%.

    A 300 KB response with Content-Length header triggers multiple progress calls
    (> 1 call, since chunks are ~64 KB); each percent is between 0–100, strictly
    increasing (percents == sorted(percents)), and the final call is exactly 100.
    """
    body = b"x" * 300_000  # several 64 KiB chunks at the client's default chunk size
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"Content-Length": str(len(body))},
            )
        )
        client = client_factory()
        progress = RecordingProgress()

        download(client, DOWNLOAD_URL, dest_dir, progress=progress, expected_length=None)

    percents = [percent for _step, percent, _message in progress.calls]
    assert len(percents) > 1
    assert percents == sorted(percents)
    assert all(0 <= p <= 100 for p in percents)
    assert percents[-1] == 100


def test_download_progress_stays_at_zero_while_total_size_is_unknown(
    client_factory: ClientFactory, dest_dir: Path
) -> None:
    """Progress remains at 0% throughout when response lacks Content-Length header."""
    body = b"y" * 200_000
    with respx.mock() as router:
        # A streamed (iterator) body carries no Content-Length header, unlike
        # a plain bytes body which httpx would auto-size: the total size is
        # genuinely unknown to the client.
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=iter([body])))
        client = client_factory()
        progress = RecordingProgress()

        download(client, DOWNLOAD_URL, dest_dir, progress=progress, expected_length=None)

    percents = [percent for _step, percent, _message in progress.calls]
    assert percents
    assert all(p == 0 for p in percents)


def test_download_progress_stays_at_zero_when_content_length_is_malformed(
    client_factory: ClientFactory, dest_dir: Path
) -> None:
    """download() reports 0% progress when Content-Length header is unparseable.

    A 200 response with Content-Length: "not-a-number" fails to parse as an int;
    the progress callback receives percent=0 for all calls, since size is unknown.
    """
    body = b"z" * 200_000
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(200, content=body, headers={"Content-Length": "not-a-number"})
        )
        client = client_factory()
        progress = RecordingProgress()

        download(client, DOWNLOAD_URL, dest_dir, progress=progress, expected_length=None)

    percents = [percent for _step, percent, _message in progress.calls]
    assert percents
    assert all(p == 0 for p in percents)


@pytest.mark.parametrize(
    ("content_disposition", "url", "expected_name"),
    [
        (
            'attachment; filename="cool-mod-v2.0.zip"',
            DOWNLOAD_URL,
            "cool-mod-v2.0.zip",
        ),
        (None, DOWNLOAD_URL, "mod-archive.zip"),
        (
            'attachment; filename="../../../../etc/passwd.zip"',
            DOWNLOAD_URL,
            "passwd.zip",
        ),
        # ".." sanitizes to nothing, and a bare-root URL path ("/") has no
        # trailing segment either: both candidate sources are unusable, so
        # the module's own fallback name must be used.
        (
            'attachment; filename=".."',
            "https://cdn.example.com/",
            FALLBACK_FILE_NAME,
        ),
    ],
    ids=[
        "quoted-content-disposition",
        "url-path-fallback",
        "sanitized-traversal",
        "module-default-name",
    ],
)
def test_download_derives_the_file_name_from_header_url_or_fallback(
    client_factory: ClientFactory,
    dest_dir: Path,
    content_disposition: str | None,
    url: str,
    expected_name: str,
) -> None:
    """File name derives from Content-Disposition header, URL path, or fallback in that order."""
    body = b"archive-bytes"
    headers = {} if content_disposition is None else {"Content-Disposition": content_disposition}
    with respx.mock() as router:
        router.get(url).mock(return_value=httpx.Response(200, content=body, headers=headers))
        client = client_factory()

        result = download(client, url, dest_dir)

    assert result == dest_dir / expected_name
    assert result.read_bytes() == body
    assert list(dest_dir.iterdir()) == [result]


def test_download_size_match_succeeds_and_returns_final_path(client_factory: ClientFactory, dest_dir: Path) -> None:
    """download() returns final file path when downloaded size matches expected_length.

    A 200 response with body=b"exact-size-content" (18 bytes) and expected_length=18
    passes the size check, saves to dest_dir/[filename], and returns the final path.
    The destination directory contains only that one file.
    """
    body = b"exact-size-content"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        result = download(client, DOWNLOAD_URL, dest_dir, expected_length=len(body))

    assert result.read_bytes() == body
    assert list(dest_dir.iterdir()) == [result]


def test_download_size_mismatch_raises_archive_error_and_removes_temp_file(
    client_factory: ClientFactory, dest_dir: Path
) -> None:
    """download() raises ArchiveError and deletes temp file when size doesn't match.

    A 200 response with body (18 bytes) but expected_length=19 triggers a size check
    failure after the full download completes. ArchiveError is raised, and the temporary
    file is immediately deleted; dest_dir remains empty.
    """
    body = b"exact-size-content"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        with pytest.raises(ArchiveError):
            download(client, DOWNLOAD_URL, dest_dir, expected_length=len(body) + 1)

    assert list(dest_dir.iterdir()) == []


def test_download_expected_length_none_skips_the_size_check(client_factory: ClientFactory, dest_dir: Path) -> None:
    """When expected_length is None, size validation is bypassed even if Content-Length differs."""
    body = b"a-body-of-777-declared-content-length"
    with respx.mock() as router:
        # Content-Length header lies about the body size; only expected_length
        # (which is None here) gates the size check, so this must not raise.
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(200, content=body, headers={"Content-Length": "999999"})
        )
        client = client_factory()

        result = download(client, DOWNLOAD_URL, dest_dir, expected_length=None)

    assert result.read_bytes() == body


def test_download_follows_a_redirect_to_the_final_url(client_factory: ClientFactory, dest_dir: Path) -> None:
    """Forge download links can 30x-redirect to a CDN URL; the client's
    ``httpx.Client`` must follow it (``follow_redirects=True``), not treat
    the redirect itself as the (unusable) response.
    """
    body = b"redirected-archive-bytes"
    redirect_target = "https://cdn.example.com/releases/download/v1/final.zip"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(307, headers={"Location": redirect_target}))
        router.get(redirect_target).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        result = download(client, DOWNLOAD_URL, dest_dir)

    assert result.read_bytes() == body


def test_download_propagates_forge_unavailable_error_and_leaves_no_temp_file(
    client_factory: ClientFactory, dest_dir: Path
) -> None:
    """download() propagates ForgeUnavailableError and cleans up on connection failure.

    When httpx.ConnectError("boom") is raised (network unreachable), stream() wraps it
    in ForgeUnavailableError and re-raises it. The context manager ensures the temp file
    (if created) is deleted, leaving dest_dir empty.
    """
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(side_effect=httpx.ConnectError("boom"))
        client = client_factory()

        with pytest.raises(ForgeUnavailableError):
            download(client, DOWNLOAD_URL, dest_dir)

    assert list(dest_dir.iterdir()) == []
