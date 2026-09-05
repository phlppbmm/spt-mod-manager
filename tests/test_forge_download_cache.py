"""Tests for forge/download.py's ``download_cached``: cache hit/miss, resume,
interruption, integrity, and LRU-triggering behavior driven over real HTTP
mocked with respx.
"""

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from fixture_types import ClientFactory
from sptmod.core.errors import ArchiveError, ForgeUnavailableError
from sptmod.core.forge import cache
from sptmod.core.forge.client import DEFAULT_CHUNK_SIZE
from sptmod.core.forge.download import CACHE_HIT_MESSAGE, DOWNLOAD_STEP, download_cached

DOWNLOAD_URL = "https://cdn.example.com/releases/download/v1/mod-archive.zip"
FORBIDDEN_WORDS = ("git", "commit", "repository", "revert", "cherry")


class RecordingProgress:
    """A ``Progress`` callback stand-in recording every call as a tuple."""

    def __init__(self) -> None:
        """Initialize with empty calls list."""
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, step: str, percent: int, message: str) -> None:
        """Append (step, percent, message) to calls log for test assertion.

        Each invocation by download_cached() is recorded, preserving the order and exact
        values (step name, percentage int, status message string).
        """
        self.calls.append((step, percent, message))


class _RaisingAfterFirstChunk(httpx.SyncByteStream):
    """A byte stream that delivers one real chunk, then fails.

    httpx's ``iter_bytes`` buffers raw reads until it has a full chunk to
    hand to the caller, so the first raw yield must be at least as large as
    the client's chunk size for any bytes to actually reach the sink before
    the failure -- otherwise nothing would be written and the interruption
    would look like a zero-byte download.
    """

    def __init__(self, first_chunk: bytes) -> None:
        self._first_chunk = first_chunk

    def __iter__(self) -> Iterator[bytes]:
        yield self._first_chunk
        raise httpx.ReadError("connection dropped mid-stream")


# --- cache hit vs miss ---------------------------------------------------


def test_first_call_downloads_and_second_call_hits_the_cache(client_factory: ClientFactory, cache_dir: Path) -> None:
    """download_cached() fetches on first call, cache-hits on second call with same expected_length.

    First call downloads body to cache_dir/777/mod-archive.zip (1 HTTP request).
    Second call with the same key="777" and expected_length finds the cached file and skips
    HTTP entirely (0 new requests, total still 1); returns the same path and updates the
    entry's mtime to "now".
    """
    body = b"archive-bytes-for-hit-test"
    with respx.mock() as router:
        route = router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
        )
        client = client_factory()

        first_progress = RecordingProgress()
        first_result = download_cached(
            client,
            DOWNLOAD_URL,
            key="777",
            progress=first_progress,
            expected_length=len(body),
            cache_dir=cache_dir,
        )

        assert route.call_count == 1
        assert first_result == cache_dir / "777" / "mod-archive.zip"
        assert first_result.read_bytes() == body

        entry = cache_dir / "777"
        old_time = time.time() - 100_000
        os.utime(entry, (old_time, old_time))

        second_progress = RecordingProgress()
        second_result = download_cached(
            client,
            DOWNLOAD_URL,
            key="777",
            progress=second_progress,
            expected_length=len(body),
            cache_dir=cache_dir,
        )

    assert route.call_count == 1  # no second HTTP request
    assert second_result == first_result
    assert second_progress.calls == [(DOWNLOAD_STEP, 100, CACHE_HIT_MESSAGE)]
    assert entry.stat().st_mtime > old_time + 1000


# --- resume ----------------------------------------------------------------


def test_resume_continues_from_the_leftover_offset_on_a_206_response(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """Resumes partial download when server sends 206 Partial Content with matching Content-Range."""
    total = 70_000
    full_payload = bytes(i % 251 for i in range(total))
    offset = 30_000
    (cache_dir / "42.part").write_bytes(full_payload[:offset])
    (cache_dir / "42.part.json").write_text(
        json.dumps(
            {
                "name": "mod-archive.zip",
                "expected_length": total,
                "url": DOWNLOAD_URL,
            }
        ),
        encoding="utf-8",
    )
    with respx.mock(assert_all_called=False) as router:
        range_route = router.get(DOWNLOAD_URL, headers={"range": f"bytes={offset}-"}).mock(
            return_value=httpx.Response(
                206,
                content=full_payload[offset:],
                headers={
                    "Content-Range": f"bytes {offset}-{total - 1}/{total}",
                },
            )
        )
        plain_route = router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=full_payload))
        client = client_factory()
        progress = RecordingProgress()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key="42",
            progress=progress,
            expected_length=total,
            cache_dir=cache_dir,
        )

    assert range_route.call_count == 1
    assert plain_route.call_count == 0
    assert result.read_bytes() == full_payload

    percents = [percent for _step, percent, _message in progress.calls]
    starting_share = round(offset * 100 / total)
    assert percents[0] >= starting_share
    assert all(p >= starting_share for p in percents)
    assert percents == sorted(percents)
    assert percents[-1] == 100


def test_a_200_answer_to_a_range_request_restarts_the_download_from_zero(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """download_cached() restarts from offset 0 when server ignores Range and sends 200.

    A leftover part file (43.part with 10 bytes of garbage) exists; download_cached sends
    Range: bytes=10- but the server responds 200 OK (ignoring the range), providing the
    full payload from offset 0. The function detects this (200 instead of 206), discards
    the leftover, and writes the full payload (40 bytes) to the final file.
    """
    total = 40
    full_payload = bytes(range(total))
    offset = 10
    # Deliberately corrupt leftover bytes: proves the final file comes from
    # a fresh download, not from stitching the (wrong) leftover prefix.
    (cache_dir / "43.part").write_bytes(b"\xff" * offset)
    (cache_dir / "43.part.json").write_text(
        json.dumps({"name": "mod-archive.zip", "expected_length": total, "url": DOWNLOAD_URL}),
        encoding="utf-8",
    )
    with respx.mock() as router:
        router.get(DOWNLOAD_URL, headers={"range": f"bytes={offset}-"}).mock(
            return_value=httpx.Response(200, content=full_payload, headers={"Content-Length": str(total)})
        )
        client = client_factory()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key="43",
            expected_length=total,
            cache_dir=cache_dir,
        )

    assert result.read_bytes() == full_payload


@pytest.mark.parametrize(
    ("key", "range_status", "range_headers"),
    [
        # Reports a different total than the recorded expected_length.
        ("44", 206, {"Content-Range": "bytes 10-19/999"}),
        ("54", 416, {}),
        # A 206 answer that omits Content-Range entirely cannot be trusted.
        ("45", 206, {}),
        # Content-Range starts somewhere other than the requested offset.
        ("46", 206, {"Content-Range": "bytes 5-19/40"}),
        # Content-Range whose start segment is not a number.
        ("47", 206, {"Content-Range": "bytes abc-19/40"}),
    ],
    ids=[
        "206-with-mismatched-content-range-total",
        "416",
        "206-missing-content-range",
        "206-wrong-start-offset",
        "206-malformed-content-range",
    ],
)
def test_an_untrustworthy_answer_to_a_resume_restarts_the_download_from_zero(
    client_factory: ClientFactory,
    cache_dir: Path,
    key: str,
    range_status: int,
    range_headers: dict[str, str],
) -> None:
    """Resume fails validation (mismatched total, malformed header, wrong offset) and restarts."""
    total = 40
    full_payload = bytes(range(total))
    offset = 10
    (cache_dir / f"{key}.part").write_bytes(full_payload[:offset])
    (cache_dir / f"{key}.part.json").write_text(
        json.dumps({"name": "mod-archive.zip", "expected_length": total, "url": DOWNLOAD_URL}),
        encoding="utf-8",
    )
    with respx.mock() as router:
        range_route = router.get(DOWNLOAD_URL, headers={"range": f"bytes={offset}-"}).mock(
            return_value=httpx.Response(
                range_status,
                content=b"irrelevant-mismatched-body",
                headers=range_headers,
            )
        )
        plain_route = router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(200, content=full_payload, headers={"Content-Length": str(total)})
        )
        client = client_factory()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key=key,
            expected_length=total,
            cache_dir=cache_dir,
        )

    assert range_route.call_count == 1
    assert plain_route.call_count == 1
    assert result.read_bytes() == full_payload


# --- interruption ------------------------------------------------------


def test_interruption_mid_stream_leaves_a_resumable_part_and_reraises(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """Stream interruption saves partial download and re-raises; subsequent call resumes."""
    total = DEFAULT_CHUNK_SIZE + 5_000
    full_payload = bytes(i % 251 for i in range(total))
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).respond(200, stream=_RaisingAfterFirstChunk(full_payload))
        client = client_factory()

        with pytest.raises(ForgeUnavailableError):
            download_cached(
                client,
                DOWNLOAD_URL,
                key="45",
                expected_length=total,
                cache_dir=cache_dir,
            )

    leftover = cache_dir / "45.part"
    sidecar = cache_dir / "45.part.json"
    assert leftover.read_bytes() == full_payload[:DEFAULT_CHUNK_SIZE]
    assert json.loads(sidecar.read_text(encoding="utf-8"))["expected_length"] == total

    # A subsequent call resumes exactly where the interrupted one stopped.
    with respx.mock() as router:
        router.get(DOWNLOAD_URL, headers={"range": f"bytes={DEFAULT_CHUNK_SIZE}-"}).mock(
            return_value=httpx.Response(
                206,
                content=full_payload[DEFAULT_CHUNK_SIZE:],
                headers={
                    "Content-Range": (f"bytes {DEFAULT_CHUNK_SIZE}-{total - 1}/{total}"),
                },
            )
        )
        client = client_factory()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key="45",
            expected_length=total,
            cache_dir=cache_dir,
        )

    assert result.read_bytes() == full_payload


# --- integrity -----------------------------------------------------------


def test_a_size_mismatched_cache_entry_is_deleted_and_downloaded_exactly_once(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """download_cached() deletes stale cache entry and downloads fresh when size mismatches.

    A cached file (key="46", 24 bytes of stale-wrong-size-content) exists, but expected_length
    points to a different size. cache.find() returns None (size mismatch), download_cached
    fetches fresh (1 HTTP call), and stores correct_body (18 bytes). The stale entry is removed,
    and the new content is saved.
    """
    entry = cache_dir / "46"
    entry.mkdir()
    (entry / "mod-archive.zip").write_bytes(b"stale-wrong-size-content")
    (entry / "meta.json").write_text(
        json.dumps(
            {
                "name": "mod-archive.zip",
                "size": len(b"stale-wrong-size-content"),
                "url": DOWNLOAD_URL,
            }
        ),
        encoding="utf-8",
    )
    correct_body = b"correct-fresh-body"
    with respx.mock() as router:
        route = router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=correct_body))
        client = client_factory()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key="46",
            expected_length=len(correct_body),
            cache_dir=cache_dir,
        )

    assert route.call_count == 1
    assert result.read_bytes() == correct_body


def test_a_fresh_download_size_mismatch_raises_and_leaves_no_complete_entry(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """download_cached() raises ArchiveError on size mismatch and leaves no complete cache entry.

    A fresh download of 25 bytes with expected_length=26 fails the post-download size check.
    ArchiveError is raised, the partial file is deleted, and no cache entry directory (47) is
    created, so cache.find() returns None.
    """
    body = b"actual-body-of-eighteen-b"
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()

        with pytest.raises(ArchiveError):
            download_cached(
                client,
                DOWNLOAD_URL,
                key="47",
                expected_length=len(body) + 1,
                cache_dir=cache_dir,
            )

    assert cache.find("47", expected_length=len(body) + 1, cache_dir=cache_dir) is None
    assert not (cache_dir / "47").exists()


def test_expected_length_none_reuses_the_cached_file_without_any_http_call(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """download_cached() with expected_length=None reuses cache without HTTP or size check.

    A cached entry (key="48") exists with a file. When expected_length=None, cache.find() returns
    the path regardless of actual vs. recorded size, and download_cached returns that path
    immediately. No HTTP request is made (respx asserts all mocked routes are used); no size
    validation occurs.
    """
    entry = cache_dir / "48"
    entry.mkdir()
    (entry / "mod-archive.zip").write_bytes(b"whatever-size-content-here")
    (entry / "meta.json").write_text(
        json.dumps(
            {
                "name": "mod-archive.zip",
                "size": len(b"whatever-size-content-here"),
                "url": DOWNLOAD_URL,
            }
        ),
        encoding="utf-8",
    )
    with respx.mock(assert_all_mocked=True) as router:
        # No route registered: any HTTP attempt raises via respx itself.
        del router
        client = client_factory()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key="48",
            expected_length=None,
            cache_dir=cache_dir,
        )

    assert result.read_bytes() == b"whatever-size-content-here"


# --- publish failure -----------------------------------------------------


def test_a_failed_publish_rename_raises_and_leaves_a_resumable_part(
    client_factory: ClientFactory, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """download_cached() raises ArchiveError on publish (final rename) failure but preserves .part.

    A download completes successfully, but os.replace() to finalize the file (move from
    temp to archive name) fails with OSError("disk full"). The function raises ArchiveError,
    but the partial file (53.part) and metadata (53.part.json) remain, allowing a subsequent
    call to resume from that offset.
    """
    body = b"body-for-publish-failure-test"
    real_replace = os.replace

    def failing_replace(
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        # Only the final publish rename (to the plain archive name) fails;
        # the ".part"/".part.json" renames the resume machinery performs
        # must keep working, or the interrupted download couldn't recover.
        if ".part" not in Path(dst).suffixes:
            raise OSError("disk full")
        real_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    with respx.mock() as router:
        route = router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()
        monkeypatch.setattr(os, "replace", failing_replace)

        with pytest.raises(ArchiveError):
            download_cached(
                client,
                DOWNLOAD_URL,
                key="53",
                expected_length=len(body),
                cache_dir=cache_dir,
            )

    assert route.call_count == 1
    leftover = cache_dir / "53.part"
    sidecar = cache_dir / "53.part.json"
    assert leftover.read_bytes() == body
    assert json.loads(sidecar.read_text(encoding="utf-8"))["expected_length"] == len(body)
    assert cache.find("53", expected_length=len(body), cache_dir=cache_dir) is None


# --- resume rejected with 416 ---------------------------------------------


def test_a_fresh_download_answered_with_416_raises_forge_unavailable(
    client_factory: ClientFactory, cache_dir: Path
) -> None:
    """download_cached() raises ForgeUnavailableError when fresh download receives 416.

    No resumable .part exists (fresh download), and the server responds 416 Range Not
    Satisfiable to the initial GET. stream() raises ForgeUnavailableError, no cache entry
    is created, and the directory remains empty.
    """
    with respx.mock() as router:
        route = router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(416))
        client = client_factory()

        with pytest.raises(ForgeUnavailableError):
            download_cached(
                client,
                DOWNLOAD_URL,
                key="55",
                expected_length=100,
                cache_dir=cache_dir,
            )

    assert route.call_count == 1
    assert not (cache_dir / "55.part").exists()
    assert cache.find("55", expected_length=100, cache_dir=cache_dir) is None


# --- LRU bound triggered by a completed download ----------------------


def test_a_completed_download_triggers_a_sweep_that_evicts_older_entries(
    client_factory: ClientFactory, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """download_cached() triggers cache sweep after successful download to enforce size cap.

    An old entry (old-key, 5000 bytes, mtime 100k seconds ago) and a new download (new-key,
    100 bytes) exist. max_bytes is capped at len(new_body) + 68 bytes. After the new download
    completes, sweep() runs and evicts the oldest entry (old-key) to stay under the cap,
    leaving only new-key in the cache.
    """
    old_entry = cache_dir / "old-key"
    old_entry.mkdir()
    (old_entry / "old.zip").write_bytes(b"O" * 5_000)
    (old_entry / "meta.json").write_text(
        json.dumps({"name": "old.zip", "size": 5_000, "url": "http://old"}),
        encoding="utf-8",
    )
    old_time = time.time() - 100_000
    os.utime(old_entry, (old_time, old_time))

    new_body = b"N" * 100
    monkeypatch.setattr(cache, "max_bytes", lambda: len(new_body) + 68)

    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=new_body))
        client = client_factory()

        result = download_cached(
            client,
            DOWNLOAD_URL,
            key="new-key",
            expected_length=len(new_body),
            cache_dir=cache_dir,
        )

    assert result.read_bytes() == new_body
    assert not old_entry.exists()


# --- progress message vocabulary ----------------------------------------


def test_progress_messages_never_mention_git_vocabulary(client_factory: ClientFactory, cache_dir: Path) -> None:
    """Progress messages use user-facing language, not git/commit/revert terminology."""
    body = b"body-for-vocabulary-test"
    all_messages: list[str] = []
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=body))
        client = client_factory()
        fresh_progress = RecordingProgress()

        download_cached(
            client,
            DOWNLOAD_URL,
            key="49",
            progress=fresh_progress,
            expected_length=len(body),
            cache_dir=cache_dir,
        )
        all_messages += [message for _s, _p, message in fresh_progress.calls]

        hit_progress = RecordingProgress()
        download_cached(
            client,
            DOWNLOAD_URL,
            key="49",
            progress=hit_progress,
            expected_length=len(body),
            cache_dir=cache_dir,
        )
        all_messages += [message for _s, _p, message in hit_progress.calls]

    assert all_messages
    for message in all_messages:
        lowered = message.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in lowered
