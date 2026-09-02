"""Service layer: the operations behind the CLI and the HTTP adapter."""

import json
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from sptmod.core.archive import ArchiveLayout, extract, find_anchor
from sptmod.core.conflicts import (
    apply_force,
    check_dependencies,
    check_dll_duplicates,
    check_file_conflicts,
    check_spt_compat,
)
from sptmod.core.errors import (
    AmbiguousModError,
    ConflictError,
    DependencyError,
    DirtyTreeError,
    GitError,
    ModNotFoundError,
    NotAGameDirError,
    NotImplementedYetError,
    ProfileResidueError,
)
from sptmod.core.forge.catalog import Catalog, CatalogEntry, SyncResult
from sptmod.core.forge.client import ForgeClient
from sptmod.core.game_dir import (
    BEPINEX_CONFIG_DIR,
    MODS_DIR,
    PLUGINS_DIR,
    PROFILES_DIR,
    ensure_game_not_running,
    spt_version,
    validate_game_dir,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    MOD_STATES,
    MOD_TYPES,
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    TRAILER_MOD_TYPE,
    CommitInfo,
    Conflict,
    ConflictKind,
    ModFile,
    ModInfo,
    ModState,
    ModType,
    ProfileResidue,
    Progress,
)
from sptmod.core.modmeta import (
    PACKAGE_JSON_NAME,
    InvalidMetadataError,
    ModMeta,
    build_trailers,
    meta_from_dll,
    mod_info_from_commit,
    read_package_json,
)
from sptmod.core.profile import (
    CheckResult,
    check_all,
    collect_mod_ids,
    find_residues,
    profile_paths,
)
from sptmod.core.repo import Repo

SPT_VERSION_TRAILER: Final = "Spt-Version"
UNKNOWN_SPT_VERSION: Final = "unknown"

type InstallStep = Literal[
    "check",
    "extract",
    "anchor",
    "metadata",
    "conflicts",
    "copy",
    "commit",
]

INSTALL_STEPS: Final[tuple[InstallStep, ...]] = (
    "check",
    "extract",
    "anchor",
    "metadata",
    "conflicts",
    "copy",
    "commit",
)

_STEP_PERCENT: Final[dict[InstallStep, int]] = {
    "check": 0,
    "extract": 10,
    "anchor": 30,
    "metadata": 45,
    "conflicts": 60,
    "copy": 75,
    "commit": 90,
}

type BrowseFilter = Literal["all", "installed", "available"]
type ExportFormat = Literal["text", "json"]

EXPORT_FORMATS: Final[tuple[ExportFormat, ...]] = ("text", "json")

_DEPENDENCY_KINDS: Final[tuple[ConflictKind, ...]] = (
    "missing-dependency",
    "dependency-version",
)

_MOD_PREFIXES: Final[tuple[Path, ...]] = (MODS_DIR, PLUGINS_DIR)
_CLIENT_TYPES: Final[tuple[ModType, ...]] = ("client", "hybrid")
_SNAPSHOT_TIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
_LINK_MARKER: Final = "://"
_UNSET_DATE: Final = datetime.min


@dataclass(frozen=True)
class InitResult:
    """Outcome of :func:`init_game_dir`.

    ``pre_existing_mods`` lists every mod found in the untracked game
    directory, ``adopted`` the ones the ``adopt`` selection turned into
    install commits.
    """

    game_dir: Path
    commit: str
    spt_version: str
    pre_existing_mods: list[Path] = field(default_factory=list)
    adopted: list[ModInfo] = field(default_factory=list)


@dataclass(frozen=True)
class InstallResult:
    """Outcome of :func:`install_from_archive`.

    ``warnings`` holds the non-blocking findings of the conflict checks,
    including the ones ``force`` downgraded from errors.
    """

    mod: ModInfo
    files: list[Path] = field(default_factory=list)
    warnings: list[Conflict] = field(default_factory=list)


@dataclass(frozen=True)
class RemoveResult:
    """Outcome of :func:`remove_mod`.

    ``warnings`` holds db file read/parse errors from ID collection, each
    naming the file and the error.
    """

    name: str
    commit: str
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToggleResult:
    """Outcome of :func:`disable_mod` and :func:`enable_mod`.

    A no-op leaves ``commit`` and ``files`` empty and explains itself in
    ``notice``; ``state`` then holds the state the mod already had.
    """

    name: str
    state: ModState
    commit: str = ""
    files: list[Path] = field(default_factory=list)
    notice: str = ""


@dataclass(frozen=True)
class AdoptResult:
    """Outcome of :func:`adopt_mod`."""

    mod: ModInfo
    files: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class ManualMod:
    """Untracked files below a mod path, grouped by their mod folder.

    ``path`` is the mod folder, or the file itself for a loose plugin,
    relative to the game directory; ``files`` are the untracked files it
    contains.
    """

    path: Path
    files: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class StatusReport:
    """The interpreted working-tree status of a game directory.

    ``other_modified`` and ``other_untracked`` hold everything that is neither
    a profile, a BepInEx config nor an untracked file below a mod path.
    """

    modified_profiles: list[Path] = field(default_factory=list)
    modified_configs: list[Path] = field(default_factory=list)
    manual_mods: list[ManualMod] = field(default_factory=list)
    other_modified: list[Path] = field(default_factory=list)
    other_untracked: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of :func:`snapshot`."""

    commit: str
    message: str
    files: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class ExportEntry:
    """One mod of an exported mod list.

    ``link`` is the Forge detail URL of the mod, empty when the catalog knows
    no match. ``mod_type`` keeps its default for entries read back from a text
    list, which carries no type.
    """

    name: str
    version: str
    mod_type: ModType = "server"
    link: str = ""


@dataclass(frozen=True)
class ImportEntry:
    """One mod an imported list and the installed mods disagree about.

    ``wanted_version`` is empty for mods only the game directory has,
    ``installed_version`` for mods only the list has.
    """

    name: str
    wanted_version: str = ""
    installed_version: str = ""
    link: str = ""


@dataclass(frozen=True)
class ImportDiff:
    """The difference between an imported mod list and the installed mods."""

    missing: list[ImportEntry] = field(default_factory=list)
    different: list[ImportEntry] = field(default_factory=list)
    extra: list[ImportEntry] = field(default_factory=list)


def init_game_dir(path: Path, adopt: Sequence[Path] | bool = False) -> InitResult:
    """Turn the SPT installation at ``path`` into a tracked repository.

    Validates the game directory, writes the whitelist ``.gitignore``, runs
    ``git init`` and creates the initial commit ``chore: clean SPT <version>``
    carrying the ``Spt-Version`` trailer. The version comes from
    ``SPT/SPT_Data/configs/core.json`` and falls back to
    ``UNKNOWN_SPT_VERSION`` when it cannot be read.

    The initial commit holds only ``.gitignore``, the tracked profiles and the
    BepInEx configs. Mod content that already exists below ``SPT/user/mods``
    and ``BepInEx/plugins`` stays untracked and is returned as
    ``pre_existing_mods`` (one path per mod folder or loose plugin file,
    relative to the game directory) so callers can offer them for adoption.

    ``adopt`` selects which of those mods become install commits through
    :func:`adopt_mod`: ``False`` adopts nothing, ``True`` adopts all of them,
    and a sequence of paths adopts exactly those, which is how a caller passes
    back the entries a user confirmed.

    Raises:
        NotAGameDirError: ``path`` is missing, is no directory or lacks
            ``SPT/`` or ``BepInEx/``.
        InvalidMetadataError: a mod selected for adoption has no usable
            metadata.
        GitError: ``path`` is already a git repository or git failed.
    """
    validate_game_dir(path)
    repo = Repo.init(path)
    version = spt_version(path) or UNKNOWN_SPT_VERSION

    untracked = repo.untracked_under(Path("."))
    baseline = [entry for entry in untracked if _mod_prefix_of(entry) is None]
    commit_hash = repo.commit(
        f"chore: clean SPT {version}",
        trailers={SPT_VERSION_TRAILER: version},
        paths=baseline,
    )
    pre_existing = _mod_entries(untracked)
    if isinstance(adopt, bool):
        selected = pre_existing if adopt else []
    else:
        selected = list(adopt)
    return InitResult(
        game_dir=path,
        commit=commit_hash,
        spt_version=version,
        pre_existing_mods=pre_existing,
        adopted=[adopt_mod(path, entry).mod for entry in selected],
    )


def install_from_archive(
    path: Path,
    archive: Path,
    name_override: str | None = None,
    version_override: str | None = None,
    force: bool = False,
    progress: Progress | None = None,
) -> InstallResult:
    """Install the mod archive ``archive`` into the game directory ``path``.

    The shared install core, also used by the Forge path. Steps, each reported
    through ``progress`` in the order of ``INSTALL_STEPS``:

    ``check``
        The working tree is clean and neither server nor client is running.
    ``extract``
        The archive is unpacked into a temporary directory, which is removed
        again on success and on failure.
    ``anchor``
        The mod trees are located inside the extraction and the mod type is
        determined.
    ``metadata``
        ``package.json`` for server and hybrid mods, DLL file name for
        client-only mods. Trailers are validated pre-copy to prevent
        half-installed states. ``name_override`` and ``version_override``
        override extracted values.
    ``conflicts``
        File, DLL and dependency checks; ``force`` downgrades them to warnings.
    ``copy``
        The anchored files are copied into the game directory.
    ``commit``
        One commit ``mod: <name> <version>`` with the full set of mod trailers,
        ``Mod-State: installed`` and ``Mod-Source`` set to the archive file
        name.

    On commit failure, newly-copied files and empty parent directories are
    rolled back. Pre-existing files that were overwritten are not restored;
    this is acceptable because the game tree remains consistent and invalid
    states can be fixed by later operations.

    Raises:
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ArchiveError: the archive is unreadable, unsafe or has no anchor.
        InvalidMetadataError: mandatory mod metadata is missing.
        ConflictError: a file or DLL conflict blocks the install and ``force``
            is not set.
        DependencyError: a required dependency is missing or has an
            incompatible version and ``force`` is not set.
        GitError: git failed or metadata contains invalid trailer values.
    """
    repo = Repo(path)
    _report(progress, "check", "checking the working tree and running processes")
    _ensure_installable(repo)

    with tempfile.TemporaryDirectory(prefix="sptmod-") as temp_name:
        temp = Path(temp_name)
        _report(progress, "extract", f"extracting {archive.name}")
        extract(archive, temp)

        _report(progress, "anchor", "searching the mod anchor")
        layout = find_anchor(temp)

        _report(progress, "metadata", "reading the mod metadata")
        meta = _read_meta(layout, name_override, version_override)

        _report(progress, "conflicts", "checking conflicts and dependencies")
        warnings = _check_install_conflicts(repo, layout, meta, force)

        _report(progress, "copy", f"copying {len(layout.files)} files")
        added = [file for file in layout.files if not (path / file).exists()]
        for mapping in layout.mappings:
            shutil.copytree(mapping.source, path / mapping.target, dirs_exist_ok=True)

    _report(progress, "commit", f"committing {meta.name} {meta.version}")
    draft = ModInfo(
        name=meta.name,
        version=meta.version,
        mod_type=layout.mod_type,
        author=meta.author,
        spt_range=meta.spt_range,
        state="installed",
        commit="",
        date=_UNSET_DATE,
        forge_id="",
        forge_version_id="",
        guid=meta.guid,
        source=archive.name,
    )
    try:
        commit_hash = repo.commit(
            f"mod: {meta.name} {meta.version}",
            trailers=build_trailers(draft),
            paths=layout.files,
        )
    except Exception:
        _remove_copied(path, added)
        raise
    committed = repo.log()[0]
    _report(progress, "commit", f"installed {meta.name} {meta.version}", percent=100)
    return InstallResult(
        mod=replace(draft, commit=commit_hash, date=committed.date),
        files=list(layout.files),
        warnings=warnings,
    )


def list_mods(path: Path, include_all: bool = False) -> list[ModInfo]:
    """Return the mods known to the repository at ``path``, newest first.

    The state of a mod is the state of the youngest commit carrying its
    ``Mod:`` trailer. By default only active mods are returned, meaning mods
    whose state is one of ``ACTIVE_STATES``; ``include_all`` also returns
    disabled and removed ones.

    Raises:
        GitError: git failed.
    """
    mods = _current_mods(Repo(path))
    if include_all:
        return mods
    return [mod for mod in mods if mod.state in ACTIVE_STATES]


def mod_files(path: Path, name: str) -> list[ModFile]:
    """Return the files touched by the youngest commit of mod ``name``.

    For an active mod these are the files its install commit added; for a
    disabled or removed mod they are the deletions of the revert commit.

    Raises:
        ModNotFoundError: no mod matches ``name``.
        AmbiguousModError: several mods match ``name``; the error lists them.
        GitError: git failed.
    """
    repo = Repo(path)
    mod = _resolve(_current_mods(repo), name)
    return repo.show_files(mod.commit)


def remove_mod(path: Path, name: str, force: bool = False) -> RemoveResult:
    """Remove the active mod ``name`` from the game directory at ``path``.

    Checks the player profiles for residue of the mod, then reverts its
    install commit and adds the trailers ``Mod: <name>`` and
    ``Mod-State: removed``. On a revert conflict the revert is aborted, the
    tree is left clean, and the error lists the conflicting files and the
    later mods that provide them.

    Raises:
        ModNotFoundError: no active mod matches ``name``.
        AmbiguousModError: several mods match ``name``; the error lists them.
        ProfileResidueError: a profile still references content of the mod and
            ``force`` is not set.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ConflictError: the revert conflicted with a later mod; names all
            conflicting files and the mods providing them.
        GitError: git failed.
    """
    repo = Repo(path)
    _ensure_installable(repo)

    active = [mod for mod in _current_mods(repo) if mod.state in ACTIVE_STATES]
    mod = _resolve(active, name)
    warnings = _check_profile_residue(repo, mod, force)

    try:
        commit_hash = repo.revert(
            mod.commit,
            extra_trailers={TRAILER_MOD: mod.name, TRAILER_MOD_STATE: "removed"},
        )
    except GitError as error:
        raise _sequencer_conflict(repo, mod, error, "remove") from error

    return RemoveResult(
        name=mod.name,
        commit=commit_hash,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
        warnings=warnings,
    )


def check_profiles(path: Path) -> CheckResult:
    """Report which active mods the player profiles at ``path`` still reference.

    Reads only: neither the repository nor the profiles are changed, so
    residues are reported and never block.

    Raises:
        GitError: git failed.
    """
    return check_all(Repo(path), path)


def history(path: Path, name: str | None = None) -> list[CommitInfo]:
    """Return the commits of the repository at ``path``, newest first.

    Without ``name`` the full history is returned, including snapshots and the
    initial commit. With ``name`` only the commits carrying that mod's
    ``Mod:`` trailer are returned; the match is case-insensitive.

    Raises:
        ModNotFoundError: no mod matches ``name``.
        AmbiguousModError: several mods match ``name``; the error lists them.
        GitError: git failed.
    """
    repo = Repo(path)
    if name is None:
        return repo.log()
    mod = _resolve(_current_mods(repo), name)
    return repo.log(trailer_filter={TRAILER_MOD: mod.name})


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
        ForgeUnavailableError: The API is unreachable; the cache keeps its
            previous contents.
        ForgeApiError: The API answered with an error envelope.
    """
    if client is not None:
        with Catalog(db_path) as catalog:
            return catalog.sync(client, full=full, progress=progress)
    with ForgeClient() as owned, Catalog(db_path) as catalog:
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
        GitError: git failed.
    """
    installed = list_mods(path)
    compatible_with = _compatible_with(path, compatible_only)
    with Catalog(db_path) as catalog:
        matched = {
            mod.id for mod in catalog.search(query, compatible_with=compatible_with)
        }
        entries = catalog.classify(installed, compatible_with=compatible_with)
    return [entry for entry in entries if entry.mod.id in matched]


def browse_catalog(
    path: Path,
    filter: BrowseFilter = "all",
    compatible_only: bool = False,
    *,
    db_path: Path | None = None,
) -> list[CatalogEntry]:
    """Return the cached catalog joined with the installed mods of ``path``.

    ``filter`` selects one of the three frontend lists: ``installed`` keeps the
    entries matching an active mod, including the ones with an update
    available, ``available`` keeps the rest, and ``all`` keeps everything.

    Args:
        path: The tracked game directory.
        filter: Which of the three lists to return.
        compatible_only: Keep only mods with a version compatible with the SPT
            version of the game directory.
        db_path: Catalog database path; defaults to :func:`default_db_path`.

    Returns:
        The selected entries ordered by mod name.

    Raises:
        GitError: git failed.
    """
    installed = list_mods(path)
    with Catalog(db_path) as catalog:
        entries = catalog.classify(
            installed, compatible_with=_compatible_with(path, compatible_only)
        )
    if filter == "all":
        return entries
    wanted = filter == "installed"
    return [entry for entry in entries if (entry.state != "available") == wanted]


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
    mod = _resolve(_current_mods(repo), name)
    if mod.state not in ACTIVE_STATES:
        return ToggleResult(
            name=mod.name,
            state=mod.state,
            notice=f"{mod.name} is already {mod.state}, nothing to disable",
        )
    _ensure_installable(repo)

    try:
        commit_hash = repo.revert(
            mod.commit,
            extra_trailers={TRAILER_MOD: mod.name, TRAILER_MOD_STATE: "disabled"},
        )
    except GitError as error:
        raise _sequencer_conflict(repo, mod, error, "disable") from error

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
    mod = _resolve(_current_mods(repo), name)
    if mod.state != "disabled":
        return ToggleResult(
            name=mod.name,
            state=mod.state,
            notice=f"{mod.name} is {mod.state}, not disabled, nothing to enable",
        )
    _ensure_installable(repo)

    try:
        commit_hash = repo.cherry_pick(
            _install_commit(repo, mod.name),
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

    ``target`` is a mod folder or a single DLL below ``SPT/user/mods`` or
    ``BepInEx/plugins``, given absolute or relative to the game directory.
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
        ConflictError: an active mod already provides ``target``, even when it
            also holds untracked files, or ``target`` is tracked without
            belonging to a mod.
        InvalidMetadataError: neither ``package.json`` nor the overrides supply
            a name and a version.
        GitError: git failed or metadata contains invalid trailer values.
    """
    repo = Repo(path)
    relative = _relative_target(path, target)
    if _mod_prefix_of(relative) is None:
        raise NotAGameDirError(
            f"cannot adopt {relative.as_posix()}: it is below neither "
            f"{MODS_DIR.as_posix()} nor {PLUGINS_DIR.as_posix()}"
        )
    if not (path / relative).exists():
        raise ModNotFoundError(f"{relative.as_posix()} does not exist in {path}")

    owner = _last_touching_mod(repo, relative)
    if owner and any(
        mod.name == owner and mod.state in ACTIVE_STATES for mod in _current_mods(repo)
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
        mod_type="server" if relative.is_relative_to(MODS_DIR) else "client",
        author=meta.author,
        spt_range=meta.spt_range,
        state="installed",
        commit="",
        date=_UNSET_DATE,
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


def status(path: Path) -> StatusReport:
    """Interpret the working-tree status of the game directory at ``path``.

    Sorts the modified files into player profiles, which change normally
    while playing and belong into a :func:`snapshot`, BepInEx configs and
    everything else. Untracked files below the mod paths are grouped per mod
    folder as manual installs a caller can offer to :func:`adopt_mod`; all
    other untracked files are reported as they are. Untracked files below the
    profile and config paths join the modified ones there, because a
    :func:`snapshot` commits them together.

    Raises:
        GitError: git failed.
    """
    raw = Repo(path).status()
    modified_profiles: list[Path] = []
    modified_configs: list[Path] = []
    other_modified: list[Path] = []
    other_untracked: list[Path] = []
    manual_files: dict[Path, list[Path]] = {}

    for entry in raw.modified:
        _group_snapshot_path(entry, modified_profiles, modified_configs, other_modified)

    for entry in raw.untracked:
        prefix = _mod_prefix_of(entry)
        if prefix is not None:
            top = prefix / entry.relative_to(prefix).parts[0]
            manual_files.setdefault(top, []).append(entry)
            continue
        _group_snapshot_path(
            entry, modified_profiles, modified_configs, other_untracked
        )

    return StatusReport(
        modified_profiles=modified_profiles,
        modified_configs=modified_configs,
        manual_mods=[
            ManualMod(path=top, files=files) for top, files in manual_files.items()
        ],
        other_modified=other_modified,
        other_untracked=other_untracked,
    )


def snapshot(path: Path, message: str | None = None) -> SnapshotResult:
    """Commit the profile and config changes of the game directory at ``path``.

    Stages only the changed files below ``SPT/user/profiles/`` and
    ``BepInEx/config/`` and commits them as ``snapshot: <message>``, defaulting
    to a timestamp, without any ``Mod:`` trailer. Changes to tracked mod files
    stay uncommitted.

    Raises:
        GitError: neither profiles nor configs changed, or git failed.
    """
    repo = Repo(path)
    text = message or datetime.now().strftime(_SNAPSHOT_TIME_FORMAT)
    report = status(path)
    paths = [*report.modified_profiles, *report.modified_configs]
    if not paths:
        raise GitError(f"{path} has no profile or config changes to snapshot")
    commit_hash = repo.commit(f"snapshot: {text}", paths=paths)
    return SnapshotResult(
        commit=commit_hash,
        message=text,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
    )


def export_mods(
    path: Path,
    format: ExportFormat = "text",
    client_only: bool = False,
    *,
    db_path: Path | None = None,
) -> str:
    """Render the active mods of the game directory at ``path`` as a list.

    ``text`` writes one ``<name> <version> <link>`` line per mod, ordered by
    name, with the link omitted when the catalog knows none. ``json`` writes an
    object ``{"mods": [{"name", "version", "type", "link"}]}``. Both are read
    back by :func:`import_list`. ``client_only`` keeps only the mods with a
    client part, meaning the ``client`` and ``hybrid`` ones, which is the list
    a Fika client needs.

    Args:
        path: The tracked game directory.
        format: The output format.
        client_only: Keep only mods with a client part.
        db_path: Catalog database path; defaults to :func:`default_db_path`.

    Returns:
        The rendered list, ending in a newline.

    Raises:
        GitError: git failed.
    """
    mods = list_mods(path)
    if client_only:
        mods = [mod for mod in mods if mod.mod_type in _CLIENT_TYPES]
    links = _catalog_links(mods, db_path)
    entries = sorted(
        (
            ExportEntry(
                name=mod.name,
                version=mod.version,
                mod_type=mod.mod_type,
                link=links.get(mod.name.casefold(), ""),
            )
            for mod in mods
        ),
        key=lambda entry: entry.name.casefold(),
    )
    if format == "json":
        payload = {
            "mods": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "type": entry.mod_type,
                    "link": entry.link,
                }
                for entry in entries
            ]
        }
        return json.dumps(payload, indent=2) + "\n"
    return "".join(
        " ".join(part for part in (entry.name, entry.version, entry.link) if part)
        + "\n"
        for entry in entries
    )


def import_list(path: Path, list_file: Path, apply: bool = False) -> ImportDiff:
    """Compare the exported mod list ``list_file`` with the game dir ``path``.

    Reads both formats of :func:`export_mods`, recognised by content rather
    than by file name, and matches the entries against the active mods
    case-insensitively by name. Mods only the list has are ``missing``, mods
    both know with a different version are ``different``, and mods only the
    game directory has are ``extra``, sorted by name. ``link`` is taken from
    the list where it carries one and stays empty otherwise.

    ``apply`` additionally installs the missing mods through the Forge install
    path; that path is not available yet and :func:`_apply_import` rejects it.

    Raises:
        NotImplementedYetError: ``apply`` is set.
        InvalidMetadataError: ``list_file`` is missing or is no readable mod
            list.
        GitError: git failed.
    """
    wanted = _read_mod_list(list_file)
    installed = {mod.name.casefold(): mod for mod in list_mods(path)}

    missing: list[ImportEntry] = []
    different: list[ImportEntry] = []
    for entry in wanted:
        mod = installed.get(entry.name.casefold())
        if mod is None:
            missing.append(
                ImportEntry(
                    name=entry.name, wanted_version=entry.version, link=entry.link
                )
            )
        elif mod.version != entry.version:
            different.append(
                ImportEntry(
                    name=mod.name,
                    wanted_version=entry.version,
                    installed_version=mod.version,
                    link=entry.link,
                )
            )

    listed = {entry.name.casefold() for entry in wanted}
    extra = [
        ImportEntry(name=mod.name, installed_version=mod.version)
        for key, mod in sorted(installed.items())
        if key not in listed
    ]

    diff = ImportDiff(missing=missing, different=different, extra=extra)
    if apply:
        _apply_import(path, diff)
    return diff


def _apply_import(path: Path, diff: ImportDiff) -> None:
    """Seam for installing the ``missing`` mods of an import diff.

    The Forge install path plugs in here; until then every ``--apply`` run is
    rejected with a typed error naming the reason.
    """
    raise NotImplementedYetError(
        f"cannot install {len(diff.missing)} missing mods into {path}: "
        f"installing from a mod list needs the Forge install path"
    )


def _compatible_with(path: Path, compatible_only: bool) -> str | None:
    return spt_version(path) if compatible_only else None


def _check_install_conflicts(
    repo: Repo, layout: ArchiveLayout, meta: ModMeta, force: bool
) -> list[Conflict]:
    """Seam for the file, DLL and dependency checks of ``core.conflicts``.

    Runs the file, DLL, dependency and SPT-compatibility checks, applies
    ``force`` to their findings and returns the remaining warnings. Blocking
    findings raise ``ConflictError`` for file and DLL problems and
    ``DependencyError`` for dependency problems; both carry the complete list
    of findings, warnings included.
    """
    active = [mod for mod in _current_mods(repo) if mod.state in ACTIVE_STATES]
    findings = apply_force(
        [
            *check_file_conflicts(repo, layout.files),
            *check_dll_duplicates(repo.path, layout.files),
            *check_dependencies(active, meta),
            *check_spt_compat(spt_version(repo.path) or "", meta.spt_range),
        ],
        force,
    )
    blocking = [finding for finding in findings if finding.severity == "error"]
    files = [finding for finding in blocking if finding.kind not in _DEPENDENCY_KINDS]
    if files:
        raise ConflictError(_blocked_message(meta.name, blocking), findings)
    if blocking:
        raise DependencyError(_blocked_message(meta.name, blocking), findings)
    return findings


def _blocked_message(name: str, blocking: Sequence[Conflict]) -> str:
    details = "; ".join(finding.message for finding in blocking)
    return f"cannot install {name}: {details} (use --force to install anyway)"


def _check_profile_residue(repo: Repo, mod: ModInfo, force: bool) -> list[str]:
    """Search every profile for content IDs of ``mod``.

    Returns the collection warnings of ``collect_mod_ids`` and raises
    ``ProfileResidueError`` when a profile still references content of ``mod``
    unless ``force`` is set.
    """
    ids = collect_mod_ids(repo, mod.commit)
    residues = [
        residue
        for profile in profile_paths(repo.path)
        for residue in find_residues(profile, ids)
    ]
    if residues and not force:
        raise ProfileResidueError(_residue_message(mod.name, residues), residues)
    return ids.warnings


def _residue_message(name: str, residues: Sequence[ProfileResidue]) -> str:
    details = "; ".join(
        f"profile {residue.profile}: {residue.count} {residue.category} "
        f"({', '.join(residue.ids)})"
        for residue in residues
    )
    return (
        f"cannot remove {name}, player profiles still reference it: {details} "
        f"(use --force to remove anyway)"
    )


def _report(
    progress: Progress | None,
    step: InstallStep,
    message: str,
    percent: int | None = None,
) -> None:
    if progress is not None:
        progress(
            step=step,
            percent=_STEP_PERCENT[step] if percent is None else percent,
            message=message,
        )


def _remove_copied(root: Path, files: Sequence[Path]) -> None:
    """Undo a copy step so a rejected install leaves no files behind."""
    for file in files:
        (root / file).unlink(missing_ok=True)
    for directory in sorted({(root / file).parent for file in files}, reverse=True):
        _prune_empty(root, directory)


def _prune_empty(root: Path, directory: Path) -> None:
    current = directory
    while (
        current != root
        and current.relative_to(root) not in _MOD_PREFIXES
        and current.is_dir()
        and not any(current.iterdir())
    ):
        current.rmdir()
        current = current.parent


def _ensure_installable(repo: Repo) -> None:
    if not repo.is_clean():
        raise DirtyTreeError(
            f"{repo.path} has uncommitted changes, commit or snapshot them first"
        )
    ensure_game_not_running()


def _read_meta(
    layout: ArchiveLayout, name_override: str | None, version_override: str | None
) -> ModMeta:
    meta = (
        _client_meta(layout, name_override, version_override)
        if layout.mod_type == "client"
        else read_package_json(_server_mod_dir(layout))
    )
    resolved = replace(
        meta,
        name=name_override or meta.name,
        version=version_override or meta.version,
    )
    _reject_multiline_metadata(resolved)
    return resolved


def _reject_multiline_metadata(meta: ModMeta) -> None:
    """Reject metadata that git would refuse as a trailer value.

    Runs before anything is copied, so a value the commit would choke on
    cannot leave a half-installed game directory behind.
    """
    for field_name, value in (
        ("name", meta.name),
        ("version", meta.version),
        ("author", meta.author),
        ("sptVersion", meta.spt_range),
        ("guid", meta.guid),
    ):
        if "\n" in value or "\r" in value:
            raise GitError(
                f"mod metadata field {field_name!r} must not contain line breaks"
            )


def _server_mod_dir(layout: ArchiveLayout) -> Path:
    sources = [
        mapping.source for mapping in layout.mappings if mapping.target == MODS_DIR
    ]
    for source in sources:
        for child in sorted(source.iterdir()):
            if (child / PACKAGE_JSON_NAME).is_file():
                return child
    raise InvalidMetadataError(
        f"no mod folder with a {PACKAGE_JSON_NAME} below "
        f"{MODS_DIR.as_posix()} in the archive"
    )


def _client_meta(
    layout: ArchiveLayout, name_override: str | None, version_override: str | None
) -> ModMeta:
    dll = next((path for path in layout.files if path.suffix.lower() == ".dll"), None)
    if dll is None:
        return ModMeta(name=name_override or "", version=version_override or "")
    return meta_from_dll(dll, name_override, version_override)


def _current_mods(repo: Repo) -> list[ModInfo]:
    """Return one entry per mod, newest first.

    Commits that only change a mod's state -- reverts and cherry-picks --
    carry ``Mod`` and ``Mod-State`` but none of the descriptive trailers, so
    a mod is described by its youngest full mod commit while state, commit
    hash and date come from its youngest commit of any kind.
    """
    heads: dict[str, CommitInfo] = {}
    described: dict[str, ModInfo] = {}
    for commit in repo.log():
        name = commit.trailers.get(TRAILER_MOD, "")
        if not name:
            continue
        heads.setdefault(name, commit)
        full = _described_mod(commit)
        if full is not None:
            described.setdefault(name, full)

    mods: list[ModInfo] = []
    for name, head in heads.items():
        detail = described.get(name)
        if detail is None:
            # Every install writes the full trailer set, so a mod without a
            # descriptive commit exists only in histories written by hand.
            continue
        state = _state_of(head)
        mods.append(
            replace(
                detail,
                state=state or detail.state,
                commit=head.hash,
                date=head.date,
            )
        )
    return mods


def _described_mod(commit: CommitInfo) -> ModInfo | None:
    if commit.trailers.get(TRAILER_MOD_TYPE, "") not in MOD_TYPES:
        return None
    return mod_info_from_commit(commit)


def _state_of(commit: CommitInfo) -> ModState | None:
    state = commit.trailers.get(TRAILER_MOD_STATE, "")
    return next((known for known in MOD_STATES if known == state), None)


def _resolve(mods: Sequence[ModInfo], name: str) -> ModInfo:
    matches = [mod for mod in mods if mod.name.casefold() == name.casefold()]
    candidates = {mod.name for mod in matches}
    if not matches:
        raise ModNotFoundError(f"no installed mod named {name!r}")
    if len(candidates) > 1:
        raise AmbiguousModError(name, sorted(candidates))
    return matches[0]


def _joined(paths: Sequence[Path]) -> str:
    return ", ".join(path.as_posix() for path in paths)


def _last_touching_mod(repo: Repo, path: Path) -> str:
    commit = repo.last_touching(path)
    return commit.trailers.get(TRAILER_MOD, "") if commit is not None else ""


def _install_commit(repo: Repo, name: str) -> str:
    """Return the youngest commit of ``name`` carrying the full trailer set.

    That is the commit an ``enable`` cherry-picks: the install commit, the
    commit of the newest update, or the cherry-pick of a previous ``enable``,
    which copies the message it was picked from. State-only commits -- reverts
    from ``disable`` and ``remove`` -- carry no ``Mod-Type`` and are skipped.

    Raises:
        ModNotFoundError: the history holds no such commit.
    """
    for commit in repo.log(trailer_filter={TRAILER_MOD: name}):
        if _described_mod(commit) is not None:
            return commit.hash
    raise ModNotFoundError(f"no install commit for {name!r} in the history")


def _sequencer_conflict(
    repo: Repo, mod: ModInfo, error: GitError, verb: str
) -> ConflictError:
    if not error.conflicting_paths:
        return ConflictError(f"cannot {verb} {mod.name}: {error.message}")
    blockers: dict[str, list[Path]] = {}
    for path in error.conflicting_paths:
        name = _last_touching_mod(repo, path)
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
    _reject_multiline_metadata(resolved)
    return resolved


def _group_snapshot_path(
    entry: Path, profiles: list[Path], configs: list[Path], rest: list[Path]
) -> None:
    if entry.is_relative_to(PROFILES_DIR):
        profiles.append(entry)
    elif entry.is_relative_to(BEPINEX_CONFIG_DIR):
        configs.append(entry)
    else:
        rest.append(entry)


def _catalog_links(mods: Sequence[ModInfo], db_path: Path | None) -> dict[str, str]:
    """Return the Forge detail URL per installed mod name, case-folded."""
    by_forge_id = {mod.forge_id: mod for mod in mods if mod.forge_id}
    links: dict[str, str] = {}
    with Catalog(db_path) as catalog:
        for entry in catalog.classify(mods):
            if entry.installed_version is None:
                continue
            matched = by_forge_id.get(str(entry.mod.id))
            name = matched.name if matched is not None else entry.mod.name
            links[name.casefold()] = entry.mod.detail_url
    return links


def _read_mod_list(list_file: Path) -> list[ExportEntry]:
    """Read an exported mod list in either format.

    JSON is recognised by a leading ``{``, everything else is read as text.

    Raises:
        InvalidMetadataError: the file is unreadable or holds no mod list.
    """
    try:
        raw = list_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InvalidMetadataError(f"{list_file} cannot be read: {error}") from error
    if raw.lstrip().startswith("{"):
        return _parse_mod_list_json(list_file, raw)
    return _parse_mod_list_text(list_file, raw)


def _parse_mod_list_json(list_file: Path, raw: str) -> list[ExportEntry]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvalidMetadataError(f"{list_file} is no valid JSON: {error}") from error
    mods = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(mods, list):
        raise InvalidMetadataError(f"{list_file} has no 'mods' list")
    return [_mod_list_entry(list_file, item) for item in mods]


def _mod_list_entry(list_file: Path, item: object) -> ExportEntry:
    if not isinstance(item, dict):
        raise InvalidMetadataError(f"{list_file}: a mod entry is no object")
    name = item.get("name")
    version = item.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise InvalidMetadataError(f"{list_file}: a mod entry has no name or version")
    mod_type = item.get("type")
    link = item.get("link")
    return ExportEntry(
        name=name,
        version=version,
        mod_type=mod_type if mod_type in MOD_TYPES else "server",
        link=link if isinstance(link, str) else "",
    )


def _parse_mod_list_text(list_file: Path, raw: str) -> list[ExportEntry]:
    """Parse the text format, whose last token is a link when it is a URL.

    Names may therefore contain spaces: everything before the version is the
    name, and a trailing token counts as a link only when it holds ``://``.
    """
    entries: list[ExportEntry] = []
    for line in raw.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        link = tokens[-1] if _LINK_MARKER in tokens[-1] else ""
        head = tokens[:-1] if link else tokens
        if len(head) < 2:
            raise InvalidMetadataError(
                f"{list_file}: cannot read {line!r}, expected '<name> <version> [link]'"
            )
        entries.append(
            ExportEntry(name=" ".join(head[:-1]), version=head[-1], link=link)
        )
    return entries


def _mod_prefix_of(entry: Path) -> Path | None:
    return next(
        (prefix for prefix in _MOD_PREFIXES if entry.is_relative_to(prefix)), None
    )


def _mod_entries(untracked: Sequence[Path]) -> list[Path]:
    entries: list[Path] = []
    for entry in untracked:
        prefix = _mod_prefix_of(entry)
        if prefix is None:
            continue
        top = prefix / entry.relative_to(prefix).parts[0]
        if top not in entries:
            entries.append(top)
    return entries
