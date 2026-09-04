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


_PRE_DESCRIPTION_SCHEMA = """
CREATE TABLE mods (
    id INTEGER PRIMARY KEY,
    hub_id INTEGER,
    guid TEXT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    teaser TEXT,
    author TEXT NOT NULL DEFAULT '',
    downloads INTEGER NOT NULL DEFAULT 0,
    detail_url TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
"""


def test_init_upgrades_a_database_created_without_the_newer_columns(
    tmp_path: Path,
) -> None:
    """A cache file written by an older release keeps its rows and gains the
    columns added since, including the stored description."""
    old_db = tmp_path / "old-forge-catalog.db"
    with sqlite3.connect(old_db) as connection:
        connection.executescript(_PRE_DESCRIPTION_SCHEMA)
        connection.execute(
            "INSERT INTO mods (id, name, slug, author, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (7, "Old Mod", "old-mod", "Author", "2025-06-01T00:00:00Z"),
        )
    connection.close()

    with Catalog(old_db) as catalog:
        mod = catalog.get_mod(7)
        assert mod is not None
        assert mod.name == "Old Mod"
        assert mod.source_links == ()
        assert catalog.get_description(7) is None

        catalog.store_description(7, "Plain text.", mod.updated_at)

        stored = catalog.get_description(7)
        assert stored is not None
        assert stored.text == "Plain text."
        assert stored.updated_at == "2025-06-01T00:00:00Z"
