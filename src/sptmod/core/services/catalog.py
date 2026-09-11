"""The Forge catalog: sync, search, browse, mod details and favorites.

Everything the manager knows about mods it has not installed. The catalog cache
is disposable, so every entry point here reopens it and treats a missing or
unreadable cache as a plain error rather than as corruption.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, Literal

from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.favorites import default_favorites_path, load_favorites, save_favorites
from sptmod.core.forge import cache as download_cache
from sptmod.core.forge import thumbnails as thumbnail_cache
from sptmod.core.forge.catalog import (
    Catalog,
    CatalogEntry,
    CatalogMod,
    CatalogState,
    CatalogVersion,
    SyncResult,
    default_db_path,
)
from sptmod.core.forge.client import ForgeClient
from sptmod.core.models import ModInfo, ModState, Progress
from sptmod.core.services.common import detected_spt_version, open_catalog
from sptmod.core.services.state import list_mods
from sptmod.core.workspace import download_cache_dir

type BrowseFilter = Literal["all", "installed", "available"]

# The id space the entries of installed mods without a catalog row live in.
# Forge ids are positive, so a negative id can never stand for a Forge mod.
_LOCAL_ID_LIMIT: Final = 2**31


@dataclass(frozen=True)
class ClearCacheResult:
    """Outcome of :func:`clear_catalog_cache`.

    ``removed`` is false when there was no cache file to delete.
    ``downloads_path`` is set only when the kept mod archives were cleared
    too, and ``downloads_freed_bytes`` says how much space that gave back.
    """

    db_path: Path
    removed: bool
    downloads_path: Path | None = None
    downloads_freed_bytes: int = 0


@dataclass(frozen=True)
class ModDetails:
    """Everything the detail view shows about a catalog mod.

    ``mod``, ``versions`` and the description all come from the local cache;
    the description is refreshed from the Forge whenever the cached mod row
    moved on to a newer ``updated_at`` than the stored text.
    """

    mod: CatalogMod | None
    versions: list[CatalogVersion]
    description: str


class _HtmlTextExtractor(HTMLParser):
    """Collect the text content of an HTML fragment, dropping every tag."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in ("p", "br", "li", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1


def _html_to_text(html: str) -> str:
    """Strip HTML tags from a Forge description, keeping paragraph breaks."""
    extractor = _HtmlTextExtractor()
    extractor.feed(html)
    text = "".join(extractor.parts)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _load_mod_details(
    catalog: Catalog,
    client: ForgeClient,
    mod_id: int,
    versions: list[CatalogVersion],
    description: str | None,
    stamp: str | None,
) -> tuple[list[CatalogVersion], str]:
    """Fetch what the cache is missing: the description and version links.

    Args:
        catalog: The open catalog cache to store the description in.
        client: The Forge API client to read from.
        mod_id: The numeric Forge mod ID.
        versions: The cached versions; refetched when none carries a link.
        description: The stored description, or None when it must be fetched.
        stamp: The cached mod's ``updated_at``, stored with the description.

    Returns:
        The versions to show and the plain-text description.
    """
    if description is None:
        description = _html_to_text(client.mod(mod_id).description or "")
        catalog.store_description(mod_id, description, stamp)
    if not any(version.link for version in versions):
        versions = catalog.fetch_versions(client, mod_id)
    return versions, description


def sync_catalog(
    path: Path,
    full: bool = False,
    progress: Progress | None = None,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> SyncResult:
    """Refresh the local Forge catalog cache of the game directory at ``path``.

    Delegates to :meth:`Catalog.sync`; ``full`` forces a full pagination
    instead of a delta sync and ``progress`` is reported once per fetched page.
    The game directory is only used to locate the installation the cache serves;
    the cache itself never lives inside it.

    Args:
        path: The tracked game directory.
        full: Force a full sync.
        progress: Optional per-page progress callback.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        client: Forge API client; a default one is built when omitted.

    Returns:
        The counts of inserted and updated mods and versions.

    Raises:
        CatalogUnavailableError: The catalog cache cannot be opened.
        ForgeUnavailableError: The API is unreachable; the cache keeps its
            previous contents.
        ForgeApiError: The API answered with an error envelope.
    """
    # The cache is machine-wide, so the game directory only identifies the
    # caller here; every service function takes it for a uniform RPC contract.
    del path
    if client is not None:
        with open_catalog(db_path) as catalog:
            return catalog.sync(client, full=full, progress=progress)
    with ForgeClient() as owned, open_catalog(db_path) as catalog:
        return catalog.sync(owned, full=full, progress=progress)


def search_catalog(
    path: Path,
    query: str,
    compatible_only: bool = False,
    *,
    db_path: Path | None = None,
) -> list[CatalogEntry]:
    """Search the cached catalog and join the result with the installed mods.

    Matches ``query`` case-insensitively against mod name and author and
    classifies every hit against the active mods of the repository at ``path``,
    so callers see whether a mod is installed, outdated or merely available.

    Args:
        path: The tracked game directory.
        query: Substring matched against name and author; empty matches all.
        compatible_only: Keep only mods with a version compatible with the SPT
            version of the game directory.
        db_path: Catalog database path; defaults to :func:`default_db_path`.

    Returns:
        The matching entries ordered by mod name.

    Raises:
        CatalogUnavailableError: The catalog cache cannot be opened.
        GitError: git failed.
    """
    installed = list_mods(path)
    spt_version = detected_spt_version(path)
    with open_catalog(db_path) as catalog:
        compatible_with = spt_version if compatible_only else None
        matched = {
            mod.id for mod in catalog.search(query, compatible_with=compatible_with)
        }
        entries = _classified(
            catalog, installed, installed, spt_version, compatible_only
        )
    return [entry for entry in entries if entry.mod.id in matched]


def browse_catalog(
    path: Path,
    catalog_filter: BrowseFilter = "all",
    compatible_only: bool = False,
    *,
    spt_version: str | None = None,
    db_path: Path | None = None,
) -> list[CatalogEntry]:
    """Return the cached catalog joined with the installed mods of ``path``.

    ``catalog_filter`` selects one of the three frontend lists: ``installed`` keeps the
    entries matching an active mod, including the ones with an update
    available, ``available`` keeps the rest, and ``all`` keeps everything.

    Args:
        path: The tracked game directory.
        catalog_filter: Which of the three lists to return.
        compatible_only: Keep only mods with a version compatible with the SPT
            version the entries are judged against.
        spt_version: Judge the entries against this SPT version instead of the
            one the game directory has; empty or omitted uses that one.
        db_path: Catalog database path; defaults to :func:`default_db_path`.

    Returns:
        The selected entries ordered by mod name.

    Raises:
        CatalogUnavailableError: The catalog cache cannot be opened.
        GitError: git failed.
    """
    installed = list_mods(path)
    tracked = [
        mod
        for mod in list_mods(path, include_all=True)
        if mod.state != ModState.REMOVED
    ]
    judged_against = spt_version or detected_spt_version(path)
    with open_catalog(db_path) as catalog:
        entries = _classified(
            catalog, installed, tracked, judged_against, compatible_only
        )
    if catalog_filter == "all":
        return entries
    wanted = catalog_filter == "installed"
    return [entry for entry in entries if (entry.state != "available") == wanted]


def get_mod_details(
    mod_id: int,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> ModDetails:
    """Return everything the detail view shows about a catalog mod.

    The cached mod row and its versions come from the local catalog (version
    details are fetched once when the cache only holds summaries). The
    description is reduced from HTML to plain text so callers can render it
    without trusting markup, and stored in the catalog with the mod's
    ``updated_at``: it is served from disk until a sync moves that stamp on,
    then fetched from the Forge detail endpoint once. An unreachable Forge
    degrades to the stored description, or to an empty one when nothing was
    stored yet, instead of failing the whole view.
    """
    cached: CatalogMod | None = None
    versions: list[CatalogVersion] = []
    with open_catalog(db_path) as catalog:
        cached = catalog.get_mod(mod_id)
        versions = catalog.get_versions(mod_id)
        stamp = None if cached is None else cached.updated_at
        stored = catalog.get_description(mod_id)
        description: str | None = None
        if stored is not None and stored.updated_at == stamp:
            description = stored.text
            if any(version.link for version in versions):
                return ModDetails(
                    mod=cached, versions=versions, description=description
                )
        try:
            if client is not None:
                versions, description = _load_mod_details(
                    catalog, client, mod_id, versions, description, stamp
                )
            else:
                with ForgeClient() as owned:
                    versions, description = _load_mod_details(
                        catalog, owned, mod_id, versions, description, stamp
                    )
        except ForgeUnavailableError:
            if cached is None:
                raise
            description = None if stored is None else stored.text
    return ModDetails(mod=cached, versions=versions, description=description or "")


def clear_catalog_cache(
    db_path: Path | None = None, *, downloads: bool = False
) -> ClearCacheResult:
    """Delete the local Forge catalog cache.

    Removes the SQLite file the catalog lives in and the mod images fetched
    for it, so the next :func:`sync_catalog` starts from an empty cache. The
    cache holds no truth about the installation, hence nothing else is
    touched.

    Args:
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        downloads: Also remove the kept mod archives, which are downloaded
            again when they are needed next.

    Returns:
        The path that was cleared, whether a file was actually deleted and,
        when ``downloads`` was asked for, the archive directory and how many
        bytes it gave back.

    Raises:
        OSError: the cache file exists but cannot be deleted.
    """
    cache = db_path or default_db_path()
    removed = cache.exists()
    cache.unlink(missing_ok=True)
    thumbnail_cache.clear()
    if not downloads:
        return ClearCacheResult(db_path=cache, removed=removed)
    freed = download_cache.clear()
    return ClearCacheResult(
        db_path=cache,
        removed=removed,
        downloads_path=download_cache_dir(),
        downloads_freed_bytes=freed,
    )


def get_thumbnails(
    mod_ids: Sequence[int],
    *,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
) -> dict[int, str | None]:
    """Return the thumbnail of every mod in ``mod_ids`` as a data URL.

    The image of a mod is read from the cached catalog row, never from the
    caller, and fetched from the Forge with the header its image host insists
    on. Every image is kept on disk, so the next call for the same mod answers
    from there.

    Args:
        mod_ids: Forge ids of the mods to show an image for.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        cache_dir: Directory the images are kept in; defaults to
            :func:`sptmod.core.workspace.thumbnail_cache_dir`.

    Returns:
        The data URL of each requested mod, or None for a mod the catalog
        knows no image for and for an image that cannot be fetched.

    Raises:
        CatalogUnavailableError: The catalog cache cannot be opened.
    """
    with open_catalog(db_path) as catalog:
        urls = {mod_id: _thumbnail_url(catalog, mod_id) for mod_id in mod_ids}
    wanted = {mod_id: url for mod_id, url in urls.items() if url is not None}
    if not wanted:
        return dict.fromkeys(urls, None)
    with ForgeClient() as client:
        loaded = thumbnail_cache.load(wanted, client=client, cache_dir=cache_dir)
    return {mod_id: loaded.get(mod_id) for mod_id in urls}


def list_favorites(*, favorites_path: Path | None = None) -> list[int]:
    """Return the Forge ids of the mods marked as favorites.

    Args:
        favorites_path: Favorites file; defaults to
            :func:`default_favorites_path`.

    Returns:
        The favored Forge mod ids, sorted and without duplicates. An
        unreadable list counts as empty.
    """
    return load_favorites(favorites_path or default_favorites_path())


def set_favorite(
    mod_id: int, favorite: bool, *, favorites_path: Path | None = None
) -> list[int]:
    """Mark or unmark the mod ``mod_id`` as a favorite.

    Marking is independent of the catalog and of any game directory, so any id
    is accepted and marking twice changes nothing.

    Args:
        mod_id: Forge id of the mod.
        favorite: Whether the mod should be a favorite.
        favorites_path: Favorites file; defaults to
            :func:`default_favorites_path`.

    Returns:
        All favored Forge mod ids after the change, sorted.

    Raises:
        OSError: the favorites file cannot be written.
    """
    path = favorites_path or default_favorites_path()
    current = set(load_favorites(path))
    if favorite:
        current.add(mod_id)
    else:
        current.discard(mod_id)
    mod_ids = sorted(current)
    save_favorites(path, mod_ids)
    return mod_ids


def _classified(
    catalog: Catalog,
    installed: Sequence[ModInfo],
    tracked: Sequence[ModInfo],
    spt_version: str | None,
    compatible_only: bool,
) -> list[CatalogEntry]:
    """Join the catalog with ``installed``, judged against ``spt_version``.

    ``tracked`` are the mods an entry is built for where the catalog holds no
    row at all; pass the same list as ``installed`` where only cached mods are
    wanted.
    """
    entries = _cached_entries(catalog, installed, spt_version, compatible_only)
    entries.extend(_local_entries(catalog, tracked))
    return sorted(entries, key=lambda entry: (entry.mod.name.casefold(), entry.mod.id))


def _cached_entries(
    catalog: Catalog,
    installed: Sequence[ModInfo],
    spt_version: str | None,
    compatible_only: bool,
) -> list[CatalogEntry]:
    """Return one entry per cached mod, judged against ``spt_version``.

    ``latest_version`` always holds a version the installer can actually
    resolve, so a mod without one is listed as ``available``/``installed``
    without a version to offer instead of promising an install or update that
    the resolver would refuse. Without ``compatible_only`` those mods stay in
    the list.
    """
    compatible = catalog.classify(installed, compatible_with=spt_version)
    if compatible_only or spt_version is None:
        return list(compatible)

    known = {entry.mod.id for entry in compatible}
    entries = list(compatible)
    for entry in catalog.classify(installed):
        if entry.mod.id in known:
            continue
        state: CatalogState = (
            "installed" if entry.installed_version is not None else "available"
        )
        entries.append(replace(entry, state=state, latest_version=None))
    return entries


def _local_entries(catalog: Catalog, tracked: Sequence[ModInfo]) -> list[CatalogEntry]:
    """Return one entry per mod of ``tracked`` the catalog holds no row for.

    A mod a player built themselves or adopted from their own installation is
    in no catalog, so the join produces nothing for it and it would be missing
    from every list the desktop app shows. Such an entry carries what the
    repository knows about the mod, no version to update to, and the ``local``
    mark that keeps the Forge actions away from it. The compatibility filter
    of the catalog does not apply: there is no published constraint to judge
    these mods by.
    """
    cached = catalog.search("")
    known_ids = {str(mod.id) for mod in cached}
    known_names = {mod.name.casefold() for mod in cached}
    entries: list[CatalogEntry] = []
    taken: set[int] = set()
    for mod in sorted(tracked, key=lambda item: item.name.casefold()):
        cached_id = bool(mod.forge_id) and mod.forge_id in known_ids
        if cached_id or mod.name.casefold() in known_names:
            continue
        entries.append(_local_entry(mod, taken))
    return entries


def _local_entry(mod: ModInfo, taken: set[int]) -> CatalogEntry:
    """Return the catalog entry standing for the installed mod ``mod``."""
    mod_id = _local_id(mod.name, taken)
    taken.add(mod_id)
    return CatalogEntry(
        mod=CatalogMod(
            id=mod_id,
            hub_id=None,
            guid=mod.guid or None,
            name=mod.name,
            slug="",
            teaser=None,
            thumbnail=None,
            author=mod.author,
            downloads=0,
            detail_url="",
            published_at=None,
            created_at=None,
            updated_at=None,
        ),
        state="installed",
        installed_version=mod.version,
        latest_version=None,
        local=True,
    )


def _local_id(name: str, taken: set[int]) -> int:
    """Return the id the entry of the installed mod ``name`` is keyed by.

    Derived from the mod name, so the same mod keeps the same id between calls
    and the frontend does not tear its row down on every refresh, and negative,
    so it can never collide with a Forge id. Two names landing on the same
    number walk down from it until one is free.
    """
    digest = hashlib.blake2b(name.casefold().encode("utf-8"), digest_size=8).digest()
    mod_id = -1 - int.from_bytes(digest, "big") % _LOCAL_ID_LIMIT
    while mod_id in taken:
        mod_id -= 1
    return mod_id


def _thumbnail_url(catalog: Catalog, mod_id: int) -> str | None:
    """Return the image URL the catalog holds for ``mod_id``, if it holds one."""
    mod = catalog.get_mod(mod_id)
    return None if mod is None else mod.thumbnail or None
