"""Local mod sources: project folders a player builds mods in.

A folder is registered once and offers every mod its ``build`` folder holds;
installing takes one of those mods and puts its newest build into the game
directory. sptmod never runs a build and stores nothing about what a folder
offers -- the archives on disk are the answer, read whenever it is asked.
A mod installed this way is never linked to a Forge mod: a fork must not be
overwritten by the release of the mod it was forked from.
"""

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sptmod.core.archive import SUPPORTED_SUFFIXES, extract, find_anchor
from sptmod.core.errors import (
    ArchiveError,
    InvalidPathError,
    LocalSourceNotBuiltError,
    LocalSourceNotFoundError,
)
from sptmod.core.game_dir import GameLayout, detect_layout
from sptmod.core.localsources import (
    LocalSource,
    default_local_sources_path,
    load_sources,
    save_sources,
    source_key,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    Conflict,
    ModInfo,
    Progress,
)
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.services.common import for_game_layout
from sptmod.core.services.install_archive import install_from_archive, read_meta
from sptmod.core.services.lifecycle import remove_mod
from sptmod.core.services.state import list_mods
from sptmod.core.workspace import staging_dir

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.

BUILD_DIR_NAME: Final = "build"

_ADDED_AT_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class OfferedMod:
    """One mod a project folder offers, in its newest build.

    ``name`` and ``version`` are what the archive itself declares, the same
    source the install commit takes them from; ``mod`` is the mod this folder
    installed from this build, in whatever state the installation has it, and
    empty while there is none.
    """

    name: str
    version: str
    archive: Path
    built_at: datetime
    mod: ModInfo | None = None


@dataclass(frozen=True)
class LocalSourceInfo:
    """A registered project folder with the mods it offers right now.

    ``mods`` is empty while the folder has built nothing sptmod can read as a
    mod -- a normal state, not a failure. ``installed`` holds the mods this
    folder put into the game directory, including those it offers no build of
    any more: those are the mods removing the folder uninstalls.
    """

    folder: Path
    added_at: str
    mods: list[OfferedMod] = field(default_factory=list[OfferedMod])
    installed: list[ModInfo] = field(default_factory=list[ModInfo])


@dataclass(frozen=True)
class LocalInstallResult:
    """Outcome of :func:`install_local_source`.

    ``replaced`` says whether an earlier build of this mod was taken out
    first; ``warnings`` holds the non-blocking findings of the install.
    """

    mod: ModInfo
    archive: Path
    replaced: bool = False
    files: list[Path] = field(default_factory=list[Path])
    warnings: list[Conflict] = field(default_factory=list[Conflict])


def add_local_source(
    path: Path, folder: Path, *, store_path: Path | None = None
) -> list[LocalSourceInfo]:
    """Register the project folder ``folder`` as a local mod source.

    Registering a folder that is already registered changes nothing. The
    folder is only remembered, never built and never read beyond its ``build``
    folder.

    Args:
        path: The game directory the listing is reported for.
        folder: The project folder to register.
        store_path: Local source file; defaults to
            :func:`default_local_sources_path`.

    Returns:
        Every registered folder with the mods it offers.

    Raises:
        InvalidPathError: ``folder`` does not exist or is no directory.
        InvalidMetadataError: the stored folders cannot be read.
        NotAGameDirError: ``path`` is no SPT game directory.
        GitError: git failed.
        OSError: the local source file cannot be written.
    """
    if not folder.is_dir():
        raise InvalidPathError(f"{folder} is no folder")
    store = store_path or default_local_sources_path()
    sources = load_sources(store)
    if _find(sources, folder) is None:
        resolved = Path(source_key(folder))
        sources = [*sources, LocalSource(folder=resolved, added_at=_now())]
        save_sources(store, sources)
    return _enriched(path, sources)


def list_local_sources(
    path: Path, *, store_path: Path | None = None
) -> list[LocalSourceInfo]:
    """Return the registered project folders and the mods each of them offers.

    Every archive in a folder's ``build`` folder is read for the mod name and
    version it declares; an archive sptmod cannot read as a mod is not offered.
    Several builds of the same mod collapse into the most recently changed one.

    Args:
        path: The game directory the installed mods are read from.
        store_path: Local source file; defaults to
            :func:`default_local_sources_path`.

    Returns:
        Every registered folder with the mods it offers.

    Raises:
        InvalidMetadataError: the stored folders cannot be read.
        NotAGameDirError: ``path`` is no SPT game directory.
        GitError: git failed.
    """
    store = store_path or default_local_sources_path()
    return _enriched(path, load_sources(store))


def remove_local_source(
    path: Path, folder: Path, *, store_path: Path | None = None
) -> list[LocalSourceInfo]:
    """Stop treating ``folder`` as a local mod source and uninstall its mods.

    Every mod installed from this folder is taken out of the game directory
    first: nothing that came from the folder is left behind with no record of
    where it came from.

    Raises:
        LocalSourceNotFoundError: ``folder`` is not registered.
        InvalidMetadataError: the stored folders cannot be read.
        NotAGameDirError: ``path`` is no SPT game directory.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        OperationInProgressError: another operation holds the game directory.
        GitError: git failed.
        OSError: the local source file cannot be written.
    """
    store = store_path or default_local_sources_path()
    sources = load_sources(store)
    source = _require(sources, folder)
    for installed in _source_mods(list_mods(path), source.folder):
        remove_mod(path, installed.name)
    remaining = [item for item in sources if item is not source]
    save_sources(store, remaining)
    return _enriched(path, remaining)


def install_local_source(
    path: Path,
    folder: Path,
    mod: str,
    progress: Progress | None = None,
    *,
    store_path: Path | None = None,
) -> LocalInstallResult:
    """Install the newest build of the mod ``mod`` of the folder ``folder``.

    The build is the most recently changed archive of that mod in the folder's
    ``build`` folder, so a rebuild is installed without anyone naming a file.
    Where the mod is currently active, it is taken out first and the new build
    installed after it: what the folder offers now replaces what is installed,
    whether or not the version, the name, the file names or the file count
    changed. The mod is recorded as coming from this folder, so removing the
    folder later uninstalls it again. The
    installed mod is never linked to a Forge mod, so a later update never
    offers to overwrite a fork with the release it was forked from.

    Args:
        path: The tracked game directory.
        folder: The registered project folder.
        mod: The name of the mod to install, matched case-insensitively
            against the names the offered archives declare.
        progress: Optional callback for the phases of the install.
        store_path: Local source file; defaults to
            :func:`default_local_sources_path`.

    Returns:
        The installed mod, its files, the warnings of the install, the archive
        it came from and whether an earlier build was replaced.

    Raises:
        LocalSourceNotFoundError: ``folder`` is not registered.
        LocalSourceNotBuiltError: the folder offers no mod of that name.
        InvalidMetadataError: the stored folders cannot be read.
        NotAGameDirError: ``path`` is no SPT game directory.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        OperationInProgressError: another operation holds the game directory.
        ArchiveError: the build cannot be read or holds no mod file.
        ConflictError: the build conflicts with another installed mod.
        GitError: git failed.
    """
    store = store_path or default_local_sources_path()
    source = _require(load_sources(store), folder)
    offered = _offered_mods(source.folder, detect_layout(path))
    wanted = mod.strip().casefold()
    build = next((item for item in offered if item.name.casefold() == wanted), None)
    if build is None:
        raise LocalSourceNotBuiltError(
            f"{source.folder} has not built {mod!r}: there is no archive of it "
            f"in its {BUILD_DIR_NAME} folder"
        )
    paired = _paired_mods(offered, list_mods(path), source.folder)
    installed = paired.get(build.name.casefold())
    if installed is not None:
        remove_mod(path, installed.name)
    result = install_from_archive(
        path, build.archive, progress=progress, source=source_key(source.folder)
    )
    return LocalInstallResult(
        mod=result.mod,
        archive=build.archive,
        replaced=installed is not None,
        files=result.files,
        warnings=result.warnings,
    )


def _now() -> str:
    return datetime.now(UTC).strftime(_ADDED_AT_FORMAT)


def _find(sources: Sequence[LocalSource], folder: Path) -> LocalSource | None:
    """Return the registered source for ``folder``, by resolved path."""
    wanted = source_key(folder)
    for source in sources:
        if source_key(source.folder) == wanted:
            return source
    return None


def _require(sources: Sequence[LocalSource], folder: Path) -> LocalSource:
    """Return the registered source for ``folder``.

    Raises:
        LocalSourceNotFoundError: the folder is not registered.
    """
    source = _find(sources, folder)
    if source is None:
        raise LocalSourceNotFoundError(f"{folder} is no local mod folder of sptmod")
    return source


def _build_archives(folder: Path) -> list[Path]:
    """Return the mod archives directly inside the folder's build folder.

    Only the files directly inside it count, so what a folder offers stays
    predictable no matter what a build tool leaves in subfolders.
    """
    build = folder / BUILD_DIR_NAME
    if not build.is_dir():
        return []
    return [
        child
        for child in sorted(build.iterdir())
        if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES
    ]


def _offered_mods(folder: Path, layout: GameLayout) -> list[OfferedMod]:
    """Return the mods ``folder`` offers, newest build per mod.

    Every archive is read the way an install reads it, so the name a mod is
    offered under is the name its install commit would carry. Two archives
    declaring the same name are two builds of one mod, and the most recently
    changed of them wins. An archive that is no readable mod is passed over:
    a build folder regularly holds other files, and a listing must not fail
    over one of them.
    """
    newest: dict[str, OfferedMod] = {}
    for archive in _build_archives(folder):
        build = _read_offer(archive, layout)
        if build is None:
            continue
        known = newest.get(build.name.casefold())
        if known is None or build.built_at >= known.built_at:
            newest[build.name.casefold()] = build
    return sorted(newest.values(), key=lambda build: build.name.casefold())


def _read_offer(archive: Path, layout: GameLayout) -> OfferedMod | None:
    """Return the mod ``archive`` declares, or None when it declares none."""
    try:
        with tempfile.TemporaryDirectory(prefix="sptmod-", dir=staging_dir()) as name:
            temp = Path(name)
            extract(archive, temp)
            extracted = for_game_layout(find_anchor(temp), layout)
            meta = read_meta(extracted, layout.mods_dir, None, None, archive)
    except (ArchiveError, InvalidMetadataError):
        return None
    if not meta.name:
        return None
    return OfferedMod(
        name=meta.name,
        version=meta.version,
        archive=archive,
        built_at=datetime.fromtimestamp(archive.stat().st_mtime, UTC),
    )


def _source_mods(mods: Sequence[ModInfo], folder: Path) -> list[ModInfo]:
    """Return the mods that were installed from ``folder``."""
    key = source_key(folder)
    return [mod for mod in mods if mod.source == key]


def _paired_mods(
    builds: Sequence[OfferedMod], mods: Sequence[ModInfo], folder: Path
) -> dict[str, ModInfo]:
    """Return the installed mod of each offered build, by case-folded name.

    Only mods installed from ``folder`` are candidates, so a build never pairs
    with a mod of the same name that came from somewhere else. A build pairs
    with the mod of its name where there is one; where a single build and a
    single mod of this folder are left over, those two are the same mod under
    a new name, which is how a renamed mod is replaced instead of duplicated.
    """
    candidates = _source_mods(mods, folder)
    paired: dict[str, ModInfo] = {}
    for build in builds:
        wanted = build.name.casefold()
        match = next((mod for mod in candidates if mod.name.casefold() == wanted), None)
        if match is not None:
            paired[wanted] = match
    open_builds = [build for build in builds if build.name.casefold() not in paired]
    open_mods = [mod for mod in candidates if mod not in paired.values()]
    if len(open_builds) == 1 and len(open_mods) == 1:
        paired[open_builds[0].name.casefold()] = open_mods[0]
    return paired


def _source_info(
    source: LocalSource, layout: GameLayout, mods: Sequence[ModInfo]
) -> LocalSourceInfo:
    """Return one source with the mods it offers and the mods it installed."""
    builds = _offered_mods(source.folder, layout)
    paired = _paired_mods(builds, mods, source.folder)
    active = [mod for mod in mods if mod.state in ACTIVE_STATES]
    return LocalSourceInfo(
        folder=source.folder,
        added_at=source.added_at,
        mods=[
            OfferedMod(
                name=build.name,
                version=build.version,
                archive=build.archive,
                built_at=build.built_at,
                mod=paired.get(build.name.casefold()),
            )
            for build in builds
        ],
        installed=_source_mods(active, source.folder),
    )


def _enriched(path: Path, sources: Sequence[LocalSource]) -> list[LocalSourceInfo]:
    """Return every source with the mods it offers and their installed state."""
    layout = detect_layout(path)
    mods = list_mods(path, include_all=True)
    return [_source_info(source, layout, mods) for source in sources]
