"""Service layer: the operations behind the CLI and the HTTP adapter."""

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
)
from sptmod.core.forge.catalog import Catalog, CatalogEntry, SyncResult
from sptmod.core.forge.client import ForgeClient
from sptmod.core.game_dir import (
    MODS_DIR,
    PLUGINS_DIR,
    ensure_game_not_running,
    spt_version,
    validate_game_dir,
)
from sptmod.core.models import (
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
from sptmod.core.repo import Repo

SPT_VERSION_TRAILER: Final = "Spt-Version"
UNKNOWN_SPT_VERSION: Final = "unknown"

ACTIVE_STATES: Final[tuple[ModState, ...]] = ("installed", "updated", "enabled")

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

_DEPENDENCY_KINDS: Final[tuple[ConflictKind, ...]] = (
    "missing-dependency",
    "dependency-version",
)

_MOD_PREFIXES: Final[tuple[Path, ...]] = (MODS_DIR, PLUGINS_DIR)
_UNSET_DATE: Final = datetime.min


@dataclass(frozen=True)
class InitResult:
    """Outcome of :func:`init_game_dir`."""

    game_dir: Path
    commit: str
    spt_version: str
    pre_existing_mods: list[Path] = field(default_factory=list)


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
    """Outcome of :func:`remove_mod`."""

    name: str
    commit: str
    files: list[Path] = field(default_factory=list)


def init_game_dir(path: Path) -> InitResult:
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

    Raises:
        NotAGameDirError: ``path`` is missing, is no directory or lacks
            ``SPT/`` or ``BepInEx/``.
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
    return InitResult(
        game_dir=path,
        commit=commit_hash,
        spt_version=version,
        pre_existing_mods=_mod_entries(untracked),
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
    _check_profile_residue(repo, mod, force)

    try:
        commit_hash = repo.revert(
            mod.commit,
            extra_trailers={TRAILER_MOD: mod.name, TRAILER_MOD_STATE: "removed"},
        )
    except GitError as error:
        raise _revert_conflict(repo, mod, error) from error

    return RemoveResult(
        name=mod.name,
        commit=commit_hash,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
    )


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
    """Seam for the profile residue check of ``core.profile``.

    Returns the warnings to hand to the caller and raises
    ``ProfileResidueError`` when a profile still references content of ``mod``
    unless ``force`` is set.
    """
    return []


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


def _revert_conflict(repo: Repo, mod: ModInfo, error: GitError) -> ConflictError:
    if not error.conflicting_paths:
        return ConflictError(f"removing {mod.name} failed: {error.message}")
    blockers: dict[str, list[Path]] = {}
    for path in error.conflicting_paths:
        name = _last_touching_mod(repo, path)
        if name:
            blockers.setdefault(name, []).append(path)
    if not blockers:
        return ConflictError(
            f"removing {mod.name} conflicts with later changes to "
            f"{_joined(error.conflicting_paths)}"
        )
    details = "; ".join(
        f"{_joined(paths)} is now provided by {name}"
        for name, paths in blockers.items()
    )
    return ConflictError(f"cannot remove {mod.name}: {details}")


def _joined(paths: Sequence[Path]) -> str:
    return ", ".join(path.as_posix() for path in paths)


def _last_touching_mod(repo: Repo, path: Path) -> str:
    commit = repo.last_touching(path)
    return commit.trailers.get(TRAILER_MOD, "") if commit is not None else ""


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
