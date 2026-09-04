"""Tests for forge/download.py: streaming download with progress and size checks."""

from pathlib import Path

import httpx
import pytest
import respx

from sptmod.core.errors import ArchiveError, ForgeUnavailableError
from sptmod.core.forge.download import FALLBACK_FILE_NAME, download
from test_forge_client_fixtures import client_factory, fake_clock, recording_sleep

__all__ = ["client_factory", "fake_clock", "recording_sleep"]

DOWNLOAD_URL = "https://cdn.example.com/releases/download/v1/mod-archive.zip"


class RecordingProgress:
    """A ``Progress`` callback stand-in recording every call as a tuple."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, step: str, percent: int, message: str) -> None:
        self.calls.append((step, percent, message))


@pytest.fixture
def dest_dir(tmp_path: Path) -> Path:
    target = tmp_path / "dest"
    target.mkdir()
    return target


def test_download_progress_is_monotonic_and_reaches_100_with_known_length(
    client_factory, dest_dir: Path
) -> None:
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

        download(
            client, DOWNLOAD_URL, dest_dir, progress=progress, expected_length=None
        )

    percents = [percent for _step, percent, _message in progress.calls]
    assert len(percents) > 1
    assert percents == sorted(percents)
    assert all(0 <= p <= 100 for p in percents)
    assert percents[-1] == 100


def test_download_progress_stays_at_zero_while_total_size_is_unknown(
    client_factory, dest_dir: Path
) -> None:
    body = b"y" * 200_000
    with respx.mock() as router:
        # A streamed (iterator) body carries no Content-Length header, unlike
        # a plain bytes body which httpx would auto-size: the total size is
        # genuinely unknown to the client.
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(200, content=iter([body]))
        )
        client = client_factory()
        progress = RecordingProgress()

        download(
            client, DOWNLOAD_URL, dest_dir, progress=progress, expected_length=None
        )

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
    client_factory,
    dest_dir: Path,
    content_disposition: str | None,
    url: str,
    expected_name: str,
) -> None:
    body = b"archive-bytes"
    headers = (
        {}
        if content_disposition is None
        else {"Content-Disposition": content_disposition}
    )
    with respx.mock() as router:
        router.get(url).mock(
            return_value=httpx.Response(200, content=body, headers=headers)
        )
        client = client_factory()

        result = download(client, url, dest_dir)

    assert result == dest_dir / expected_name
    assert result.read_bytes() == body
    assert list(dest_dir.iterdir()) == [result]


def test_download_size_match_succeeds_and_returns_final_path(
    client_factory, dest_dir: Path
) -> None:
    body = b"exact-size-content"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        result = download(client, DOWNLOAD_URL, dest_dir, expected_length=len(body))

    assert result.read_bytes() == body
    assert list(dest_dir.iterdir()) == [result]


def test_download_size_mismatch_raises_archive_error_and_removes_temp_file(
    client_factory, dest_dir: Path
) -> None:
    body = b"exact-size-content"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        with pytest.raises(ArchiveError):
            download(client, DOWNLOAD_URL, dest_dir, expected_length=len(body) + 1)

    assert list(dest_dir.iterdir()) == []


def test_download_expected_length_none_skips_the_size_check(
    client_factory, dest_dir: Path
) -> None:
    body = b"a-body-of-777-declared-content-length"
    with respx.mock() as router:
        # Content-Length header lies about the body size; only expected_length
        # (which is None here) gates the size check, so this must not raise.
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(
                200, content=body, headers={"Content-Length": "999999"}
            )
        )
        client = client_factory()

        result = download(client, DOWNLOAD_URL, dest_dir, expected_length=None)

    assert result.read_bytes() == body


def test_download_follows_a_redirect_to_the_final_url(
    client_factory, dest_dir: Path
) -> None:
    """Forge download links can 30x-redirect to a CDN URL; the client's
    ``httpx.Client`` must follow it (``follow_redirects=True``), not treat
    the redirect itself as the (unusable) response.
    """
    body = b"redirected-archive-bytes"
    redirect_target = "https://cdn.example.com/releases/download/v1/final.zip"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(307, headers={"Location": redirect_target})
        )
        router.get(redirect_target).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        result = download(client, DOWNLOAD_URL, dest_dir)

    assert result.read_bytes() == body


def test_download_propagates_forge_unavailable_error_and_leaves_no_temp_file(
    client_factory, dest_dir: Path
) -> None:
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(side_effect=httpx.ConnectError("boom"))
        client = client_factory()

        with pytest.raises(ForgeUnavailableError):
            download(client, DOWNLOAD_URL, dest_dir)

    assert list(dest_dir.iterdir()) == []
