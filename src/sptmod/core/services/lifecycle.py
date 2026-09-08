"""The life of an installed mod: remove, disable, enable and adopt.

Removing and toggling work on the files a mod owns and leave everything else in
place; adopting takes files that are already in the game directory and puts them
under management without moving them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sptmod.core.errors import (
    ConflictError,
    GitError,
    ModNotFoundError,
    NotAGameDirError,
)
from sptmod.core.game_dir import (
    PLUGINS_DIR,
    GameLayout,
    detect_layout,
    is_spt_own_path,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    ModInfo,
    ModState,
    ModType,
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
from sptmod.core.services.common import (
    UNSET_DATE,
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
    reject_multiline_metadata,
)
from sptmod.core.services.profiles import (
    CleanupResult,
    check_profile_residue,
    clean_profiles_inline,
)
from sptmod.core.services.state import (
    ensure_installable,
)
from sptmod.core.workspace import (
    game_lock,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


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
        ModFileWriteError: a profile could not be backed up or written.
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
                extra_trailers={
                    TRAILER_MOD: mod.name,
                    TRAILER_MOD_STATE: ModState.REMOVED,
                },
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
                extra_trailers={
                    TRAILER_MOD: mod.name,
                    TRAILER_MOD_STATE: ModState.DISABLED,
                },
            )
        except GitError as error:
            raise _sequencer_conflict(repo, mod, error, "disable") from error
        _drop_runtime_data(repo, commit_hash, detect_layout(path))

    return ToggleResult(
        name=mod.name,
        state=ModState.DISABLED,
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
        if mod.state != ModState.DISABLED:
            return ToggleResult(
                name=mod.name,
                state=mod.state,
                notice=f"{mod.name} is {mod.state}, not disabled, nothing to enable",
            )
        ensure_installable(repo)

        try:
            commit_hash = repo.cherry_pick(
                install_commit(repo, mod.name),
                extra_trailers={
                    TRAILER_MOD: mod.name,
                    TRAILER_MOD_STATE: ModState.ENABLED,
                },
            )
        except GitError as error:
            raise _sequencer_conflict(repo, mod, error, "enable") from error

    return ToggleResult(
        name=mod.name,
        state=ModState.ENABLED,
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
        mod_type=(
            ModType.SERVER
            if relative.is_relative_to(layout.mods_dir)
            else ModType.CLIENT
        ),
        author=meta.author,
        spt_range=meta.spt_range,
        state=ModState.INSTALLED,
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
