"""Tests for Catalog.__init__ failure handling: no leaked database handles."""

import os
import sqlite3
from pathlib import Path

import pytest

from sptmod.core.forge.catalog import Catalog

_FD_DIR = Path("/proc/self/fd")


def _open_handles_on(path: Path) -> int:
    """Count this process' open file descriptors pointing at ``path``."""
    count = 0
    for entry in _FD_DIR.iterdir():
        try:
            if os.readlink(entry) == str(path):
                count += 1
        except OSError:
            # The descriptor was closed while we were listing; not ours.
            continue
    return count


def test_init_on_a_non_sqlite_file_raises_database_error(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a sqlite database, just plain junk padding" * 5)

    with pytest.raises(sqlite3.DatabaseError):
        Catalog(garbage)


@pytest.mark.skipif(
    not _FD_DIR.is_dir(), reason="needs /proc to observe open file descriptors"
)
def test_init_failure_leaves_no_open_handle_on_the_database_file(
    tmp_path: Path,
) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a sqlite database, just plain junk padding" * 5)

    with pytest.raises(sqlite3.DatabaseError):
        Catalog(garbage)

    assert _open_handles_on(garbage) == 0
