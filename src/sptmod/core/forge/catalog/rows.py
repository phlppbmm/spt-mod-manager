"""Pure conversions between SQLite rows, Forge payloads and catalog dataclasses.

Nothing here touches a database connection or the Forge API; the functions turn
one value into another and decide which of two values wins.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from sptmod.core.forge.catalog.schema import (
    SYNC_OVERLAP_DAYS,
    CatalogMod,
    CatalogSourceLink,
    CatalogVersion,
)
from sptmod.core.forge.client import ForgeMod, ForgeVersion, ForgeVersionSummary
from sptmod.core.models import Json
from sptmod.core.modmeta import UnsupportedRangeError, version_matches


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


def mod_from_row(row: sqlite3.Row) -> CatalogMod:
    """Build a cached mod from a ``mods`` row."""
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
        first_seen_at=row["first_seen_at"],
    )


def _links_from_json(raw: str | None) -> tuple[CatalogSourceLink, ...]:
    """Decode the JSON-encoded source links column, tolerating junk."""
    try:
        data: Json = json.loads(raw or "[]")
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


def links_to_json(links: tuple[CatalogSourceLink, ...]) -> str:
    """Encode the source links for the JSON-encoded ``source_links`` column."""
    return json.dumps([{"url": link.url, "label": link.label} for link in links])


def version_from_row(row: sqlite3.Row) -> CatalogVersion:
    """Build a cached version from a ``versions`` row."""
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
        fika_compatibility=row["fika_compatibility"],
    )


def mod_from_forge(mod: ForgeMod) -> CatalogMod:
    """Build a cached mod from a Forge ``/mods`` payload."""
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


def version_from_summary(mod_id: int, summary: ForgeVersionSummary) -> CatalogVersion:
    """Build a cached version from an ``include=versions`` summary."""
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


def version_from_forge(mod_id: int, version: ForgeVersion) -> CatalogVersion:
    """Build a cached version from a full Forge version payload."""
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
        fika_compatibility=version.fika_compatibility,
    )


def merge_version(
    existing: CatalogVersion | None, incoming: CatalogVersion
) -> CatalogVersion:
    """Keep cached download details that the incoming payload does not carry.

    The ``include=versions`` summaries of /mods know neither ``link`` nor
    ``content_length`` nor ``updated_at`` nor ``fika_compatibility``, so a sync
    must not wipe what a previous :meth:`Catalog.fetch_versions` already stored.
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
        fika_compatibility=(
            existing.fika_compatibility
            if incoming.fika_compatibility is None
            else incoming.fika_compatibility
        ),
    )


def first_seen_at(existing: CatalogMod | None) -> str | None:
    """Return the first-seen stamp for an upsert: stored value, or now for new mods."""
    if existing is not None:
        return existing.first_seen_at
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_comparable(version: str) -> bool:
    try:
        return version_matches(version, version)
    except UnsupportedRangeError:
        return False


def highest(versions: Sequence[CatalogVersion]) -> CatalogVersion:
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


def delta_filter(watermark: str) -> str | None:
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


def percent(current_page: int, last_page: int) -> int:
    """Return the progress percentage of a sync page, guarding an unknown total."""
    if last_page <= 0:
        return 100
    return min(100, round(current_page * 100 / last_page))
