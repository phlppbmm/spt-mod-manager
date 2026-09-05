"""Tests for Catalog.__init__ failure handling: no leaked database handles."""

import os
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from sptmod.core.forge.catalog import Catalog
from test_forge_catalog_fixtures import (
    ClientFactory,
    client_factory,
    fake_clock,
    mod_json_full,
    owner_json,
    page_envelope,
    recording_sleep,
    respx_router,
)

__all__ = [
    "client_factory",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]

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
    """Catalog.__init__ rejects invalid database files with sqlite3.DatabaseError."""
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
    """When Catalog.__init__ fails, the database file is not left open."""
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


def test_init_upgrades_a_database_without_first_seen_at_reads_it_as_none(
    tmp_path: Path,
) -> None:
    """A row cached before ``first_seen_at`` existed has no way to know when it
    was first cached, so the migrated column reads back as None rather than a
    guessed timestamp."""
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
        assert mod.first_seen_at is None


def test_legacy_mod_without_first_seen_at_keeps_it_none_after_a_later_sync(
    tmp_path: Path, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """A pre-migration row has no first-seen stamp to preserve; re-syncing the
    same mod id must not backfill one, since the real first-seen moment is
    unknowable for rows the cache already held before the column existed."""
    old_db = tmp_path / "old-forge-catalog.db"
    with sqlite3.connect(old_db) as connection:
        connection.executescript(_PRE_DESCRIPTION_SCHEMA)
        connection.execute(
            "INSERT INTO mods (id, name, slug, author, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (1, "Alpha Mod", "alpha-mod", "Alice", "2025-01-01T00:00:00Z"),
        )
    connection.close()

    with Catalog(old_db) as catalog:
        migrated = catalog.get_mod(1)
        assert migrated is not None
        assert migrated.first_seen_at is None

        updated_mod = mod_json_full(
            id=1,
            name="Alpha Mod Renamed",
            slug="alpha-mod",
            owner=owner_json(id=10, name="Alice"),
            updated_at="2025-07-01T00:00:00Z",
            versions=[],
        )
        respx_router.get("/mods").mock(
            return_value=httpx.Response(
                200,
                json=page_envelope(
                    [updated_mod], current_page=1, last_page=1, total=1, next_url=None
                ),
            )
        )
        client = client_factory()

        catalog.sync(client, full=True)

        synced = catalog.get_mod(1)
        assert synced is not None
        assert synced.name == "Alpha Mod Renamed"
        assert synced.first_seen_at is None


_PRE_FIKA_VERSIONS_SCHEMA = """
CREATE TABLE versions (
    id INTEGER PRIMARY KEY,
    mod_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    link TEXT,
    content_length INTEGER,
    spt_version_constraint TEXT NOT NULL DEFAULT '',
    downloads INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    updated_at TEXT
);
"""


def test_init_upgrades_a_versions_table_without_fika_compatibility_reads_none(
    tmp_path: Path,
) -> None:
    """A database created before the fika_compatibility column was added to
    the versions table migrates silently on open, and existing version rows
    read back with fika_compatibility=None rather than raising a schema error.
    """
    old_db = tmp_path / "old-forge-catalog.db"
    with sqlite3.connect(old_db) as connection:
        connection.executescript(_PRE_FIKA_VERSIONS_SCHEMA)
        connection.execute(
            "INSERT INTO versions (id, mod_id, version, spt_version_constraint)"
            " VALUES (?, ?, ?, ?)",
            (900, 1, "1.0.0", "^3.8.0"),
        )
    connection.close()

    with Catalog(old_db) as catalog:
        versions = catalog.get_versions(1)
        assert len(versions) == 1
        assert versions[0].fika_compatibility is None
