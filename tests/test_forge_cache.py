"""Tests for forge/cache.py: the persistent, disposable download cache.

Exercises the on-disk protocol the module's own docstring documents
(``<key>/meta.json``, ``<key>/<name>``, ``<key>.part``, ``<key>.part.json``,
``<key>.<token>.part``, ``<key>.<token>.part.json``) directly through the
public functions (``max_bytes``, ``find``, ``claim``, ``keep``, ``release``,
``sweep``, ``clear``), never through private helpers.
"""

import json
import os
import time
from pathlib import Path

import pytest

from sptmod.core.errors import ArchiveError
from sptmod.core.forge import cache
from sptmod.core.forge.cache import (
    DEFAULT_MAX_BYTES,
    MAX_GB_ENV_VAR,
    PART_MAX_AGE_SECONDS,
)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "cache"
    directory.mkdir()
    return directory


def _write_entry(
    cache_dir: Path,
    key: str,
    name: str,
    content: bytes,
    *,
    url: str = "http://example.invalid/x.zip",
) -> Path:
    """Build a complete cache entry directly on disk, bypassing ``keep``."""
    entry = cache_dir / key
    entry.mkdir(parents=True)
    (entry / name).write_bytes(content)
    (entry / "meta.json").write_text(
        json.dumps({"name": name, "size": len(content), "url": url}),
        encoding="utf-8",
    )
    return entry / name


def _write_leftover_part(
    cache_dir: Path,
    key: str,
    content: bytes,
    *,
    name: str | None = "foo.zip",
    expected_length: int | None = None,
    url: str = "http://example.invalid/x.zip",
) -> Path:
    """Leave an unclaimed partial download behind, as a killed process would."""
    part = cache_dir / f"{key}.part"
    part.write_bytes(content)
    (cache_dir / f"{key}.part.json").write_text(
        json.dumps({"name": name, "expected_length": expected_length, "url": url}),
        encoding="utf-8",
    )
    return part


def _sidecar_of(work: Path) -> Path:
    return work.with_name(f"{work.name}.json")


def _dir_size(path: Path) -> int:
    """Sum the on-disk byte size of every file below ``path``, recursively."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


# --- max_bytes -------------------------------------------------------------


def test_max_bytes_default_without_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MAX_GB_ENV_VAR, raising=False)

    assert cache.max_bytes() == DEFAULT_MAX_BYTES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", 5 * 1024**3), ("1.5", int(1.5 * 1024**3))],
    ids=["integer", "fractional"],
)
def test_max_bytes_reads_a_valid_gb_value(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv(MAX_GB_ENV_VAR, raw)

    assert cache.max_bytes() == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-number",
        "0",
        "-1",
        "-3.5",
        "inf",
        "-inf",
        "nan",
        "1e-30",
        "",
    ],
    ids=[
        "garbage",
        "zero",
        "neg",
        "neg-frac",
        "inf",
        "neg-inf",
        "nan",
        "underflows-to-zero-bytes",
        "empty",
    ],
)
def test_max_bytes_falls_back_to_default_for_garbage_or_non_positive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(MAX_GB_ENV_VAR, raw)

    assert cache.max_bytes() == DEFAULT_MAX_BYTES


# --- find --------------------------------------------------------------


def test_find_returns_none_when_there_is_no_entry(cache_dir: Path) -> None:
    assert cache.find("missing-key", cache_dir=cache_dir) is None


def test_find_returns_the_archive_path_when_present(cache_dir: Path) -> None:
    archive = _write_entry(cache_dir, "k1", "mod.zip", b"archive-bytes")

    assert cache.find("k1", len(b"archive-bytes"), cache_dir) == archive


def test_find_removes_a_size_mismatched_entry_and_reports_it_missing(
    cache_dir: Path,
) -> None:
    _write_entry(cache_dir, "k2", "mod.zip", b"archive-bytes")

    result = cache.find("k2", expected_length=999, cache_dir=cache_dir)

    assert result is None
    assert not (cache_dir / "k2").exists()


def test_find_reuses_the_cached_file_as_is_when_expected_length_is_none(
    cache_dir: Path,
) -> None:
    archive = _write_entry(cache_dir, "k3", "mod.zip", b"any-size-content")

    result = cache.find("k3", expected_length=None, cache_dir=cache_dir)

    assert result == archive
    assert archive.exists()


def test_find_returns_none_for_invalid_json_meta_without_raising(
    cache_dir: Path,
) -> None:
    entry = cache_dir / "k4"
    entry.mkdir()
    (entry / "mod.zip").write_bytes(b"content")
    (entry / "meta.json").write_text("{not valid json", encoding="utf-8")

    assert cache.find("k4", cache_dir=cache_dir) is None


def test_find_returns_none_when_meta_json_is_unreadable_without_raising(
    cache_dir: Path,
) -> None:
    entry = cache_dir / "k5"
    entry.mkdir()
    (entry / "mod.zip").write_bytes(b"content")
    # meta.json is itself a directory: reading it as text raises OSError.
    (entry / "meta.json").mkdir()

    assert cache.find("k5", cache_dir=cache_dir) is None


def test_find_returns_none_when_meta_name_escapes_its_entry_directory(
    cache_dir: Path,
) -> None:
    entry = cache_dir / "k6"
    entry.mkdir()
    (entry / "meta.json").write_text(
        json.dumps({"name": "../evil.zip", "size": 1, "url": "http://x"}),
        encoding="utf-8",
    )

    assert cache.find("k6", cache_dir=cache_dir) is None


def test_find_returns_none_when_meta_names_a_file_that_does_not_exist(
    cache_dir: Path,
) -> None:
    entry = cache_dir / "k7"
    entry.mkdir()
    (entry / "meta.json").write_text(
        json.dumps({"name": "mod.zip", "size": 1, "url": "http://x"}),
        encoding="utf-8",
    )

    assert cache.find("k7", cache_dir=cache_dir) is None


def test_find_returns_none_when_the_named_archive_is_a_directory_not_a_file(
    cache_dir: Path,
) -> None:
    """The metadata can outlive the file it names turning into something else
    entirely (e.g. a directory left behind by an unrelated process); find()
    reports it missing instead of raising, and leaves the entry untouched.
    """
    state = cache.claim("k22", None, "http://x", cache_dir)
    state.work.write_bytes(b"archive-bytes")
    archive = cache.keep(state, "mod.zip", "k22", cache_dir)
    archive.unlink()
    archive.mkdir()

    assert cache.find("k22", cache_dir=cache_dir) is None
    assert (cache_dir / "k22" / "meta.json").exists()


def test_find_refreshes_the_entrys_last_use_time_on_a_hit(cache_dir: Path) -> None:
    _write_entry(cache_dir, "k8", "mod.zip", b"content")
    entry = cache_dir / "k8"
    old_time = time.time() - 100_000
    os.utime(entry, (old_time, old_time))

    cache.find("k8", cache_dir=cache_dir)

    assert entry.stat().st_mtime > old_time + 1000


# --- claim ---------------------------------------------------------------


def test_claim_with_no_leftover_starts_fresh_at_offset_zero(
    cache_dir: Path,
) -> None:
    state = cache.claim("k9", 100, "http://new", cache_dir)

    assert state.offset == 0
    assert state.name is None
    assert state.expected_length == 100
    assert state.url == "http://new"
    assert state.work.parent == cache_dir
    assert not state.work.exists()
    assert not (cache_dir / "k9.part").exists()


def test_claim_takes_over_a_leftover_part_matching_expected_length(
    cache_dir: Path,
) -> None:
    content = b"0123456789"
    _write_leftover_part(cache_dir, "k10", content, name="foo.zip", expected_length=500)

    state = cache.claim("k10", 500, "http://new", cache_dir)

    assert state.offset == len(content)
    assert state.name == "foo.zip"
    assert state.expected_length == 500
    assert state.url == "http://new"
    assert state.work.read_bytes() == content
    assert not (cache_dir / "k10.part").exists()
    assert not (cache_dir / "k10.part.json").exists()


def test_claim_discards_a_zero_byte_leftover_part(cache_dir: Path) -> None:
    _write_leftover_part(cache_dir, "k11", b"", name="foo.zip", expected_length=None)

    state = cache.claim("k11", None, "http://new", cache_dir)

    assert state.offset == 0
    assert state.name is None
    assert not state.work.exists()


def test_claim_discards_a_leftover_part_recorded_for_a_different_size(
    cache_dir: Path,
) -> None:
    _write_leftover_part(
        cache_dir, "k12", b"abcdefghij", name="foo.zip", expected_length=500
    )

    state = cache.claim("k12", 999, "http://new", cache_dir)

    assert state.offset == 0
    assert state.name is None
    assert not state.work.exists()


def test_two_claims_on_one_leftover_part_only_one_gets_the_offset(
    cache_dir: Path,
) -> None:
    content = b"partial-bytes-1234567890"
    _write_leftover_part(
        cache_dir, "k13", content, name="foo.zip", expected_length=len(content) + 50
    )

    first = cache.claim("k13", len(content) + 50, "http://x", cache_dir)
    second = cache.claim("k13", len(content) + 50, "http://x", cache_dir)

    assert first.offset == len(content)
    assert first.name == "foo.zip"
    assert first.work.read_bytes() == content
    assert second.offset == 0
    assert second.name is None
    assert first.work != second.work
    assert not second.work.exists()


# --- keep ------------------------------------------------------------------


def test_keep_publishes_the_archive_and_writes_meta(cache_dir: Path) -> None:
    state = cache.claim("k14", None, "http://x", cache_dir)
    state.work.write_bytes(b"full-archive-content")

    archive = cache.keep(state, "mod.zip", "k14", cache_dir)

    assert archive == cache_dir / "k14" / "mod.zip"
    assert archive.read_bytes() == b"full-archive-content"
    meta = json.loads((cache_dir / "k14" / "meta.json").read_text(encoding="utf-8"))
    assert meta == {
        "name": "mod.zip",
        "size": len(b"full-archive-content"),
        "url": "http://x",
    }
    assert not state.work.exists()


def test_keep_removes_a_sidecar_left_over_from_a_resumed_claim(
    cache_dir: Path,
) -> None:
    _write_leftover_part(
        cache_dir, "k15", b"partial", name="mod.zip", expected_length=20
    )
    state = cache.claim("k15", 20, "http://orig", cache_dir)
    sidecar = _sidecar_of(state.work)
    assert sidecar.exists()
    state.work.write_bytes(b"partial-plus-the-rest")

    cache.keep(state, "mod.zip", "k15", cache_dir)

    assert not sidecar.exists()


def test_two_keeps_for_the_same_key_leave_one_complete_file_and_no_stray_parts(
    cache_dir: Path,
) -> None:
    full_content = b"same-full-content-for-both-competing-downloads"
    state_a = cache.claim("k16", None, "http://x", cache_dir)
    state_b = cache.claim("k16", None, "http://x", cache_dir)
    state_a.work.write_bytes(full_content)
    state_b.work.write_bytes(full_content)

    archive_a = cache.keep(state_a, "mod.zip", "k16", cache_dir)
    archive_b = cache.keep(state_b, "mod.zip", "k16", cache_dir)

    assert archive_a == archive_b == cache_dir / "k16" / "mod.zip"
    assert archive_b.read_bytes() == full_content
    stray_parts = [p for p in cache_dir.iterdir() if ".part" in p.suffixes]
    assert stray_parts == []
    entry_dir = cache_dir / "k16"
    assert sorted(p.name for p in entry_dir.iterdir()) == ["meta.json", "mod.zip"]


# --- release -----------------------------------------------------------


def test_release_hands_an_interrupted_download_back_for_resume(
    cache_dir: Path,
) -> None:
    state = cache.claim("k17", 100, "http://x", cache_dir)
    state.work.write_bytes(b"partial-data-xyz")

    cache.release(state, "k17", cache_dir)

    leftover = cache_dir / "k17.part"
    sidecar = cache_dir / "k17.part.json"
    assert leftover.read_bytes() == b"partial-data-xyz"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "name": None,
        "expected_length": 100,
        "url": "http://x",
    }
    assert not state.work.exists()

    resumed = cache.claim("k17", 100, "http://x", cache_dir)
    assert resumed.offset == len(b"partial-data-xyz")
    assert resumed.expected_length == 100


def test_release_discards_the_released_copy_when_a_leftover_already_exists(
    cache_dir: Path,
) -> None:
    state_a = cache.claim("k18", None, "http://x", cache_dir)
    state_b = cache.claim("k18", None, "http://x", cache_dir)
    state_a.work.write_bytes(b"AAAA-content-from-a")
    state_b.work.write_bytes(b"BBBB-content-from-b")
    cache.release(state_a, "k18", cache_dir)
    leftover = cache_dir / "k18.part"
    assert leftover.read_bytes() == b"AAAA-content-from-a"

    cache.release(state_b, "k18", cache_dir)

    assert leftover.read_bytes() == b"AAAA-content-from-a"
    assert not state_b.work.exists()


def test_release_discards_a_zero_byte_work_file(cache_dir: Path) -> None:
    state = cache.claim("k19", None, "http://x", cache_dir)

    cache.release(state, "k19", cache_dir)

    assert not (cache_dir / "k19.part").exists()
    assert not (cache_dir / "k19.part.json").exists()


def test_release_drops_the_unfinished_download_when_the_handback_rename_fails(
    cache_dir: Path,
) -> None:
    """A release that cannot rename its work file into ``<key>.part`` gives
    up on keeping it, rather than raising: the next run simply starts fresh.
    """
    state = cache.claim("k21", None, "http://x", cache_dir)
    state.work.write_bytes(b"partial-data-that-cannot-be-handed-back")
    os.chmod(cache_dir, 0o500)  # read+exec, no write: os.replace inside fails
    try:
        cache.release(state, "k21", cache_dir)
    finally:
        os.chmod(cache_dir, 0o755)

    assert not (cache_dir / "k21.part").exists()


# --- sweep: .part aging ------------------------------------------------


def test_sweep_removes_part_files_older_than_the_max_age_and_keeps_fresh_ones(
    cache_dir: Path,
) -> None:
    old_part = cache_dir / "old.part"
    old_part.write_bytes(b"x" * 50)
    old_sidecar = cache_dir / "old.part.json"
    old_sidecar.write_text("{}", encoding="utf-8")
    fresh_part = cache_dir / "fresh.part"
    fresh_part.write_bytes(b"y" * 30)
    old_time = time.time() - PART_MAX_AGE_SECONDS - 100
    os.utime(old_part, (old_time, old_time))
    os.utime(old_sidecar, (old_time, old_time))

    freed = cache.sweep(cache_dir=cache_dir)

    assert freed == 50 + len(b"{}")
    assert not old_part.exists()
    assert not old_sidecar.exists()
    assert fresh_part.exists()


def test_sweep_ignores_a_part_file_whose_stat_fails_instead_of_raising(
    cache_dir: Path,
) -> None:
    """A ``.part`` entry that turns out to be a broken symlink cannot be
    stat'd for its age; sweep leaves it alone rather than crashing.
    """
    ghost = cache_dir / "ghost.part"
    ghost.symlink_to(cache_dir / "does-not-exist")

    freed = cache.sweep(cache_dir=cache_dir)

    assert freed == 0
    assert ghost.is_symlink()


def test_sweep_leaves_a_stale_part_in_place_when_it_cannot_be_removed(
    cache_dir: Path,
) -> None:
    part = _write_leftover_part(cache_dir, "k99", b"stale-data")
    sidecar = _sidecar_of(part)
    old_time = time.time() - PART_MAX_AGE_SECONDS - 100
    os.utime(part, (old_time, old_time))
    os.utime(sidecar, (old_time, old_time))
    os.chmod(cache_dir, 0o500)  # read+exec, no write: unlink inside fails
    try:
        freed = cache.sweep(cache_dir=cache_dir)
    finally:
        os.chmod(cache_dir, 0o755)

    assert freed == 0
    assert part.read_bytes() == b"stale-data"


# --- sweep: LRU bound ----------------------------------------------------


def test_sweep_evicts_oldest_last_use_entries_first_until_under_the_cap(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_entry(cache_dir, "a", "a.zip", b"1" * 30)
    _write_entry(cache_dir, "b", "b.zip", b"2" * 40)
    _write_entry(cache_dir, "c", "c.zip", b"3" * 50)
    a_size = _dir_size(cache_dir / "a")
    b_size = _dir_size(cache_dir / "b")
    c_size = _dir_size(cache_dir / "c")
    now = time.time()
    os.utime(cache_dir / "a", (now - 300, now - 300))
    os.utime(cache_dir / "b", (now - 200, now - 200))
    os.utime(cache_dir / "c", (now - 100, now - 100))
    # Room for exactly the newest entry: both older ones must go.
    monkeypatch.setattr(cache, "max_bytes", lambda: c_size)

    freed = cache.sweep(cache_dir=cache_dir)

    assert freed == a_size + b_size
    assert not (cache_dir / "a").exists()
    assert not (cache_dir / "b").exists()
    assert (cache_dir / "c" / "c.zip").read_bytes() == b"3" * 50


def test_sweep_never_evicts_the_keep_key_however_large_the_cache_is(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_entry(cache_dir, "a", "a.zip", b"1" * 80)
    _write_entry(cache_dir, "b", "b.zip", b"2" * 10)
    _write_entry(cache_dir, "c", "c.zip", b"3" * 10)
    b_size = _dir_size(cache_dir / "b")
    c_size = _dir_size(cache_dir / "c")
    now = time.time()
    os.utime(cache_dir / "a", (now - 300, now - 300))
    os.utime(cache_dir / "b", (now - 200, now - 200))
    os.utime(cache_dir / "c", (now - 100, now - 100))
    # A cap far below even the protected entry alone: b and c must still go.
    monkeypatch.setattr(cache, "max_bytes", lambda: 1)

    freed = cache.sweep(keep_key="a", cache_dir=cache_dir)

    assert freed == b_size + c_size
    assert (cache_dir / "a" / "a.zip").read_bytes() == b"1" * 80
    assert not (cache_dir / "b").exists()
    assert not (cache_dir / "c").exists()


def test_sweep_skips_an_entry_it_cannot_remove_and_still_evicts_the_rest(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_entry(cache_dir, "a", "a.zip", b"1" * 30)
    _write_entry(cache_dir, "b", "b.zip", b"2" * 20)
    b_size = _dir_size(cache_dir / "b")
    now = time.time()
    os.utime(cache_dir / "a", (now - 300, now - 300))
    os.utime(cache_dir / "b", (now - 200, now - 200))
    # 'a' is the oldest (evicted first) but cannot actually be removed.
    os.chmod(cache_dir / "a", 0o000)
    monkeypatch.setattr(cache, "max_bytes", lambda: 0)

    try:
        freed = cache.sweep(cache_dir=cache_dir)
    finally:
        os.chmod(cache_dir / "a", 0o755)

    assert freed == b_size
    assert (cache_dir / "a").exists()
    assert not (cache_dir / "b").exists()


def test_keep_raises_archive_error_naming_the_file_when_the_publish_rename_fails(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = cache.claim("k20", None, "http://x", cache_dir)
    state.work.write_bytes(b"full-archive-content")

    def failing_replace(src: object, dst: object, *a: object, **kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", failing_replace)

    with pytest.raises(ArchiveError) as excinfo:
        cache.keep(state, "mod.zip", "k20", cache_dir)

    message = str(excinfo.value)
    assert "mod.zip" in message
    for word in ("git", "commit", "repository", "revert", "cherry"):
        assert word not in message.lower()
    # The failed publish leaves the unfinished download in place, so a
    # subsequent ``release`` can still hand it back for resume.
    assert state.work.read_bytes() == b"full-archive-content"
    assert not (cache_dir / "k20" / "mod.zip").exists()


# --- clear -----------------------------------------------------------------


def test_clear_removes_everything_and_reports_bytes_freed(cache_dir: Path) -> None:
    entry = cache_dir / "e1"
    entry.mkdir()
    (entry / "archive.zip").write_bytes(b"A" * 40)
    (entry / "meta.json").write_text("{}", encoding="utf-8")
    leftover = cache_dir / "leftover.part"
    leftover.write_bytes(b"B" * 15)

    freed = cache.clear(cache_dir)

    assert freed == 40 + len(b"{}") + 15
    assert list(cache_dir.iterdir()) == []


def test_clear_on_an_empty_cache_frees_nothing(cache_dir: Path) -> None:
    assert cache.clear(cache_dir) == 0


def test_clear_reports_bytes_freed_only_for_entries_it_can_actually_remove(
    cache_dir: Path,
) -> None:
    _write_entry(cache_dir, "a", "a.zip", b"1" * 10)
    _write_entry(cache_dir, "b", "b.zip", b"2" * 20)
    b_size = _dir_size(cache_dir / "b")
    os.chmod(cache_dir / "a", 0o000)

    try:
        freed = cache.clear(cache_dir)
    finally:
        os.chmod(cache_dir / "a", 0o755)

    assert freed == b_size
    assert (cache_dir / "a").exists()
    assert not (cache_dir / "b").exists()


@pytest.mark.parametrize(
    "call",
    [
        lambda cache_dir: cache.sweep(cache_dir=cache_dir),
        lambda cache_dir: cache.clear(cache_dir),
    ],
    ids=["sweep", "clear"],
)
def test_an_unreadable_cache_dir_frees_nothing_instead_of_raising(
    tmp_path: Path, call
) -> None:
    unreadable = tmp_path / "cache"
    unreadable.mkdir()
    (unreadable / "leftover.part").write_bytes(b"data")
    os.chmod(unreadable, 0o000)

    try:
        freed = call(unreadable)
    finally:
        os.chmod(unreadable, 0o755)

    assert freed == 0
