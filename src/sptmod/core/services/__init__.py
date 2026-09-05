"""Service layer: the operations behind the CLI and the RPC adapter."""

import os.path
import shutil
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from sptmod.core.errors import (
    AlreadyInitializedError,
    ConflictError,
    GitError,
    InvalidPathError,
    ModNotFoundError,
    NotAGameDirError,
    SptVersionUnknownError,
)
from sptmod.core.game_dir import (
    PLUGINS_DIR,
    GameLayout,
    detect_layout,
    is_game_dir,
    is_spt_own_path,
    read_core_json_version,
    read_pe_file_version,
    resolve_spt_version,
    store_spt_version,
    validate_game_dir,
)
from sptmod.core.launch import (
    LaunchResult,
    LaunchStatus,
    StopResult,
)
from sptmod.core.launch import launch_profile as _launch_profile
from sptmod.core.launch import launch_status as _launch_status
from sptmod.core.launch import stop_game as _stop_game
from sptmod.core.models import (
    ACTIVE_STATES,
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    ModInfo,
    ModState,
    Progress,
)
from sptmod.core.modmeta import (
    PACKAGE_JSON_NAME,
    InvalidMetadataError,
    ModMeta,
    build_trailers,
    meta_from_dll,
    read_package_json,
)
from sptmod.core.repo import Repo
from sptmod.core.services.catalog import (
    BrowseFilter,
    ClearCacheResult,
    ModDetails,
    browse_catalog,
    clear_catalog_cache,
    get_mod_details,
    list_favorites,
    search_catalog,
    set_favorite,
    sync_catalog,
)
from sptmod.core.services.common import (
    INSTALL_STEPS,
    UNSET_DATE,
    InstallStep,
    current_mods,
    install_commit,
    last_touching_mod,
    mod_prefix_of,
    owned_prefixes,
    owning_mod_dir,
    prune_empty,
    resolve_mod,
)
from sptmod.core.services.install_archive import (
    InstallResult,
    install_from_archive,
    reject_multiline_metadata,
)
from sptmod.core.services.install_forge import (
    InstallModResult,
    install_mod,
    plan_mod_install,
)
from sptmod.core.services.profiles import (
    CleanupPlan,
    CleanupResult,
    UntouchedResidue,
    check_profile_residue,
    check_profiles,
    clean_profile,
    clean_profiles_inline,
    list_profiles,
    plan_profile_cleanup,
)
from sptmod.core.services.state import (
    ManualMod,
    SnapshotResult,
    StatusReport,
    ensure_installable,
    history,
    list_mods,
    mod_files,
    snapshot,
    status,
)
from sptmod.core.services.transfer import (
    EXPORT_FORMATS,
    ExportEntry,
    ExportFormat,
    ImportDiff,
    ImportEntry,
    export_mods,
    import_list,
)
from sptmod.core.services.update import (
    BlockedUpdate,
    IncompatibleMod,
    UpdateCandidate,
    UpdateResult,
    UpdatesReport,
    check_updates,
    update_mod,
)
from sptmod.core.workspace import (
    game_lock,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.
__all__ = [
    "ACTIVE_STATES",
    "AdoptResult",
    "BlockedUpdate",
    "BrowseFilter",
    "CleanupPlan",
    "CleanupResult",
    "ClearCacheResult",
    "DirectoryEntry",
    "DirectoryListing",
    "EXPORT_FORMATS",
    "ExportEntry",
    "ExportFormat",
    "INSTALL_STEPS",
    "ImportDiff",
    "ImportEntry",
    "IncompatibleMod",
    "InitResult",
    "InstallModResult",
    "InstallResult",
    "InstallStep",
    "InvalidPathError",
    "LaunchResult",
    "LaunchStatus",
    "LocatedDirectory",
    "ManualMod",
    "ModDetails",
    "RemoveResult",
    "SPT_VERSION_TRAILER",
    "SnapshotResult",
    "StatusReport",
    "StopResult",
    "ToggleResult",
    "UNKNOWN_SPT_VERSION",
    "UntouchedResidue",
    "UpdateCandidate",
    "UpdateResult",
    "UpdatesReport",
    "adopt_mod",
    "browse_catalog",
    "check_profiles",
    "check_updates",
    "clean_profile",
    "clear_catalog_cache",
    "disable_mod",
    "enable_mod",
    "export_mods",
    "get_mod_details",
    "history",
    "import_list",
    "init_game_dir",
    "install_from_archive",
    "install_mod",
    "launch_profile",
    "launch_status",
    "list_directories",
    "list_favorites",
    "list_mods",
    "list_profiles",
    "locate_directory",
    "mod_files",
    "plan_mod_install",
    "plan_profile_cleanup",
    "remove_mod",
    "search_catalog",
    "set_favorite",
    "set_spt_version",
    "snapshot",
    "status",
    "stop_game",
    "sync_catalog",
    "update_mod",
]

SPT_VERSION_TRAILER: Final = "Spt-Version"
UNKNOWN_SPT_VERSION: Final = "unknown"


_LOCATE_MOUNTS: Final[tuple[str, ...]] = ("/mnt", "/media", "/opt")
_LOCATE_SKIP_NAMES: Final[frozenset[str]] = frozenset(
    {"node_modules", "__pycache__", "proc", "sys", "dev"}
)
_LOCATE_MAX_DEPTH: Final = 5
_LOCATE_MAX_RESULTS: Final = 10
_LOCATE_TIME_BUDGET: Final = 5.0


@dataclass(frozen=True)
class InitResult:
    """Outcome of :func:`init_game_dir`.

    ``pre_existing_mods`` lists every mod found in the untracked game
    directory, ``adopted`` the ones the ``adopt`` selection turned into
    install commits. ``warnings`` carries the layout detection notes, for
    instance when both runtime directories exist.
    """

    game_dir: Path
    commit: str
    spt_version: str
    warnings: list[str] = field(default_factory=list[str])
    pre_existing_mods: list[Path] = field(default_factory=list[Path])
    adopted: list[ModInfo] = field(default_factory=list[ModInfo])


@dataclass(frozen=True)
class RemoveResult:
    """Outcome of :func:`remove_mod`.

    ``warnings`` holds db file read/parse errors from ID collection, each
    naming the file and the error. ``cleanup`` reports the profile cleanup
    that ran with the removal, and is absent when none was asked for.
    """

    name: str
    commit: str
    files: list[Path] = field(default_factory=list[Path])
    warnings: list[str] = field(default_factory=list[str])
    cleanup: CleanupResult | None = None


@dataclass(frozen=True)
class ToggleResult:
    """Outcome of :func:`disable_mod` and :func:`enable_mod`.

    A no-op leaves ``commit`` and ``files`` empty and explains itself in
    ``notice``; ``state`` then holds the state the mod already had.
    """

    name: str
    state: ModState
    commit: str = ""
    files: list[Path] = field(default_factory=list[Path])
    notice: str = ""


@dataclass(frozen=True)
class AdoptResult:
    """Outcome of :func:`adopt_mod`."""

    mod: ModInfo
    files: list[Path] = field(default_factory=list[Path])


@dataclass(frozen=True)
class DirectoryEntry:
    """One subdirectory of a browsed directory."""

    name: str
    path: Path
    is_game_dir: bool


@dataclass(frozen=True)
class DirectoryListing:
    """The subdirectories of one directory, for a folder picker.

    ``parent`` is None at the filesystem root, where browsing cannot go up.
    """

    path: Path
    parent: Path | None
    entries: list[DirectoryEntry] = field(default_factory=list[DirectoryEntry])


@dataclass(frozen=True)
class LocatedDirectory:
    """A directory the fingerprint search matched."""

    path: Path
    is_game_dir: bool


def list_directories(path: str | Path | None = None) -> DirectoryListing:
    """List the subdirectories of ``path`` for a folder picker.

    Without ``path`` the home directory of the current user is listed. Hidden
    directories are skipped, files are not reported, and the entries are
    sorted by name.

    Raises:
        InvalidPathError: ``path`` does not exist, is no directory or cannot
            be read.
    """
    target = Path(path).expanduser() if path is not None else Path.home()
    try:
        resolved = target.resolve()
        if not resolved.exists():
            raise InvalidPathError(f"{resolved} does not exist")
        if not resolved.is_dir():
            raise InvalidPathError(f"{resolved} is not a directory")
        children = sorted(
            (child for child in resolved.iterdir() if _is_listable_dir(child)),
            key=lambda child: child.name.casefold(),
        )
    except OSError as error:
        raise InvalidPathError(f"{target} cannot be read: {error}") from error
    return DirectoryListing(
        path=resolved,
        parent=resolved.parent if resolved.parent != resolved else None,
        entries=[
            DirectoryEntry(
                name=child.name, path=child, is_game_dir=_looks_like_game_dir(child)
            )
            for child in children
        ],
    )


def locate_directory(
    name: str,
    fingerprint: Sequence[str],
    *,
    roots: Sequence[Path] | None = None,
) -> list[LocatedDirectory]:
    """Find directories named ``name`` that contain every ``fingerprint`` entry.

    The browser hands a folder picker's selection over as a folder name plus a
    few of its entry names; this maps that back to absolute paths. Searched are
    the home directory and the mount points ``/mnt``, ``/media`` and ``/opt``
    where they exist, down to :data:`_LOCATE_MAX_DEPTH` levels, skipping hidden
    and notoriously deep directories. The search stops after
    :data:`_LOCATE_MAX_RESULTS` matches or :data:`_LOCATE_TIME_BUDGET` seconds.

    ``roots`` replaces those default search roots with the given directories.

    Raises:
        InvalidPathError: ``name`` is empty or contains a path separator.
    """
    if not name or "/" in name or "\\" in name:
        raise InvalidPathError(f"{name!r} is not a plain directory name")
    deadline = time.monotonic() + _LOCATE_TIME_BUDGET
    found: list[LocatedDirectory] = []
    seen: set[Path] = set()
    search_roots = (
        _locate_roots() if roots is None else [root for root in roots if root.is_dir()]
    )
    for root in search_roots:
        for candidate in _walk_for_name(root, name, deadline):
            if candidate in seen:
                continue
            seen.add(candidate)
            if not _matches_fingerprint(candidate, fingerprint):
                continue
            found.append(
                LocatedDirectory(
                    path=candidate, is_game_dir=_looks_like_game_dir(candidate)
                )
            )
            if len(found) >= _LOCATE_MAX_RESULTS:
                return found
    return found


def _is_listable_dir(path: Path) -> bool:
    return not path.name.startswith(".") and os.path.isdir(path)


def _looks_like_game_dir(path: Path) -> bool:
    """Answer :func:`is_game_dir` for a directory that may be unreadable."""
    try:
        return is_game_dir(path)
    except OSError:
        return False


def _locate_roots() -> list[Path]:
    roots = [Path.home(), *(Path(mount) for mount in _LOCATE_MOUNTS)]
    return [root for root in roots if root.is_dir()]


def _walk_for_name(root: Path, name: str, deadline: float) -> Iterator[Path]:
    """Yield directories named ``name`` below ``root``, breadth-first."""
    level = [root]
    for _ in range(_LOCATE_MAX_DEPTH):
        if not level or time.monotonic() > deadline:
            return
        children: list[Path] = []
        for directory in level:
            try:
                entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                if not _is_searchable_dir(entry):
                    continue
                if entry.name == name:
                    yield entry
                else:
                    children.append(entry)
        level = children


def _is_searchable_dir(path: Path) -> bool:
    return (
        not path.name.startswith(".")
        and path.name not in _LOCATE_SKIP_NAMES
        and os.path.isdir(path)
        and not os.path.islink(path)
    )


def _matches_fingerprint(candidate: Path, fingerprint: Sequence[str]) -> bool:
    return all(os.path.exists(candidate / entry) for entry in fingerprint)


def init_game_dir(
    path: Path,
    adopt: Sequence[Path] | bool = False,
    spt_version: str | None = None,
) -> InitResult:
    """Turn the SPT installation at ``path`` into a tracked repository.

    Detects the layout, determines the SPT version, writes the matching
    whitelist ``.gitignore``, runs ``git init`` and creates the initial commit
    ``chore: clean SPT <version>`` carrying the ``Spt-Version`` trailer.

    ``spt_version`` is the version a user supplies; it wins over the detection
    chain and is stored in the repository for later runs. Without it the
    version comes from :func:`~sptmod.core.game_dir.resolve_spt_version`, and
    when that finds nothing the call fails before anything is written, so the
    caller can ask for a version and repeat the init.

    The initial commit holds only ``.gitignore``, the tracked profiles and the
    BepInEx configs. Mod content that already exists below the server mod
    directory and ``BepInEx/plugins`` stays untracked and is returned as
    ``pre_existing_mods`` (one path per mod folder or loose plugin file,
    relative to the game directory) so callers can offer them for adoption.

    ``adopt`` selects which of those mods become install commits through
    :func:`adopt_mod`: ``False`` adopts nothing, ``True`` adopts all of them,
    and a sequence of paths adopts exactly those, which is how a caller passes
    back the entries a user confirmed.

    A repository this call created is removed again when a later step fails, and
    a repository without any commit, left behind by an interrupted init, is
    continued instead of refused.

    Raises:
        NotAGameDirError: ``path`` is missing, is no directory or lacks a
            runtime directory or ``BepInEx/``.
        SptVersionUnknownError: no SPT version was given and none could be
            detected.
        InvalidMetadataError: a mod selected for adoption has no usable
            metadata.
        GitError: ``path`` is an initialized repository with commits, or git
            failed.
    """
    layout = validate_game_dir(path)
    version = (spt_version or "").strip() or _detect_spt_version(layout)
    if not version:
        raise SptVersionUnknownError(
            f"the SPT version of {path} cannot be detected, pass it explicitly"
        )

    with game_lock(path, "setting up the game folder"):
        repo, created = _open_for_init(layout)
        try:
            if spt_version:
                store_spt_version(repo, version)
            untracked = repo.untracked_under(Path("."))
            baseline = [
                entry for entry in untracked if mod_prefix_of(entry, layout) is None
            ]
            commit_hash = repo.commit(
                f"chore: clean SPT {version}",
                trailers={SPT_VERSION_TRAILER: version},
                paths=baseline,
            )
            pre_existing = _mod_entries(untracked, layout)
            selected = (pre_existing if adopt else []) if isinstance(adopt, bool) else list(adopt)
            adopted = [adopt_mod_at(path, entry).mod for entry in selected]
        except Exception:
            if created:
                shutil.rmtree(path / ".git", ignore_errors=True)
            raise

    return InitResult(
        game_dir=path,
        commit=commit_hash,
        spt_version=version,
        warnings=[layout.warning] if layout.warning else [],
        pre_existing_mods=pre_existing,
        adopted=adopted,
    )


def _open_for_init(layout: GameLayout) -> tuple[Repo, bool]:
    """Return the repository to initialize into and whether this call created it.

    An interrupted init leaves a repository without any commit behind; that one
    is reused and completed, while a repository with commits is refused.

    Raises:
        GitError: the game directory is an initialized repository with commits.
    """
    if not (layout.root / ".git").exists():
        return Repo.init(layout), True
    repo = Repo(layout.root)
    if repo.log():
        raise AlreadyInitializedError(
            f"{layout.root} is already an initialized sptmod repository"
        )
    repo.configure(layout)
    return repo, False


def _detect_spt_version(layout: GameLayout) -> str | None:
    """Return the detected SPT version of ``layout`` before any git init.

    The stored answer of an earlier run is only part of the chain once the game
    directory is a repository.
    """
    if (layout.root / ".git").exists():
        return resolve_spt_version(layout, Repo(layout.root))
    return read_core_json_version(layout) or read_pe_file_version(
        layout.root / layout.server_exe
    )


def set_spt_version(path: Path, version: str) -> str:
    """Store ``version`` as the SPT version of the game directory ``path``.

    The answer a user gives when neither the core config nor the server
    executable reveals a version. It is kept in the local git config of the
    game repository, so :func:`~sptmod.core.game_dir.resolve_spt_version`
    picks it up on every later run.

    Args:
        path: The game directory.
        version: The SPT version to store, for example ``4.1.2``.

    Returns:
        The stored version, stripped of surrounding whitespace.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        ValueError: ``version`` is empty.
        GitError: ``path`` is no git repository or git failed.
    """
    validate_game_dir(path)
    cleaned = version.strip()
    if not cleaned:
        raise ValueError("the SPT version must not be empty")
    store_spt_version(Repo(path), cleaned)
    return cleaned


def remove_mod(
    path: Path, name: str, force: bool = False, clean_profiles: bool = False
) -> RemoveResult:
    """Remove the active mod ``name`` from the game directory at ``path``.

    Checks the player profiles for residue of the mod, then reverts its
    install commit and adds the trailers ``Mod: <name>`` and
    ``Mod-State: removed``. On a revert conflict the revert is aborted, the
    tree is left clean, and the error lists the conflicting files and the
    later mods that provide them.

    With ``clean_profiles`` the profiles are cleaned of the mod first, as
    :func:`clean_profile` does it and reported in
    :attr:`RemoveResult.cleanup`. The residue check is then skipped: what
    survives a cleanup is what no cleanup can attribute, and blocking the
    removal on it would defeat the purpose.

    Args:
        path: The tracked game directory.
        name: The name of the active mod to remove; matched case-insensitively.
        force: Ignore profile residues and remove anyway.
        clean_profiles: Clean the mod's content from the profiles before
            removing it, backing up and snapshotting the changes.

    Returns:
        The revert commit hash, affected files, warnings, and the cleanup result
        if profiles were cleaned.

    Raises:
        ModNotFoundError: no active mod matches ``name``.
        AmbiguousModError: several mods match ``name``; the error lists them.
        ProfileResidueError: a profile still references content of the mod and
            neither ``force`` nor ``clean_profiles`` is set.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        SptmodError: a profile could not be backed up or written.
        ConflictError: the revert conflicted with a later mod; names all
            conflicting files and the mods providing them.
        GitError: git failed.
    """
    repo = Repo(path)
    with game_lock(path, f"removing {name}"):
        ensure_installable(repo)

        active = [mod for mod in current_mods(repo) if mod.state in ACTIVE_STATES]
        mod = resolve_mod(active, name)
        cleanup = clean_profiles_inline(repo, mod) if clean_profiles else None
        warnings = (
            list(cleanup.warnings)
            if cleanup is not None
            else check_profile_residue(repo, mod, force)
        )

        try:
            commit_hash = repo.revert(
                mod.commit,
                extra_trailers={TRAILER_MOD: mod.name, TRAILER_MOD_STATE: "removed"},
            )
        except GitError as error:
            raise _sequencer_conflict(repo, mod, error, "remove") from error
        _drop_runtime_data(repo, commit_hash, detect_layout(path))

    return RemoveResult(
        name=mod.name,
        commit=commit_hash,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
        warnings=warnings,
        cleanup=cleanup,
    )


def disable_mod(path: Path, name: str) -> ToggleResult:
    """Disable the active mod ``name`` in the game directory at ``path``.

    Reverts the installed version of the mod and marks the revert with the
    trailers ``Mod: <name>`` and ``Mod-State: disabled``, so the files leave
    the working tree while the history keeps them. There is no ``.disabled``
    folder; the state of a mod is the state of its youngest commit.

    A mod that is not active is left untouched and reported as a no-op through
    ``notice``.

    Raises:
        ModNotFoundError: no mod named ``name`` was ever installed.
        AmbiguousModError: several mods match ``name``; the error lists them.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ConflictError: the revert conflicted with a later mod; names all
            conflicting files and the mods providing them.
        GitError: git failed.
    """
    repo = Repo(path)
    with game_lock(path, f"disabling {name}"):
        mod = resolve_mod(current_mods(repo), name)
        if mod.state not in ACTIVE_STATES:
            return ToggleResult(
                name=mod.name,
                state=mod.state,
                notice=f"{mod.name} is already {mod.state}, nothing to disable",
            )
        ensure_installable(repo)

        try:
            commit_hash = repo.revert(
                mod.commit,
                extra_trailers={TRAILER_MOD: mod.name, TRAILER_MOD_STATE: "disabled"},
            )
        except GitError as error:
            raise _sequencer_conflict(repo, mod, error, "disable") from error
        _drop_runtime_data(repo, commit_hash, detect_layout(path))

    return ToggleResult(
        name=mod.name,
        state="disabled",
        commit=commit_hash,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
    )


def enable_mod(path: Path, name: str) -> ToggleResult:
    """Enable the disabled mod ``name`` in the game directory at ``path``.

    Cherry-picks the youngest commit of the mod carrying the full trailer set
    and marks the new commit with the trailers ``Mod: <name>`` and
    ``Mod-State: enabled``, so the files return with the content they had when
    they were disabled. HANDOFF §4b says "the original install commit"; taking
    the youngest full commit instead brings back the version the mod was last
    updated to rather than the version it was first installed with.

    A mod that is not disabled is left untouched and reported as a no-op
    through ``notice``.

    Raises:
        ModNotFoundError: no mod named ``name`` was ever installed.
        AmbiguousModError: several mods match ``name``; the error lists them.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ConflictError: the cherry-pick conflicted with a later mod; names all
            conflicting files and the mods providing them.
        GitError: git failed.
    """
    repo = Repo(path)
    with game_lock(path, f"enabling {name}"):
        mod = resolve_mod(current_mods(repo), name)
        if mod.state != "disabled":
            return ToggleResult(
                name=mod.name,
                state=mod.state,
                notice=f"{mod.name} is {mod.state}, not disabled, nothing to enable",
            )
        ensure_installable(repo)

        try:
            commit_hash = repo.cherry_pick(
                install_commit(repo, mod.name),
                extra_trailers={TRAILER_MOD: mod.name, TRAILER_MOD_STATE: "enabled"},
            )
        except GitError as error:
            raise _sequencer_conflict(repo, mod, error, "enable") from error

    return ToggleResult(
        name=mod.name,
        state="enabled",
        commit=commit_hash,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
    )


def adopt_mod(
    path: Path,
    target: Path,
    name_override: str | None = None,
    version_override: str | None = None,
) -> AdoptResult:
    """Take the untracked mod ``target`` into the repository at ``path``.

    ``target`` is a mod folder or a single DLL below the server mod directory
    or ``BepInEx/plugins``, given absolute or relative to the game directory.
    Metadata comes from ``package.json`` for a mod folder and from the file
    name for a bare DLL, which needs ``version_override`` to carry a version;
    both overrides win over what was found.

    Commits the files below ``target`` as a regular install commit
    ``mod: <name> <version>`` with the full trailer set, ``Mod-State:
    installed`` and ``Mod-Source`` set to the game-directory-relative path, so
    the mod afterwards appears in :func:`list_mods` and :func:`mod_files`.
    Only those files are staged; other pending changes stay untouched and the
    working tree may be dirty, which it is after an :func:`init_game_dir` of an
    installation that already had mods.

    Raises:
        NotAGameDirError: ``target`` lies outside the game directory or below
            none of the mod paths.
        ModNotFoundError: ``target`` does not exist.
        ConflictError: ``target`` is part of SPT itself, an active mod already
            provides it, even when it also holds untracked files, or it is
            tracked without belonging to a mod.
        InvalidMetadataError: neither ``package.json`` nor the overrides supply
            a name and a version.
        GitError: git failed or metadata contains invalid trailer values.
    """
    with game_lock(path, f"adopting {target.name}"):
        return adopt_mod_at(path, target, name_override, version_override)


def adopt_mod_at(
    path: Path,
    target: Path,
    name_override: str | None = None,
    version_override: str | None = None,
) -> AdoptResult:
    """Adopt ``target`` without taking the lock of the game directory."""
    layout = detect_layout(path)
    repo = Repo(path)
    relative = _relative_target(path, target)
    if is_spt_own_path(relative):
        raise ConflictError(
            f"cannot adopt {relative.as_posix()}: it is part of SPT itself, not a mod"
        )
    if mod_prefix_of(relative, layout) is None:
        raise NotAGameDirError(
            f"cannot adopt {relative.as_posix()}: it is below neither "
            f"{layout.mods_dir.as_posix()} nor {PLUGINS_DIR.as_posix()}"
        )
    if not (path / relative).exists():
        raise ModNotFoundError(f"{relative.as_posix()} does not exist in {path}")

    owner = last_touching_mod(repo, relative)
    if owner and any(
        mod.name == owner and mod.state in ACTIVE_STATES for mod in current_mods(repo)
    ):
        raise ConflictError(
            f"cannot adopt {relative.as_posix()}: it is already provided by {owner}"
        )

    files = repo.untracked_under(relative)
    if not files:
        raise ConflictError(
            f"cannot adopt {relative.as_posix()}: it holds no untracked files"
        )

    meta = _adopt_meta(path, relative, name_override, version_override)
    draft = ModInfo(
        name=meta.name,
        version=meta.version,
        mod_type="server" if relative.is_relative_to(layout.mods_dir) else "client",
        author=meta.author,
        spt_range=meta.spt_range,
        state="installed",
        commit="",
        date=UNSET_DATE,
        forge_id="",
        forge_version_id="",
        guid=meta.guid,
        source=relative.as_posix(),
    )
    commit_hash = repo.commit(
        f"mod: {meta.name} {meta.version}",
        trailers=build_trailers(draft),
        paths=files,
    )
    committed = repo.log()[0]
    return AdoptResult(
        mod=replace(draft, commit=commit_hash, date=committed.date), files=files
    )


def launch_profile(
    path: Path,
    profile_id: str,
    stop_server_on_exit: bool = True,
    progress: Progress | None = None,
) -> LaunchResult:
    """Start the SPT server and the game at ``path`` for one player profile.

    Reuses a server that is already answering and starts one otherwise, waits
    until it is ready, signs the profile in and starts the game. The started
    programs outlive this call.

    Args:
        path: The tracked game directory.
        profile_id: The profile to play, named by its file name without the
            extension.
        stop_server_on_exit: Close the server when the game closes.
        progress: Optional callback for the phases of the launch.

    Returns:
        The profile, its player name and the started processes.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        GameRunningError: The game is already running.
        ServerPortBusyError: Another program holds the server's address.
        LoginFailedError: The server does not know the profile.
        LauncherSettingsError: The settings of the official SPT launcher are
            missing or unusable.
        LaunchError: A program is missing, could not be started, or the server
            did not answer in time.
    """
    return _launch_profile(path, profile_id, stop_server_on_exit, progress)


def launch_status(path: Path) -> LaunchStatus:
    """Report whether the SPT server and the game at ``path`` are running.

    Reads only, and never fails: an installation nothing is known about
    reports nothing running. A server or game started outside sptmod is
    reported as well.
    """
    return _launch_status(path)


def stop_game(path: Path) -> StopResult:
    """Close the game and the SPT server of the installation at ``path``.

    Everything the game started is closed with it. Progress the game has not
    saved yet is lost, so callers ask before calling this.
    """
    return _stop_game(path)


def _drop_runtime_data(repo: Repo, commit_hash: str, layout: GameLayout) -> None:
    """Delete the runtime data left in the directories ``commit_hash`` emptied.

    A mod that is removed or disabled must leave nothing behind, but the files
    it wrote itself while the game ran were never recorded and therefore
    survive the revert. Only untracked files go; another mod's content in the
    same directory stays. The cleanup is best-effort: whatever cannot be
    deleted simply stays in place.
    """
    boundary = owned_prefixes(layout)
    directories = {
        directory
        for mod_file in repo.show_files(commit_hash)
        if (directory := owning_mod_dir(mod_file.path, layout)) is not None
    }
    for directory in directories:
        if not (repo.path / directory).is_dir():
            continue
        leftovers = repo.untracked_under(directory)
        parents: set[Path] = set()
        for leftover in leftovers:
            try:
                (repo.path / leftover).unlink(missing_ok=True)
            except OSError:
                continue
            parents.add((repo.path / leftover).parent)
        for parent in sorted(parents, reverse=True):
            try:
                prune_empty(repo.path, parent, boundary)
            except OSError:
                continue


def _joined(paths: Sequence[Path]) -> str:
    return ", ".join(path.as_posix() for path in paths)


def _sequencer_conflict(
    repo: Repo, mod: ModInfo, error: GitError, verb: str
) -> ConflictError:
    if not error.conflicting_paths:
        return ConflictError(f"cannot {verb} {mod.name}: {error.message}")
    blockers: dict[str, list[Path]] = {}
    for path in error.conflicting_paths:
        name = last_touching_mod(repo, path)
        if name:
            blockers.setdefault(name, []).append(path)
    if not blockers:
        return ConflictError(
            f"cannot {verb} {mod.name}: later changes to "
            f"{_joined(error.conflicting_paths)} conflict"
        )
    details = "; ".join(
        f"{_joined(paths)} is now provided by {name}"
        for name, paths in blockers.items()
    )
    return ConflictError(f"cannot {verb} {mod.name}: {details}")


def _relative_target(path: Path, target: Path) -> Path:
    """Return ``target`` relative to the game directory ``path``.

    Raises:
        NotAGameDirError: ``target`` lies outside ``path``.
    """
    absolute = target if target.is_absolute() else path / target
    try:
        return absolute.resolve().relative_to(path.resolve())
    except ValueError as error:
        raise NotAGameDirError(
            f"{target} lies outside the game directory {path}"
        ) from error


def _adopt_meta(
    path: Path, relative: Path, name_override: str | None, version_override: str | None
) -> ModMeta:
    """Read the metadata of the adoption target ``relative``.

    Uses ``package.json`` where the target has one and the file name otherwise,
    which is why a bare DLL needs a version override.

    Raises:
        InvalidMetadataError: name or version stay empty.
    """
    if (path / relative / PACKAGE_JSON_NAME).is_file():
        meta = read_package_json(path / relative)
    else:
        meta = meta_from_dll(relative, name_override, version_override)
    resolved = replace(
        meta,
        name=name_override or meta.name,
        version=version_override or meta.version,
    )
    if not resolved.name or not resolved.version:
        raise InvalidMetadataError(
            f"cannot adopt {relative.as_posix()}: it has no {PACKAGE_JSON_NAME}, "
            f"pass --name and --version"
        )
    reject_multiline_metadata(resolved)
    return resolved


def _mod_entries(untracked: Sequence[Path], layout: GameLayout) -> list[Path]:
    entries: list[Path] = []
    for entry in untracked:
        prefix = mod_prefix_of(entry, layout)
        if prefix is None:
            continue
        top = prefix / entry.relative_to(prefix).parts[0]
        if top not in entries:
            entries.append(top)
    return entries
