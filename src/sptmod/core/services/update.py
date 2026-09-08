"""Finding and applying mod updates.

An update is discovered by asking the Forge about the installed versions and is
applied by replacing the files the mod owns, keeping a snapshot beforehand. Mods
the Forge does not know, or that no longer fit the SPT version, are reported
rather than touched.
"""

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sptmod.core.archive import (
    ArchiveLayout,
    extract,
    find_anchor,
)
from sptmod.core.conflicts import (
    apply_force,
    check_dependencies,
    check_dll_duplicates,
    check_file_conflicts,
    check_spt_compat,
)
from sptmod.core.errors import (
    ConflictError,
    DependencyError,
)
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient, UpdatesResult
from sptmod.core.game_dir import (
    BEPINEX_CONFIG_DIR,
    GameLayout,
    detect_layout,
    resolve_spt_version,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    TRAILER_MOD_PREVIOUS_VERSION,
    Conflict,
    ModFile,
    ModInfo,
    ModState,
    Progress,
)
from sptmod.core.modmeta import (
    ModMeta,
    build_trailers,
)
from sptmod.core.repo import Repo
from sptmod.core.services.common import (
    DEPENDENCY_KINDS,
    InstallStep,
    blocked_message,
    current_mods,
    for_game_layout,
    install_commit,
    mod_prefixes,
    prefixed_progress,
    prune_empty,
    report_step,
    required_spt_version,
    resolve_mod,
    write_mod_files,
)
from sptmod.core.services.install_archive import (
    read_meta,
)
from sptmod.core.services.install_forge import (
    download_entry,
)
from sptmod.core.services.state import (
    ensure_installable,
    list_mods,
)
from sptmod.core.workspace import (
    game_lock,
    staging_dir,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


@dataclass(frozen=True)
class UpdateCandidate:
    """An installed mod Forge recommends a newer version for.

    ``name`` is the name of the installed mod, ``link`` the download URL of the
    recommended version and empty when Forge published none.
    """

    name: str
    installed_version: str
    new_version: str
    version_id: int
    link: str = ""
    content_length: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class BlockedUpdate:
    """An installed mod whose newer version other installed mods block.

    ``blocking_mods`` names the installed mods whose dependency constraints
    stand in the way.
    """

    name: str
    installed_version: str
    new_version: str
    reason: str = ""
    blocking_mods: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class IncompatibleMod:
    """An installed mod without any version for the queried SPT release.

    ``latest_compatible_version`` is the newest version Forge has for any other
    SPT release, empty when Forge names none.
    """

    name: str
    installed_version: str
    reason: str = ""
    latest_compatible_version: str = ""


@dataclass(frozen=True)
class UpdatesReport:
    """What :func:`check_updates` found for the installed mods.

    ``skipped`` names the active mods that were not queried at all because
    neither their Forge ID nor the catalog identifies them on Forge.
    """

    spt_version: str
    available: list[UpdateCandidate] = field(default_factory=list[UpdateCandidate])
    blocked: list[BlockedUpdate] = field(default_factory=list[BlockedUpdate])
    up_to_date: list[str] = field(default_factory=list[str])
    incompatible: list[IncompatibleMod] = field(default_factory=list[IncompatibleMod])
    skipped: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of :func:`update_mod`.

    A no-op leaves ``version``, ``commit`` and ``files`` empty and explains
    itself in ``notice``; ``previous_version`` then holds the installed
    version. ``files`` are the files of the update commit with their change,
    so the files the new version dropped appear as deletions.
    """

    name: str
    previous_version: str
    version: str = ""
    commit: str = ""
    files: list[ModFile] = field(default_factory=list[ModFile])
    warnings: list[Conflict] = field(default_factory=list[Conflict])
    notice: str = ""


def check_updates(
    path: Path,
    names: Sequence[str] | None = None,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> UpdatesReport:
    """Ask Forge which of the mods installed in ``path`` have newer versions.

    Queries ``/mods/updates`` with one ``identifier:version`` pair per active
    mod, identified by its ``Mod-Forge-Id`` or, without one, by the Forge ID
    the catalog matches its name to. Active mods neither identifies are
    reported as ``skipped`` instead of being queried. ``names`` restricts the
    query to those mods, matched case-insensitively.

    The report keeps the four answers of the endpoint apart, so a caller can
    show blocked updates and mods without a version for the installed SPT
    release next to the updates that can actually be applied. Nothing is
    downloaded or written.

    Args:
        path: The tracked game directory.
        names: The mods to check; without it every active mod.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        client: Forge API client; a default one is built when omitted.

    Returns:
        The available, blocked, up-to-date, incompatible and skipped mods.

    Raises:
        NotAGameDirError: the SPT version of ``path`` cannot be determined.
        ModNotFoundError: a name in ``names`` matches no active mod.
        AmbiguousModError: a name in ``names`` matches several mods.
        ForgeUnavailableError: the Forge API is unreachable.
        ForgeApiError: the Forge API answered with an error envelope.
        GitError: git failed.
    """
    if client is not None:
        return _query_updates(path, names, db_path, client)
    with ForgeClient() as owned:
        return _query_updates(path, names, db_path, owned)


def update_mod(
    path: Path,
    name: str,
    force: bool = False,
    progress: Progress | None = None,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> UpdateResult:
    """Update the active mod ``name`` in ``path`` to the recommended version.

    Takes the update :func:`check_updates` reports for the mod, downloads its
    archive and replaces the installed state in a single commit
    ``mod: <name> <old> → <new>``: the files of the old version the new one no
    longer provides are deleted, the new files are copied in, and the commit
    carries the full trailer set with ``Mod-State: updated``,
    ``Mod-Previous-Version`` set to the old version, ``Mod-Source`` set to the
    download URL and the Forge IDs of the new version. Files below
    ``BepInEx/config/`` are never touched, so the settings of the mod survive
    the update.

    A mod Forge reports no applicable update for is left untouched and reported
    as a no-op through ``notice``, which also covers a blocked update and a mod
    without a version for the installed SPT release.

    Args:
        path: The tracked game directory.
        name: The mod to update, matched case-insensitively.
        force: Update despite conflicts, missing dependencies or an
            incompatible SPT version.
        progress: Optional callback for download and install progress.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        client: Forge API client; a default one is built when omitted.

    Returns:
        The old and new version with the commit and its files, or a no-op
        result carrying a notice.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory or its SPT version
            cannot be determined.
        ModNotFoundError: no active mod matches ``name``.
        AmbiguousModError: several mods match ``name``.
        ArchiveError: the update has no download link, or its archive is
            incomplete, unreadable or unsafe.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ConflictError: a file or DLL conflict blocks the update and ``force``
            is not set.
        DependencyError: a dependency check blocks the update and ``force`` is
            not set.
        ModFileWriteError: a file of the new version could not be written
            into the game directory.
        ForgeUnavailableError: the Forge API or the download URL is
            unreachable.
        ForgeApiError: the Forge API answered with an error envelope.
        GitError: git failed.
    """
    if client is not None:
        return _apply_update(path, name, force, progress, db_path, client)
    with ForgeClient() as owned:
        return _apply_update(path, name, force, progress, db_path, owned)


def _query_updates(
    path: Path,
    names: Sequence[str] | None,
    db_path: Path | None,
    client: ForgeClient,
) -> UpdatesReport:
    version = required_spt_version(path)
    active = list_mods(path)
    if names is not None:
        active = [resolve_mod(active, name) for name in names]

    identified: dict[str, ModInfo] = {}
    skipped: list[str] = []
    with Catalog(db_path) as catalog:
        for mod in active:
            cached = None if mod.forge_id else catalog.find_mod(mod.name)
            forge_id = mod.forge_id or (str(cached.id) if cached else "")
            if not forge_id:
                skipped.append(mod.name)
                continue
            identified[forge_id] = mod

    if not identified:
        return UpdatesReport(spt_version=version, skipped=skipped)
    result = client.updates(
        [(forge_id, mod.version) for forge_id, mod in identified.items()], version
    )
    return _updates_report(result, identified, skipped)


def _updates_report(
    result: UpdatesResult, identified: Mapping[str, ModInfo], skipped: Sequence[str]
) -> UpdatesReport:
    """Sort the four lists of the updates endpoint into an ``UpdatesReport``.

    Every entry is named after the installed mod its Forge ID belongs to, so
    the report speaks about the mods the game directory knows.
    """

    def name_of(mod_id: int, fallback: str) -> str:
        mod = identified.get(str(mod_id))
        return mod.name if mod is not None else fallback

    return UpdatesReport(
        spt_version=result.spt_version,
        available=[
            UpdateCandidate(
                name=name_of(
                    update.current_version.mod_id, update.current_version.name
                ),
                installed_version=update.current_version.version,
                new_version=update.recommended_version.version,
                version_id=update.recommended_version.id,
                link=update.recommended_version.link or "",
                content_length=update.recommended_version.content_length,
                reason=update.update_reason,
            )
            for update in result.updates
        ],
        blocked=[
            BlockedUpdate(
                name=name_of(
                    blocked.current_version.mod_id, blocked.current_version.name
                ),
                installed_version=blocked.current_version.version,
                new_version=blocked.latest_version.version,
                reason=blocked.block_reason,
                blocking_mods=[mod.mod_name for mod in blocked.blocking_mods],
            )
            for blocked in result.blocked_updates
        ],
        up_to_date=[name_of(mod.mod_id, mod.name) for mod in result.up_to_date],
        incompatible=[
            IncompatibleMod(
                name=name_of(mod.mod_id, mod.name),
                installed_version=mod.version,
                reason=mod.reason,
                latest_compatible_version=mod.latest_compatible_version or "",
            )
            for mod in result.incompatible_with_spt
        ],
        skipped=list(skipped),
    )


def _apply_update(
    path: Path,
    name: str,
    force: bool,
    progress: Progress | None,
    db_path: Path | None,
    client: ForgeClient,
) -> UpdateResult:
    game_layout = detect_layout(path)
    repo = Repo(path)
    active = [mod for mod in current_mods(repo) if mod.state in ACTIVE_STATES]
    mod = resolve_mod(active, name)
    ensure_installable(repo)

    report = _query_updates(path, [mod.name], db_path, client)
    candidate = next(
        (entry for entry in report.available if entry.name == mod.name), None
    )
    if candidate is None:
        return UpdateResult(
            name=mod.name,
            previous_version=mod.version,
            notice=_no_update_notice(mod, report),
        )

    reporter = prefixed_progress(progress, f"{mod.name} {candidate.new_version}")
    obsolete = _replaceable_files(repo, mod)

    with tempfile.TemporaryDirectory(prefix="sptmod-", dir=staging_dir()) as temp_name:
        temp = Path(temp_name)
        report_step(reporter, InstallStep.CHECK, "downloading the new version")
        archive = download_entry(
            client,
            candidate.link,
            candidate.content_length,
            candidate.version_id,
            mod.name,
            reporter,
        )
        unpacked = temp / "unpacked"
        unpacked.mkdir()
        report_step(reporter, InstallStep.EXTRACT, f"extracting {archive.name}")
        extract(archive, unpacked)

        report_step(reporter, InstallStep.ANCHOR, "searching the mod anchor")
        layout = for_game_layout(find_anchor(unpacked), game_layout)

        report_step(reporter, InstallStep.METADATA, "reading the mod metadata")
        meta = read_meta(
            layout, game_layout.mods_dir, mod.name, candidate.new_version, archive
        )

        with game_lock(path, f"updating {mod.name}", reporter):
            ensure_installable(repo)

            report_step(
                reporter, InstallStep.CONFLICTS, "checking conflicts and dependencies"
            )
            warnings = _check_update_conflicts(
                repo, game_layout, layout, meta, mod, force
            )

            removed = [file for file in obsolete if file not in set(layout.files)]
            report_step(
                reporter, InstallStep.COPY, f"copying {len(layout.files)} files"
            )
            write_mod_files(path, layout, removed)
            for directory in sorted(
                {(path / file).parent for file in removed}, reverse=True
            ):
                prune_empty(path, directory, mod_prefixes(game_layout))

            report_step(
                reporter,
                InstallStep.COMMIT,
                f"committing {mod.name} {candidate.new_version}",
            )
            draft = replace(
                mod,
                version=candidate.new_version,
                mod_type=layout.mod_type,
                author=meta.author,
                spt_range=meta.spt_range,
                state=ModState.UPDATED,
                forge_version_id=str(candidate.version_id),
                guid=meta.guid,
                source=candidate.link,
            )
            commit_hash = repo.commit(
                f"mod: {mod.name} {mod.version} → {candidate.new_version}",
                trailers={
                    **build_trailers(draft),
                    TRAILER_MOD_PREVIOUS_VERSION: mod.version,
                },
                paths=[*removed, *layout.files],
            )

    report_step(
        reporter,
        InstallStep.COMMIT,
        f"updated {mod.name} to {candidate.new_version}",
        percent=100,
    )
    return UpdateResult(
        name=mod.name,
        previous_version=mod.version,
        version=candidate.new_version,
        commit=commit_hash,
        files=repo.show_files(commit_hash),
        warnings=warnings,
    )


def _replaceable_files(repo: Repo, mod: ModInfo) -> list[Path]:
    """Return the files of ``mod`` an update may delete.

    These are the files its youngest descriptive commit added, without the
    BepInEx configs, which an update never touches.
    """
    return [
        entry.path
        for entry in repo.show_files(install_commit(repo, mod.name))
        if entry.change != "deleted"
        and not entry.path.is_relative_to(BEPINEX_CONFIG_DIR)
    ]


def _check_update_conflicts(
    repo: Repo,
    game_layout: GameLayout,
    layout: ArchiveLayout,
    meta: ModMeta,
    mod: ModInfo,
    force: bool,
) -> list[Conflict]:
    """Run the install checks of an update, ignoring the mod's own files.

    The new version overwrites the files of the old one by design, so file
    conflicts with the mod being updated are not findings.
    """
    active = [
        other
        for other in current_mods(repo)
        if other.state in ACTIVE_STATES and other.name != mod.name
    ]
    findings = apply_force(
        [
            *[
                finding
                for finding in check_file_conflicts(repo, layout.files)
                if finding.mod != mod.name
            ],
            *check_dll_duplicates(repo.path, layout.files),
            *check_dependencies(active, meta),
            *check_spt_compat(
                resolve_spt_version(game_layout, repo) or "", meta.spt_range
            ),
        ],
        force,
    )
    blocking = [finding for finding in findings if finding.severity == "error"]
    files = [finding for finding in blocking if finding.kind not in DEPENDENCY_KINDS]
    if files:
        raise ConflictError(blocked_message(mod.name, blocking), findings)
    if blocking:
        raise DependencyError(blocked_message(mod.name, blocking), findings)
    return findings


def _no_update_notice(mod: ModInfo, report: UpdatesReport) -> str:
    """Explain why an update of ``mod`` is a no-op."""
    blocked = next((entry for entry in report.blocked if entry.name == mod.name), None)
    if blocked is not None:
        blockers = ", ".join(blocked.blocking_mods)
        return (
            f"{mod.name} {mod.version} cannot be updated to "
            f"{blocked.new_version}: {blocked.reason}"
            + (f" (blocked by {blockers})" if blockers else "")
        )
    incompatible = next(
        (entry for entry in report.incompatible if entry.name == mod.name), None
    )
    if incompatible is not None:
        return (
            f"{mod.name} {mod.version} has no version for SPT "
            f"{report.spt_version}: {incompatible.reason}"
        )
    if mod.name in report.up_to_date:
        return f"{mod.name} {mod.version} is up to date"
    return f"Forge reports no update for {mod.name} {mod.version}"
