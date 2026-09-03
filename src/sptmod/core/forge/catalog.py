"""Local Forge catalog: SQLite cache with sync, search, resolve and browse."""

import contextlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

import platformdirs

from sptmod.core.errors import SptmodError
from sptmod.core.forge.client import (
    MAX_PER_PAGE,
    ForgeClient,
    ForgeMod,
    ForgeVersion,
    ForgeVersionSummary,
)
from sptmod.core.models import ModInfo, Progress
from sptmod.core.modmeta import UnsupportedRangeError, version_matches

APP_NAME: Final = "sptmod"
DB_FILE_NAME: Final = "forge-catalog.db"
WATERMARK_KEY: Final = "mods_updated_at"
DATA_VERSION_KEY: Final = "data_version"
# Bump when a sync starts caching NEW per-mod data (e.g. added columns):
# caches synced under an older data version force their next sync to run
# full, so existing rows pick the new fields up without a manual --full.
DATA_VERSION: Final = "2"
SYNC_OVERLAP_DAYS: Final = 1

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
    source_links TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS mods_name_idx ON mods (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS mods_author_idx ON mods (author COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS mods_updated_at_idx ON mods (updated_at);
"""  # noqa: E501 - continued below

# Columns added after the first release; applied via ALTER TABLE on open so
# existing caches keep working (the cache is disposable, this is the only
# schema evolution mechanism).
_MOD_COLUMN_MIGRATIONS: Final[tuple[str, ...]] = (
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
)

_VERSIONS_SCHEMA: Final = """

CREATE TABLE IF NOT EXISTS versions (
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
CREATE INDEX IF NOT EXISTS versions_mod_id_idx ON versions (mod_id);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_MOD_COLUMNS: Final = (
    "id, hub_id, guid, name, slug, teaser, thumbnail, author, downloads,"
    " detail_url, published_at, created_at, updated_at, favourites_count,"
    " endorsements_count, fika_compatibility, featured, contains_ads,"
    " contains_ai_content, category, license_name, license_link, source_links"
)
_VERSION_COLUMNS: Final = (
    "id, mod_id, version, link, content_length, spt_version_constraint,"
    " downloads, published_at, updated_at"
)

type CatalogState = Literal["installed", "update_available", "available"]

CATALOG_STATES: Final[tuple[CatalogState, ...]] = (
    "installed",
    "update_available",
    "available",
)

type _UpsertOutcome = Literal["new", "updated", "unchanged"]


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


@dataclass(frozen=True)
class CatalogSourceLink:
    """A labeled source-code link of a catalog mod."""

    url: str
    label: str


@dataclass(frozen=True)
class CatalogVersion:
    """A mod version as cached in the local catalog.

    ``link`` and ``content_length`` are None for versions that only ever
    arrived through the ``include=versions`` summary of /mods; they are filled
    once :meth:`Catalog.fetch_versions` loads the full version list.
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


def is_compatible(spt_version_constraint: str, spt_version: str) -> bool:
    """Return whether ``spt_version`` satisfies the version's SPT constraint.

    Constraints the semver matcher does not understand count as incompatible
    rather than raising, so a single odd catalog entry cannot break a search.
    An empty constraint means the compatibility is unknown and counts as
    incompatible too (old hub imports ship versions without one).
    """
    if not spt_version_constraint.strip():
        return False
    try:
        return version_matches(spt_version, spt_version_constraint)
    except UnsupportedRangeError:
        return False


def is_newer(candidate: str, installed: str) -> bool:
    """Return whether ``candidate`` is a strictly newer version than ``installed``.

    Versions that the semver matcher cannot parse count as not newer.
    """
    try:
        return version_matches(candidate, f">{installed}")
    except UnsupportedRangeError:
        return False


def _mod_from_row(row: sqlite3.Row) -> CatalogMod:
    return CatalogMod(
        id=row["id"],
        hub_id=row["hub_id"],
        guid=row["guid"],
        name=row["name"],
        slug=row["slug"],
        teaser=row["teaser"],
        thumbnail=row["thumbnail"],
        author=row["author"],
        downloads=row["downloads"],
        detail_url=row["detail_url"],
        published_at=row["published_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        favourites_count=row["favourites_count"],
        endorsements_count=row["endorsements_count"],
        fika_compatibility=(
            None
            if row["fika_compatibility"] is None
            else bool(row["fika_compatibility"])
        ),
        featured=bool(row["featured"]),
        contains_ads=bool(row["contains_ads"]),
        contains_ai_content=bool(row["contains_ai_content"]),
        category=row["category"],
        license_name=row["license_name"],
        license_link=row["license_link"],
        source_links=_links_from_json(row["source_links"]),
    )


def _links_from_json(raw: str | None) -> tuple[CatalogSourceLink, ...]:
    """Decode the JSON-encoded source links column, tolerating junk."""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(
        CatalogSourceLink(
            url=str(item.get("url", "")), label=str(item.get("label", ""))
        )
        for item in data
        if isinstance(item, dict) and item.get("url")
    )


def _links_to_json(links: tuple[CatalogSourceLink, ...]) -> str:
    return json.dumps([{"url": link.url, "label": link.label} for link in links])


def _version_from_row(row: sqlite3.Row) -> CatalogVersion:
    return CatalogVersion(
        id=row["id"],
        mod_id=row["mod_id"],
        version=row["version"],
        link=row["link"],
        content_length=row["content_length"],
        spt_version_constraint=row["spt_version_constraint"],
        downloads=row["downloads"],
        published_at=row["published_at"],
        updated_at=row["updated_at"],
    )


def _mod_from_forge(mod: ForgeMod) -> CatalogMod:
    return CatalogMod(
        id=mod.id,
        hub_id=mod.hub_id,
        guid=mod.guid,
        name=mod.name,
        slug=mod.slug,
        teaser=mod.teaser,
        thumbnail=mod.thumbnail,
        author="" if mod.owner is None else mod.owner.name,
        downloads=mod.downloads,
        detail_url=mod.detail_url,
        published_at=mod.published_at,
        created_at=mod.created_at,
        updated_at=mod.updated_at,
        favourites_count=mod.favourites_count,
        endorsements_count=mod.endorsements_count,
        fika_compatibility=mod.fika_compatibility,
        featured=mod.featured,
        contains_ads=mod.contains_ads,
        contains_ai_content=mod.contains_ai_content,
        category=None if mod.category is None else mod.category.title,
        license_name=None if mod.license is None else mod.license.name,
        license_link=None if mod.license is None else mod.license.link,
        source_links=tuple(
            CatalogSourceLink(url=link.url, label=link.label)
            for link in mod.source_code_links
        ),
    )


def _version_from_summary(mod_id: int, summary: ForgeVersionSummary) -> CatalogVersion:
    return CatalogVersion(
        id=summary.id,
        mod_id=mod_id,
        version=summary.version,
        link=None,
        content_length=None,
        spt_version_constraint=summary.spt_version_constraint,
        downloads=summary.downloads,
        published_at=summary.published_at,
        updated_at=None,
    )


def _version_from_forge(mod_id: int, version: ForgeVersion) -> CatalogVersion:
    return CatalogVersion(
        id=version.id,
        mod_id=mod_id,
        version=version.version,
        link=version.link,
        content_length=version.content_length,
        spt_version_constraint=version.spt_version_constraint,
        downloads=version.downloads,
        published_at=version.published_at,
        updated_at=version.updated_at,
    )


def _merge_version(
    existing: CatalogVersion | None, incoming: CatalogVersion
) -> CatalogVersion:
    """Keep cached download details that the incoming payload does not carry.

    The ``include=versions`` summaries of /mods know neither ``link`` nor
    ``content_length`` nor ``updated_at``, so a sync must not wipe what a
    previous :meth:`Catalog.fetch_versions` already stored.
    """
    if existing is None:
        return incoming
    return replace(
        incoming,
        link=existing.link if incoming.link is None else incoming.link,
        content_length=(
            existing.content_length
            if incoming.content_length is None
            else incoming.content_length
        ),
        updated_at=(
            existing.updated_at if incoming.updated_at is None else incoming.updated_at
        ),
    )


def _is_comparable(version: str) -> bool:
    try:
        return version_matches(version, version)
    except UnsupportedRangeError:
        return False


def _highest(versions: Sequence[CatalogVersion]) -> CatalogVersion:
    """Pick the highest of ``versions``, which are ordered newest published first.

    Version strings the semver matcher cannot compare are ignored as long as
    comparable ones exist; if none is comparable, the most recently published
    version wins.
    """
    comparable = [version for version in versions if _is_comparable(version.version)]
    if not comparable:
        return versions[0]
    best = comparable[0]
    for candidate in comparable[1:]:
        if is_newer(candidate.version, best.version):
            best = candidate
    return best


def _delta_filter(watermark: str) -> str | None:
    """Build the ``updated_between`` filter value for a delta sync.

    The filter is date-granular, so the range starts ``SYNC_OVERLAP_DAYS``
    before the watermark day and ends today. Returns None if the watermark
    carries no parsable date.
    """
    try:
        start = date.fromisoformat(watermark[:10]) - timedelta(days=SYNC_OVERLAP_DAYS)
    except ValueError:
        return None
    return f"{start.isoformat()},{datetime.now(UTC).date().isoformat()}"


class Catalog:
    """SQLite-backed cache of the Forge mod catalog.

    The database is opened (and created) on construction. All read operations
    work purely on the cache and keep serving data while the Forge API is
    unreachable; only :meth:`sync` and :meth:`fetch_versions` talk to the API.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Open the catalog database at ``db_path``.

        Args:
            db_path: Path of the SQLite file; defaults to
                :func:`default_db_path`. Missing parent directories are
                created.
        """
        self.db_path = default_db_path() if db_path is None else db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path)
        try:
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            self._db.executescript(_VERSIONS_SCHEMA)
            # Lightweight migration for caches created before the thumbnail
            # column existed; the cache is disposable, so this is the only
            # schema evolution mechanism.
            for column in _MOD_COLUMN_MIGRATIONS:
                with contextlib.suppress(sqlite3.OperationalError):
                    self._db.execute(f"ALTER TABLE mods ADD COLUMN {column}")
            self._db.commit()
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def __enter__(self) -> "Catalog":
        """Context manager entry; returns self."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Context manager exit; closes the database connection."""
        self.close()

    def last_synced_at(self) -> str | None:
        """Return the stored delta-sync watermark, or None if never synced.

        The watermark is the greatest ``updated_at`` seen during the last sync,
        stored verbatim as delivered by the API.
        """
        row = self._db.execute(
            "SELECT value FROM sync_state WHERE key = ?", (WATERMARK_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def sync(
        self,
        client: ForgeClient,
        *,
        full: bool = False,
        progress: Progress | None = None,
    ) -> SyncResult:
        """Refresh the cache from the Forge API.

        A full sync paginates the whole catalog. A delta sync queries
        ``filter[updated_between]`` from the watermark date minus
        ``SYNC_OVERLAP_DAYS`` up to today, sorted by ``-updated_at``; the
        overlap compensates the date granularity of the filter and re-seen rows
        are upserted idempotently. A missing or malformed watermark makes the
        delta sync fall back to the full listing. The ``include=versions``
        summaries embedded in the listing (latest six per mod) are cached
        alongside the mods.

        A completed full listing also prunes the mods (and their versions) it
        did not return; a filtered delta sync never prunes, since mods outside
        its date window are absent but not deleted.

        Args:
            client: The Forge API client to read from.
            full: Force a full sync instead of a delta sync.
            progress: Optional callback invoked once per fetched page.

        Returns:
            The counts of inserted and updated mods and versions.

        Raises:
            ForgeUnavailableError: The API is unreachable; the cache keeps its
                previous contents.
            ForgeApiError: The API answered with an error envelope.
        """
        if self._stored_data_version() != DATA_VERSION:
            # The cache predates newly cached fields; refresh every row once.
            full = True
        watermark = self.last_synced_at()
        updated_between = (
            None if full or watermark is None else _delta_filter(watermark)
        )
        filters = (
            None if updated_between is None else {"updated_between": updated_between}
        )

        counts: dict[_UpsertOutcome, int] = {"new": 0, "updated": 0, "unchanged": 0}
        version_counts: dict[_UpsertOutcome, int] = {
            "new": 0,
            "updated": 0,
            "unchanged": 0,
        }
        pages = 0
        page_number = 1
        seen: set[int] = set()
        try:
            while True:
                page = client.mods(
                    filters=filters,
                    sort="-updated_at",
                    include=["versions", "license", "category", "source_code_links"],
                    page=page_number,
                    per_page=MAX_PER_PAGE,
                )
                pages += 1
                for mod in page.items:
                    seen.add(mod.id)
                    counts[self._upsert_mod(_mod_from_forge(mod))] += 1
                    for summary in mod.versions:
                        outcome = self._upsert_version(
                            _version_from_summary(mod.id, summary)
                        )
                        version_counts[outcome] += 1
                    self._advance_watermark(mod.updated_at)
                if progress is not None:
                    progress(
                        "page",
                        _percent(page.meta.current_page, page.meta.last_page),
                        f"page {page.meta.current_page} of {page.meta.last_page}",
                    )
                if page.meta.current_page >= page.meta.last_page:
                    break
                page_number += 1
            if updated_between is None and seen:
                self._prune(seen)
            self._store_data_version()
        except BaseException:
            self._db.rollback()
            raise
        self._db.commit()

        return SyncResult(
            new_mods=counts["new"],
            updated_mods=counts["updated"],
            new_versions=version_counts["new"],
            updated_versions=version_counts["updated"],
            pages=pages,
            full=updated_between is None,
        )

    def fetch_versions(self, client: ForgeClient, mod_id: int) -> list[CatalogVersion]:
        """Load the full version list of one mod from the API into the cache.

        Only this call fills ``link`` and ``content_length``, so it is the
        prerequisite for downloading. Kept separate from :meth:`sync` to stay
        inside the rate budget.

        Args:
            client: The Forge API client to read from.
            mod_id: The numeric Forge mod ID.

        Returns:
            The cached versions of the mod, newest first.

        Raises:
            ForgeUnavailableError: The API is unreachable.
            ForgeApiError: The API answered with an error envelope.
        """
        try:
            for version in client.iter_mod_versions(mod_id):
                self._upsert_version(_version_from_forge(mod_id, version))
        except BaseException:
            self._db.rollback()
            raise
        self._db.commit()
        return self.get_versions(mod_id)

    def get_mod(self, mod_id: int) -> CatalogMod | None:
        """Return the cached mod with ``mod_id``, or None if it is unknown."""
        row = self._db.execute(
            f"SELECT {_MOD_COLUMNS} FROM mods WHERE id = ?", (mod_id,)
        ).fetchone()
        return None if row is None else _mod_from_row(row)

    def find_mod(self, name: str) -> CatalogMod | None:
        """Return the cached mod whose name matches ``name`` case-insensitively.

        Returns None if no mod matches; on several matches the first by name is
        returned. Matching uses Python's ``casefold``, so it also folds
        non-ASCII letters that SQLite's ASCII-only ``NOCASE`` would miss.
        """
        wanted = name.casefold()
        return next(
            (mod for mod in self._all_mods() if mod.name.casefold() == wanted), None
        )

    def search(
        self, query: str, *, compatible_with: str | None = None
    ) -> list[CatalogMod]:
        """Search the cache by mod name and author.

        Matching happens in Python with ``casefold``: it folds non-ASCII
        letters and treats ``%`` and ``_`` as literal characters, unlike SQL
        ``LIKE``.

        Args:
            query: Substring matched case-insensitively against name and
                author; an empty query matches every cached mod.
            compatible_with: SPT version (e.g. ``3.11.5``); keeps only mods with
                at least one cached version whose ``spt_version_constraint``
                the version satisfies.

        Returns:
            The matching mods ordered by name.
        """
        needle = query.casefold()
        mods = [
            mod
            for mod in self._all_mods()
            if needle in mod.name.casefold() or needle in mod.author.casefold()
        ]
        if compatible_with is None:
            return mods
        return [
            mod
            for mod in mods
            if self.get_versions(mod.id, compatible_with=compatible_with)
        ]

    def get_versions(
        self, mod_id: int, *, compatible_with: str | None = None
    ) -> list[CatalogVersion]:
        """Return the cached versions of a mod, newest first.

        Ordering is by ``published_at`` descending, ties broken by version ID
        descending; version strings are not sorted semantically.

        Args:
            mod_id: The numeric Forge mod ID.
            compatible_with: SPT version filter; keeps only compatible versions.
        """
        rows = self._db.execute(
            f"SELECT {_VERSION_COLUMNS} FROM versions WHERE mod_id = ?"
            " ORDER BY published_at DESC, id DESC",
            (mod_id,),
        ).fetchall()
        versions = [_version_from_row(row) for row in rows]
        if compatible_with is None:
            return versions
        return [
            version
            for version in versions
            if is_compatible(version.spt_version_constraint, compatible_with)
        ]

    def resolve_version(
        self,
        mod_id_or_name: int | str,
        *,
        wanted: str | None = None,
        spt_version: str | None = None,
    ) -> CatalogVersion:
        """Pick the version of a mod to install from the cache.

        Args:
            mod_id_or_name: A numeric Forge mod ID or a mod name matched
                case-insensitively.
            wanted: Exact version string to select; without it the highest
                version wins: semantic order among the version strings the
                semver matcher understands, and the most recently published
                one when none of them is parsable.
            spt_version: SPT version the chosen version must be compatible
                with; without it compatibility is not checked.

        Returns:
            The selected version.

        Raises:
            NoCompatibleVersionError: The mod is unknown to the cache, the
                wanted version does not exist, or no cached version is
                compatible with ``spt_version``.
        """
        mod = (
            self.get_mod(mod_id_or_name)
            if isinstance(mod_id_or_name, int)
            else self.find_mod(mod_id_or_name)
        )
        if mod is None:
            raise NoCompatibleVersionError(
                f"{mod_id_or_name!r} is not in the local catalog"
            )
        versions = self.get_versions(mod.id, compatible_with=spt_version)
        if wanted is not None:
            versions = [version for version in versions if version.version == wanted]
        if not versions:
            raise NoCompatibleVersionError(
                f"no cached version of {mod.name!r} matches the request"
            )
        return _highest(versions)

    def classify(
        self, installed: Sequence[ModInfo], *, compatible_with: str | None = None
    ) -> list[CatalogEntry]:
        """Join the cached catalog with the installed mods of the game repository.

        A cached mod counts as installed when its Forge ID equals the
        ``Mod-Forge-Id`` of an installed mod; otherwise the mod names are
        compared case-insensitively. An installed mod is
        ``update_available`` when the newest cached version (restricted to
        compatible ones if ``compatible_with`` is set) is strictly newer than
        the installed version. Everything else is ``available``.

        The installed mods are passed in rather than read here, so the catalog
        stays independent of the repository and service layers. Callers pass
        the mods they consider active; no state filtering happens here.

        Args:
            installed: The mods currently installed in the game repository.
            compatible_with: SPT version filter; drops cached mods without a
                compatible version and restricts the update check to compatible
                versions.

        Returns:
            One entry per cached mod, ordered by name.
        """
        by_forge_id = {mod.forge_id: mod for mod in installed if mod.forge_id}
        by_name = {mod.name.casefold(): mod for mod in installed}

        entries: list[CatalogEntry] = []
        for mod in self.search("", compatible_with=compatible_with):
            versions = self.get_versions(mod.id, compatible_with=compatible_with)
            latest = versions[0] if versions else None
            match = by_forge_id.get(str(mod.id)) or by_name.get(mod.name.casefold())
            if match is None:
                entries.append(CatalogEntry(mod, "available", None, latest))
                continue
            newer = latest is not None and is_newer(latest.version, match.version)
            entries.append(
                CatalogEntry(
                    mod,
                    "update_available" if newer else "installed",
                    match.version,
                    latest,
                )
            )
        return entries

    def _all_mods(self) -> list[CatalogMod]:
        rows = self._db.execute(
            f"SELECT {_MOD_COLUMNS} FROM mods ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [_mod_from_row(row) for row in rows]

    def _prune(self, seen: set[int]) -> None:
        cached = {int(row["id"]) for row in self._db.execute("SELECT id FROM mods")}
        stale = [(mod_id,) for mod_id in cached - seen]
        self._db.executemany("DELETE FROM versions WHERE mod_id = ?", stale)
        self._db.executemany("DELETE FROM mods WHERE id = ?", stale)

    def _upsert_mod(self, mod: CatalogMod) -> _UpsertOutcome:
        existing = self.get_mod(mod.id)
        if existing == mod:
            return "unchanged"
        self._db.execute(
            f"INSERT OR REPLACE INTO mods ({_MOD_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mod.id,
                mod.hub_id,
                mod.guid,
                mod.name,
                mod.slug,
                mod.teaser,
                mod.thumbnail,
                mod.author,
                mod.downloads,
                mod.detail_url,
                mod.published_at,
                mod.created_at,
                mod.updated_at,
                mod.favourites_count,
                mod.endorsements_count,
                (
                    None
                    if mod.fika_compatibility is None
                    else int(mod.fika_compatibility)
                ),
                int(mod.featured),
                int(mod.contains_ads),
                int(mod.contains_ai_content),
                mod.category,
                mod.license_name,
                mod.license_link,
                _links_to_json(mod.source_links),
            ),
        )
        return "new" if existing is None else "updated"

    def _upsert_version(self, version: CatalogVersion) -> _UpsertOutcome:
        row = self._db.execute(
            f"SELECT {_VERSION_COLUMNS} FROM versions WHERE id = ?", (version.id,)
        ).fetchone()
        existing = None if row is None else _version_from_row(row)
        merged = _merge_version(existing, version)
        if existing == merged:
            return "unchanged"
        self._db.execute(
            f"INSERT OR REPLACE INTO versions ({_VERSION_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                merged.id,
                merged.mod_id,
                merged.version,
                merged.link,
                merged.content_length,
                merged.spt_version_constraint,
                merged.downloads,
                merged.published_at,
                merged.updated_at,
            ),
        )
        return "new" if existing is None else "updated"

    def _stored_data_version(self) -> str | None:
        row = self._db.execute(
            "SELECT value FROM sync_state WHERE key = ?", (DATA_VERSION_KEY,)
        ).fetchone()
        return None if row is None else row["value"]

    def _store_data_version(self) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
            (DATA_VERSION_KEY, DATA_VERSION),
        )

    def _advance_watermark(self, updated_at: str | None) -> None:
        if not updated_at:
            return
        current = self.last_synced_at()
        if current is not None and current >= updated_at:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
            (WATERMARK_KEY, updated_at),
        )


def _percent(current_page: int, last_page: int) -> int:
    if last_page <= 0:
        return 100
    return min(100, round(current_page * 100 / last_page))
