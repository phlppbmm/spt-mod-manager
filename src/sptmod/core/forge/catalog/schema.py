"""The catalog SQL schema, its migrations and the dataclasses it mirrors.

The ``mods`` table in :data:`SCHEMA` and :class:`CatalogMod` are one column per
field: they stay in the same module so the mirror can be read in one place.
:data:`MOD_COLUMN_MIGRATIONS` is the only schema evolution mechanism -- the
cache is disposable, so columns added after the first release are applied with
ALTER TABLE when an existing database is opened.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import platformdirs

from sptmod.core.errors import SptmodError
from sptmod.core.forge.client import FikaCompatibility

APP_NAME: Final = "sptmod"
DB_FILE_NAME: Final = "forge-catalog.db"
WATERMARK_KEY: Final = "mods_updated_at"
DATA_VERSION_KEY: Final = "data_version"
# Bump when a sync starts caching NEW per-mod data (e.g. added columns):
# caches synced under an older data version force their next sync to run
# full, so existing rows pick the new fields up without a manual --full.
DATA_VERSION: Final = "2"
SYNC_OVERLAP_DAYS: Final = 1
SYNC_PAGE_STEP: Final = "page"

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS mods (
    id INTEGER PRIMARY KEY,
    hub_id INTEGER,
    guid TEXT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    teaser TEXT,
    thumbnail TEXT,
    author TEXT NOT NULL DEFAULT '',
    downloads INTEGER NOT NULL DEFAULT 0,
    detail_url TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    favourites_count INTEGER NOT NULL DEFAULT 0,
    endorsements_count INTEGER NOT NULL DEFAULT 0,
    fika_compatibility INTEGER,
    featured INTEGER NOT NULL DEFAULT 0,
    contains_ads INTEGER NOT NULL DEFAULT 0,
    contains_ai_content INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    license_name TEXT,
    license_link TEXT,
    source_links TEXT NOT NULL DEFAULT '[]',
    description TEXT,
    description_updated_at TEXT,
    first_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS mods_name_idx ON mods (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS mods_author_idx ON mods (author COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS mods_updated_at_idx ON mods (updated_at);
"""

# Columns added after the first release; applied via ALTER TABLE on open so
# existing caches keep working (the cache is disposable, this is the only
# schema evolution mechanism).
MOD_COLUMN_MIGRATIONS: Final[tuple[str, ...]] = (
    "thumbnail TEXT",
    "favourites_count INTEGER NOT NULL DEFAULT 0",
    "endorsements_count INTEGER NOT NULL DEFAULT 0",
    "fika_compatibility INTEGER",
    "featured INTEGER NOT NULL DEFAULT 0",
    "contains_ads INTEGER NOT NULL DEFAULT 0",
    "contains_ai_content INTEGER NOT NULL DEFAULT 0",
    "category TEXT",
    "license_name TEXT",
    "license_link TEXT",
    "source_links TEXT NOT NULL DEFAULT '[]'",
    "description TEXT",
    "description_updated_at TEXT",
    "first_seen_at TEXT",
)

VERSIONS_SCHEMA: Final = """

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    mod_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    link TEXT,
    content_length INTEGER,
    spt_version_constraint TEXT NOT NULL DEFAULT '',
    downloads INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    updated_at TEXT,
    fika_compatibility TEXT
);
CREATE INDEX IF NOT EXISTS versions_mod_id_idx ON versions (mod_id);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

VERSION_COLUMN_MIGRATIONS: Final[tuple[str, ...]] = ("fika_compatibility TEXT",)

MOD_COLUMNS: Final = (
    "id, hub_id, guid, name, slug, teaser, thumbnail, author, downloads,"
    " detail_url, published_at, created_at, updated_at, favourites_count,"
    " endorsements_count, fika_compatibility, featured, contains_ads,"
    " contains_ai_content, category, license_name, license_link, source_links,"
    " first_seen_at"
)
VERSION_COLUMNS: Final = (
    "id, mod_id, version, link, content_length, spt_version_constraint,"
    " downloads, published_at, updated_at, fika_compatibility"
)
DESCRIPTION_COLUMNS: Final = "description, description_updated_at"

type CatalogState = Literal["installed", "update_available", "available"]

CATALOG_STATES: Final[tuple[CatalogState, ...]] = (
    "installed",
    "update_available",
    "available",
)

type UpsertOutcome = Literal["new", "updated", "unchanged"]


class NoCompatibleVersionError(SptmodError):
    """No cached version of the mod satisfies the requested constraints."""


@dataclass(frozen=True)
class CatalogMod:
    """A mod as cached in the local catalog.

    ``author`` is the Forge owner name; additional authors are not cached.
    ``updated_at`` is the raw ISO-8601 timestamp from the API and drives the
    delta sync watermark.
    """

    id: int
    hub_id: int | None
    guid: str | None
    name: str
    slug: str
    teaser: str | None
    thumbnail: str | None
    author: str
    downloads: int
    detail_url: str
    published_at: str | None
    created_at: str | None
    updated_at: str | None
    favourites_count: int = 0
    endorsements_count: int = 0
    fika_compatibility: bool | None = None
    featured: bool = False
    contains_ads: bool = False
    contains_ai_content: bool = False
    category: str | None = None
    license_name: str | None = None
    license_link: str | None = None
    source_links: tuple["CatalogSourceLink", ...] = ()
    first_seen_at: str | None = None


@dataclass(frozen=True)
class CatalogSourceLink:
    """A labeled source-code link of a catalog mod."""

    url: str
    label: str


@dataclass(frozen=True)
class CatalogVersion:
    """A mod version as cached in the local catalog.

    ``link``, ``content_length`` and ``fika_compatibility`` are None for
    versions that only ever arrived through the ``include=versions`` summary of
    /mods; they are filled once :meth:`Catalog.fetch_versions` loads the full
    version list.
    """

    id: int
    mod_id: int
    version: str
    link: str | None
    content_length: int | None
    spt_version_constraint: str
    downloads: int
    published_at: str | None
    updated_at: str | None
    fika_compatibility: FikaCompatibility | None = None


@dataclass(frozen=True)
class CachedDescription:
    """A stored mod description and the mod state it was fetched for.

    ``updated_at`` is the ``updated_at`` the mod row carried when the
    description was stored; a differing mod ``updated_at`` means the stored
    text is outdated.
    """

    text: str
    updated_at: str | None


@dataclass(frozen=True)
class SyncResult:
    """Counts of what a :meth:`Catalog.sync` run changed."""

    new_mods: int
    updated_mods: int
    new_versions: int
    updated_versions: int
    pages: int
    full: bool


@dataclass(frozen=True)
class CatalogEntry:
    """A cached mod joined with the installed state of the game repository."""

    mod: CatalogMod
    state: CatalogState
    installed_version: str | None
    latest_version: CatalogVersion | None


def default_db_path() -> Path:
    """Return the catalog database path in the user's config directory.

    Never points into the game directory: the cache is foreign state and must
    stay out of the tracked repository.
    """
    return platformdirs.user_config_path(APP_NAME, appauthor=False) / DB_FILE_NAME
