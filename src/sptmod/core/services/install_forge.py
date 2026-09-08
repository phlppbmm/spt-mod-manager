"""Installing mods from the Forge: planning and execution.

Planning resolves a mod and its dependencies into an ordered list of entries;
execution downloads each entry and hands it to the archive installer. Both
report progress step by step so the caller can show what is happening.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from sptmod.core.errors import (
    ArchiveError,
    PartialInstallError,
)
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from sptmod.core.forge.client import ForgeClient
from sptmod.core.forge.download import download_cached
from sptmod.core.forge.resolve import plan_install
from sptmod.core.models import (
    InstallPlan,
    PlanEntry,
    Progress,
)
from sptmod.core.services.common import (
    open_catalog,
    prefixed_progress,
    required_spt_version,
)
from sptmod.core.services.install_archive import (
    InstallResult,
    install_from_archive,
)
from sptmod.core.services.state import (
    list_mods,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


FIKA_SERVER_SLUG: Final = "project-fika-server"


FIKA_CLIENT_SLUG: Final = "project-fika"


_FIKA_SERVER_TITLE: Final = "Project Fika - Server"


_FIKA_CLIENT_TITLE: Final = "Project Fika"


@dataclass(frozen=True)
class FikaInstallPlan:
    """What playing co-op needs: the two halves of Fika and their one plan.

    Fika is published as two mods, the server mod and the client plugin. The
    plan holds both halves in one list, the server mod first, so a caller
    confirms them together and then installs them by their ids.
    """

    server_mod_id: int
    client_mod_id: int
    plan: InstallPlan


@dataclass(frozen=True)
class InstallModResult:
    """Outcome of :func:`install_mod`.

    ``installed`` holds one entry per mod of ``plan``, in the order they were
    installed, so dependencies come before the mod that needed them.
    """

    plan: InstallPlan
    installed: list[InstallResult] = field(default_factory=list[InstallResult])


def plan_mod_install(
    path: Path,
    mod_id_or_name: int | str,
    version: str | None = None,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> InstallPlan:
    """Resolve what installing ``mod_id_or_name`` into ``path`` would download.

    Picks the version from the cached catalog, asks Forge for its dependency
    tree and drops the dependencies the game directory already satisfies. The
    plan is what a caller presents for confirmation before running
    :func:`install_mod`; nothing is downloaded or written here.

    Args:
        path: The tracked game directory.
        mod_id_or_name: The numeric Forge mod ID or the mod name, matched
            case-insensitively.
        version: The exact version to install; without it the highest version
            compatible with the SPT version of ``path`` wins.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        client: Forge API client; a default one is built when omitted.

    Returns:
        The plan with dependencies before the mods needing them and the
        requested mod last.

    Raises:
        NotAGameDirError: the SPT version of ``path`` cannot be determined, so
            no plan can be resolved for it.
        NoCompatibleVersionError: the catalog knows no version of the mod
            matching ``version`` and the SPT version.
        UnresolvableDependencyError: a dependency conflicts, has no compatible
            version or takes part in a cycle.
        ForgeUnavailableError: the Forge API is unreachable.
        ForgeApiError: the Forge API answered with an error envelope.
        GitError: git failed.
    """
    if client is not None:
        return _resolve_plan(path, mod_id_or_name, version, db_path, client)
    with ForgeClient() as owned:
        return _resolve_plan(path, mod_id_or_name, version, db_path, owned)


def install_mod(
    path: Path,
    mod_id_or_name: int | str,
    version: str | None = None,
    force: bool = False,
    progress: Progress | None = None,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> InstallModResult:
    """Install ``mod_id_or_name`` and its missing dependencies from Forge.

    Resolves the plan of :func:`plan_mod_install` and works it off in order:
    every entry is downloaded into a temporary directory and installed through
    :func:`install_from_archive`, so each mod becomes its own commit with the
    conflict and profile rules of a local install. The commits carry
    ``Mod-Forge-Id`` and ``Mod-Forge-Version-Id`` of the planned entry and
    ``Mod-Source`` set to the download URL. ``force`` is passed to every
    install of the plan.

    Progress is reported through the ``download`` step while an archive
    arrives and through the steps of :func:`install_from_archive` while it is
    installed, each prefixed by the entry it belongs to in its message.

    The plan is resolved again here rather than taken from the caller, so the
    download links are fresh. A plan a caller showed for confirmation can
    therefore differ from the one that is installed when Forge changes in
    between; that race is accepted for the prototype.

    An install that fails partway leaves the commits of the entries installed
    before it in place; the game directory therefore stays consistent and the
    call can be repeated once the cause is fixed.

    Args:
        path: The tracked game directory.
        mod_id_or_name: The numeric Forge mod ID or the mod name.
        version: The exact version of the requested mod; without it the highest
            compatible one.
        force: Install despite conflicts, missing dependencies or an
            incompatible SPT version.
        progress: Optional callback for download and install progress.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        client: Forge API client; a default one is built when omitted.

    Returns:
        The plan that was worked off and one install result per entry.

    Raises:
        NotAGameDirError: the SPT version of ``path`` cannot be determined.
        NoCompatibleVersionError: the catalog knows no matching version.
        UnresolvableDependencyError: a dependency cannot be resolved.
        ArchiveError: a planned entry has no download link, or an archive is
            incomplete, unreadable or unsafe.
        PartialInstallError: an entry failed after an earlier one was already
            installed; it carries the finished installs and the cause.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ConflictError: a file or DLL conflict blocks an install and ``force``
            is not set.
        DependencyError: a dependency check blocks an install and ``force`` is
            not set.
        ForgeUnavailableError: the Forge API or a download URL is unreachable.
        ForgeApiError: the Forge API answered with an error envelope.
        GitError: git failed.
    """
    if client is not None:
        return _install_plan(
            path, mod_id_or_name, version, force, progress, db_path, client
        )
    with ForgeClient() as owned:
        return _install_plan(
            path, mod_id_or_name, version, force, progress, db_path, owned
        )


def plan_fika_install(
    path: Path,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> FikaInstallPlan:
    """Resolve what installing Fika into ``path`` would download.

    Both halves are looked up by their Forge address and planned on their own,
    because they are published as separate mods with their own versions and
    dependencies. The two plans become one list, the server mod and its
    dependencies first, with anything both halves need listed once.

    Args:
        path: The tracked game directory.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        client: Forge API client; a default one is built when omitted.

    Returns:
        The ids of the two halves and the plan installing both of them.

    Raises:
        NotAGameDirError: the SPT version of ``path`` cannot be determined.
        NoCompatibleVersionError: one half of Fika is not in the catalog, or
            the catalog knows no version of it matching the SPT version.
        UnresolvableDependencyError: a dependency conflicts, has no compatible
            version or takes part in a cycle.
        ForgeUnavailableError: the Forge API is unreachable.
        ForgeApiError: the Forge API answered with an error envelope.
        GitError: git failed.
    """
    if client is not None:
        return _resolve_fika_plan(path, db_path, client)
    with ForgeClient() as owned:
        return _resolve_fika_plan(path, db_path, owned)


def _resolve_fika_plan(
    path: Path, db_path: Path | None, client: ForgeClient
) -> FikaInstallPlan:
    server_mod_id = _fika_mod_id(db_path, FIKA_SERVER_SLUG, _FIKA_SERVER_TITLE)
    client_mod_id = _fika_mod_id(db_path, FIKA_CLIENT_SLUG, _FIKA_CLIENT_TITLE)
    server_plan = _resolve_plan(path, server_mod_id, None, db_path, client)
    client_plan = _resolve_plan(path, client_mod_id, None, db_path, client)
    return FikaInstallPlan(
        server_mod_id=server_mod_id,
        client_mod_id=client_mod_id,
        plan=replace(
            server_plan,
            entries=_merged_entries(server_plan.entries, client_plan.entries),
        ),
    )


def _fika_mod_id(db_path: Path | None, slug: str, title: str) -> int:
    """Return the Forge id of one half of Fika.

    Raises:
        NoCompatibleVersionError: the catalog does not know that half.
    """
    with open_catalog(db_path) as catalog:
        mod = catalog.find_by_slug(slug)
    if mod is None:
        raise NoCompatibleVersionError(
            f"{title} is not in the mod catalog. Sync the catalog and try again."
        )
    return mod.id


def _merged_entries(
    server_entries: list[PlanEntry], client_entries: list[PlanEntry]
) -> list[PlanEntry]:
    """Return both halves as one install order, each mod appearing once."""
    merged: list[PlanEntry] = []
    seen: set[int] = set()
    for entry in [*server_entries, *client_entries]:
        if entry.mod_id in seen:
            continue
        seen.add(entry.mod_id)
        merged.append(entry)
    return merged


def _resolve_plan(
    path: Path,
    mod_id_or_name: int | str,
    version: str | None,
    db_path: Path | None,
    client: ForgeClient,
) -> InstallPlan:
    installed = required_spt_version(path)
    with open_catalog(db_path) as catalog:
        plan = plan_install(
            mod_id_or_name, version, catalog, client, list_mods(path), installed
        )
        return replace(
            plan,
            entries=[_with_link(entry, catalog, client) for entry in plan.entries],
        )


def _with_link(entry: PlanEntry, catalog: Catalog, client: ForgeClient) -> PlanEntry:
    """Fill the download link of ``entry`` from Forge when the cache has none.

    A synced catalog holds version summaries without a link; only
    :meth:`Catalog.fetch_versions` loads the full version records.
    """
    if entry.link:
        return entry
    versions = catalog.fetch_versions(client, entry.mod_id)
    fetched = next(v for v in versions if v.id == entry.version_id)
    return replace(entry, link=fetched.link, content_length=fetched.content_length)


def _install_plan(
    path: Path,
    mod_id_or_name: int | str,
    version: str | None,
    force: bool,
    progress: Progress | None,
    db_path: Path | None,
    client: ForgeClient,
) -> InstallModResult:
    plan = _resolve_plan(path, mod_id_or_name, version, db_path, client)
    installed: list[InstallResult] = []
    for entry in plan.entries:
        try:
            installed.append(_install_entry(path, entry, force, progress, client))
        except Exception as error:
            if not installed:
                raise
            raise PartialInstallError(
                f"{entry.name} {entry.version} failed after "
                f"{len(installed)} of {len(plan.entries)} mods were "
                f"installed: {error}",
                installed,
                error,
            ) from error
    return InstallModResult(plan=plan, installed=installed)


def _install_entry(
    path: Path,
    entry: PlanEntry,
    force: bool,
    progress: Progress | None,
    client: ForgeClient,
) -> InstallResult:
    """Download one planned entry and install it as its own commit."""
    reporter = prefixed_progress(progress, f"{entry.name} {entry.version}")
    archive = download_entry(
        client,
        entry.link,
        entry.content_length,
        entry.version_id,
        entry.name,
        reporter,
    )
    return install_from_archive(
        path,
        archive,
        version_override=entry.version,
        force=force,
        progress=reporter,
        forge_id=str(entry.mod_id),
        forge_version_id=str(entry.version_id),
        source=entry.link,
    )


def download_entry(
    client: ForgeClient,
    link: str | None,
    content_length: int | None,
    version_id: int,
    name: str,
    progress: Progress | None,
) -> Path:
    """Download ``link`` into the download cache, reusing an earlier copy.

    Raises:
        ArchiveError: Forge published no download link for the mod.
    """
    if not link:
        raise ArchiveError(f"Forge publishes no download link for {name}")
    return download_cached(
        client,
        link,
        str(version_id),
        progress=progress,
        expected_length=content_length,
    )
