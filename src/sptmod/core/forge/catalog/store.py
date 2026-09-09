"""The :class:`Catalog` database object: the only module that opens the SQLite cache.

The shared names of :mod:`sptmod.core.forge.catalog.schema` and
:mod:`sptmod.core.forge.catalog.rows` are imported back under their original
private names, so the method bodies read exactly as they did in the single
module they were split out of.
"""

import contextlib
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from sptmod.core.forge.catalog.rows import delta_filter as _delta_filter
from sptmod.core.forge.catalog.rows import first_seen_at as _first_seen_at
from sptmod.core.forge.catalog.rows import highest as _highest
from sptmod.core.forge.catalog.rows import is_compatible, is_newer
from sptmod.core.forge.catalog.rows import links_to_json as _links_to_json
from sptmod.core.forge.catalog.rows import merge_version as _merge_version
from sptmod.core.forge.catalog.rows import mod_from_forge as _mod_from_forge
from sptmod.core.forge.catalog.rows import mod_from_row as _mod_from_row
from sptmod.core.forge.catalog.rows import percent as _percent
from sptmod.core.forge.catalog.rows import version_from_forge as _version_from_forge
from sptmod.core.forge.catalog.rows import version_from_row as _version_from_row
from sptmod.core.forge.catalog.rows import version_from_summary as _version_from_summary
from sptmod.core.forge.catalog.schema import (
    DATA_VERSION,
    DATA_VERSION_KEY,
    SCHEMA,
    SYNC_PAGE_STEP,
    WATERMARK_KEY,
    CachedDescription,
    CatalogEntry,
    CatalogMod,
    CatalogVersion,
    NoCompatibleVersionError,
    SyncResult,
    default_db_path,
)
from sptmod.core.forge.catalog.schema import DESCRIPTION_COLUMNS as _DESCRIPTION_COLUMNS
from sptmod.core.forge.catalog.schema import MOD_COLUMN_MIGRATIONS as _MOD_COLUMN_MIGRATIONS
from sptmod.core.forge.catalog.schema import MOD_COLUMNS as _MOD_COLUMNS
from sptmod.core.forge.catalog.schema import VERSION_COLUMN_MIGRATIONS as _VERSION_COLUMN_MIGRATIONS
from sptmod.core.forge.catalog.schema import VERSION_COLUMNS as _VERSION_COLUMNS
from sptmod.core.forge.catalog.schema import VERSIONS_SCHEMA as _VERSIONS_SCHEMA
from sptmod.core.forge.catalog.schema import UpsertOutcome as _UpsertOutcome
from sptmod.core.forge.client import MAX_PER_PAGE, ForgeClient
from sptmod.core.models import ModInfo, Progress


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
            for table, columns in (
                ("mods", _MOD_COLUMN_MIGRATIONS),
                ("versions", _VERSION_COLUMN_MIGRATIONS),
            ):
                for column in columns:
                    with contextlib.suppress(sqlite3.OperationalError):
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
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
                        SYNC_PAGE_STEP,
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

    def get_description(self, mod_id: int) -> CachedDescription | None:
        """Return the stored description of ``mod_id``, or None if unstored."""
        row = self._db.execute(
            f"SELECT {_DESCRIPTION_COLUMNS} FROM mods WHERE id = ?", (mod_id,)
        ).fetchone()
        if row is None or row["description"] is None:
            return None
        return CachedDescription(
            text=str(row["description"]), updated_at=row["description_updated_at"]
        )

    def store_description(
        self, mod_id: int, description: str, updated_at: str | None
    ) -> None:
        """Store the reduced description of a cached mod.

        Args:
            mod_id: The numeric Forge mod ID; unknown IDs store nothing.
            description: The description reduced to plain text.
            updated_at: The mod's ``updated_at`` the description belongs to.
        """
        self._db.execute(
            "UPDATE mods SET description = ?, description_updated_at = ? WHERE id = ?",
            (description, updated_at, mod_id),
        )
        self._db.commit()

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

    def find_by_slug(self, slug: str) -> CatalogMod | None:
        """Return the cached mod whose slug matches ``slug`` case-insensitively.

        The slug is the stable name a mod carries in its Forge address, so it
        identifies a mod where its display name would not.
        """
        wanted = slug.casefold()
        return next(
            (mod for mod in self._all_mods() if mod.slug.casefold() == wanted), None
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
                wanted version does not exist, or no known version is
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
            named = f"No version {wanted} of" if wanted else "No version of"
            supported = f"supports SPT {spt_version}" if spt_version else "is available"
            raise NoCompatibleVersionError(f"{named} {mod.name!r} {supported}.")
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
        merged = replace(mod, first_seen_at=_first_seen_at(existing))
        if existing == merged:
            return "unchanged"
        stored = self.get_description(mod.id)
        self._db.execute(
            f"INSERT OR REPLACE INTO mods ({_MOD_COLUMNS}, {_DESCRIPTION_COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                merged.first_seen_at,
                None if stored is None else stored.text,
                None if stored is None else stored.updated_at,
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
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                merged.fika_compatibility,
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
