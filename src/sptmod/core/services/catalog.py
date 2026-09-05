"""The Forge catalog: sync, search, browse, mod details and favorites.

Everything the manager knows about mods it has not installed. The catalog cache
is disposable, so every entry point here reopens it and treats a missing or
unreadable cache as a plain error rather than as corruption.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.favorites import default_favorites_path, load_favorites, save_favorites
from sptmod.core.forge import cache as download_cache
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
from sptmod.core.models import ModInfo, Progress
from sptmod.core.services.common import detected_spt_version, open_catalog
from sptmod.core.services.state import list_mods
from sptmod.core.workspace import download_cache_dir

type BrowseFilter = Literal["all", "installed", "available"]


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
    compatible_with = _compatible_with(path, compatible_only)
    with open_catalog(db_path) as catalog:
        matched = {
            mod.id for mod in catalog.search(query, compatible_with=compatible_with)
        }
        entries = _classified(catalog, installed, path, compatible_only)
    return [entry for entry in entries if entry.mod.id in matched]


def browse_catalog(
    path: Path,
    catalog_filter: BrowseFilter = "all",
    compatible_only: bool = False,
    *,
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
            version of the game directory.
        db_path: Catalog database path; defaults to :func:`default_db_path`.

    Returns:
        The selected entries ordered by mod name.

    Raises:
        CatalogUnavailableError: The catalog cache cannot be opened.
        GitError: git failed.
    """
    installed = list_mods(path)
    with open_catalog(db_path) as catalog:
        entries = _classified(catalog, installed, path, compatible_only)
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

    Removes the SQLite file the catalog lives in, so the next
    :func:`sync_catalog` starts from an empty cache. The cache holds no truth
    about the installation, hence nothing else is touched.

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
    if not downloads:
        return ClearCacheResult(db_path=cache, removed=removed)
    freed = download_cache.clear()
    return ClearCacheResult(
        db_path=cache,
        removed=removed,
        downloads_path=download_cache_dir(),
        downloads_freed_bytes=freed,
    )


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
    path: Path,
    compatible_only: bool,
) -> list[CatalogEntry]:
    """Join the catalog with ``installed``, judged against the SPT version.

    ``latest_version`` always holds a version the installer can actually
    resolve, so a mod without one is listed as ``available``/``installed``
    without a version to offer instead of promising an install or update that
    the resolver would refuse. Without ``compatible_only`` those mods stay in
    the list.
    """
    spt_version = detected_spt_version(path)
    compatible = catalog.classify(installed, compatible_with=spt_version)
    if compatible_only or spt_version is None:
        return compatible

    known = {entry.mod.id for entry in compatible}
    entries = list(compatible)
    for entry in catalog.classify(installed):
        if entry.mod.id in known:
            continue
        state: CatalogState = (
            "installed" if entry.installed_version is not None else "available"
        )
        entries.append(replace(entry, state=state, latest_version=None))
    return sorted(entries, key=lambda entry: (entry.mod.name.casefold(), entry.mod.id))


def _compatible_with(path: Path, compatible_only: bool) -> str | None:
    return detected_spt_version(path) if compatible_only else None
