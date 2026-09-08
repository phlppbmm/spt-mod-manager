"""Player profiles: listing, residue detection and cleanup.

Profiles are the game's own save files. This module reports what they contain,
finds the mod IDs a removed mod left behind and rewrites the profile documents
that a cleanup touches, keeping a timestamped backup of each one.
"""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sptmod.core.errors import (
    InvalidPathError,
    ModFileWriteError,
    ProfileResidueError,
)
from sptmod.core.game_dir import GameLayout, detect_layout
from sptmod.core.models import ModInfo, ProfileResidue, ProfileSummary
from sptmod.core.profile import (
    CheckResult,
    check_all,
    clean_document,
    collect_mod_ids,
    find_residues,
    parse_profile,
    profile_paths,
    profile_summaries,
    write_profile,
)
from sptmod.core.repo import Repo
from sptmod.core.services.common import current_mods, install_commit, resolve_mod
from sptmod.core.services.state import ensure_installable, take_snapshot
from sptmod.core.workspace import game_lock

# The game names its own profile backup directories this way.
_BACKUP_TIME_FORMAT: Final = "%Y-%m-%d_%H-%M-%S"


_UNTOUCHED_REASON: Final = (
    "sptmod cannot tell what these IDs are used for, so it leaves them alone"
)


@dataclass(frozen=True)
class UntouchedResidue:
    """Residue a profile cleanup leaves in place, and why it does.

    Attributes:
        residue: The residue that was not removed.
        reason: Why the cleanup cannot attribute and remove it.
    """

    residue: ProfileResidue
    reason: str


@dataclass(frozen=True)
class CleanupPlan:
    """What cleaning the profiles of one mod would remove.

    Attributes:
        mod: The mod the plan is for.
        profiles: The profile files the cleanup would change, by file name
            without the extension.
        removals: What would go, one entry per profile and category.
        untouched: What would stay, each with the reason it cannot be removed.
        warnings: Db files of the mod that could not be read and profiles that
            could not be understood.
    """

    mod: str
    profiles: list[str] = field(default_factory=list[str])
    removals: list[ProfileResidue] = field(default_factory=list[ProfileResidue])
    untouched: list[UntouchedResidue] = field(default_factory=list[UntouchedResidue])
    warnings: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class CleanupResult:
    """Outcome of :func:`clean_profile`.

    Attributes:
        mod: The mod the profiles were cleaned of.
        profiles: The profile files that changed.
        backups: The copies of those files taken beforehand, relative to the
            game directory.
        removed: What was removed, one entry per profile and category.
        untouched: What stayed, each with the reason it cannot be removed.
        snapshot: The snapshot the change was secured in, empty when no
            profile changed.
        warnings: Db files of the mod that could not be read and profiles that
            could not be understood.
    """

    mod: str
    profiles: list[str] = field(default_factory=list[str])
    backups: list[Path] = field(default_factory=list[Path])
    removed: list[ProfileResidue] = field(default_factory=list[ProfileResidue])
    untouched: list[UntouchedResidue] = field(default_factory=list[UntouchedResidue])
    snapshot: str = ""
    warnings: list[str] = field(default_factory=list[str])


def list_profiles(path: Path) -> list[ProfileSummary]:
    """Describe the player profiles of the installation at ``path``.

    Reads only. Every profile file is listed, sorted by file name; a file that
    cannot be read is listed as unreadable with the reason instead of being
    dropped.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
    """
    return profile_summaries(detect_layout(path))


def check_profiles(path: Path) -> CheckResult:
    """Report which active mods the player profiles at ``path`` still reference.

    Reads only: neither the repository nor the profiles are changed, so
    residues are reported and never block.

    Raises:
        GitError: git failed.
    """
    return check_all(Repo(path), detect_layout(path))


def plan_profile_cleanup(path: Path, mod_name: str) -> CleanupPlan:
    """Report what cleaning the profiles at ``path`` of ``mod_name`` removes.

    Reads only: neither the repository nor the profiles are changed, so the
    plan can be shown before anything is decided. It is built by the very code
    that does the cleaning, so it lists exactly what :func:`clean_profile`
    would remove. A mod that was already removed can still be planned for.

    Args:
        path: The tracked game directory.
        mod_name: The name of the mod to plan a cleanup for; matched
            case-insensitively.

    Returns:
        What would be removed and left untouched, per profile and category.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        ModNotFoundError: no mod matches ``mod_name``.
        AmbiguousModError: several mods match ``mod_name``.
        GitError: git failed.
    """
    repo = Repo(path)
    layout = detect_layout(path)
    mod = resolve_mod(current_mods(repo), mod_name)
    ids = collect_mod_ids(repo, install_commit(repo, mod.name), layout)
    warnings = list(ids.warnings)
    profiles: list[str] = []
    removals: list[ProfileResidue] = []
    untouched: list[UntouchedResidue] = []
    for target in profile_paths(layout):
        parsed = parse_profile(target)
        if parsed is None:
            warnings.append(_unreadable_profile(target))
            continue
        cleaned = clean_document(parsed, ids)
        untouched.extend(_untouched_in(cleaned.untouched, target.stem))
        if cleaned.removed:
            profiles.append(target.stem)
            removals.extend(_found_in(cleaned.removed, target.stem))
    return CleanupPlan(
        mod=mod.name,
        profiles=profiles,
        removals=removals,
        untouched=untouched,
        warnings=warnings,
    )


def clean_profile(
    path: Path, mod_name: str, profiles: list[str] | None = None
) -> CleanupResult:
    """Remove the content of ``mod_name`` from the player profiles at ``path``.

    Cleans every profile, or only the ones named in ``profiles`` by their file
    name without the extension. A copy of each profile that changes is put
    into the backups folder the game uses itself, and the change is secured in
    a snapshot afterwards. Profiles without anything to remove are not
    touched at all.

    Args:
        path: The tracked game directory.
        mod_name: The name of the mod to clean profiles of; matched
            case-insensitively.
        profiles: The file names (without extension) of profiles to clean, or
            ``None`` to clean every profile.

    Returns:
        What was removed and left untouched, the backup copies created, and
        the snapshot commit hash, empty when nothing changed.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        ModNotFoundError: no mod matches ``mod_name``.
        AmbiguousModError: several mods match ``mod_name``.
        InvalidPathError: there is no profile with one of the given names.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        OperationInProgressError: another operation holds the game directory.
        ModFileWriteError: a profile could not be backed up or written.
        GitError: git failed.
    """
    repo = Repo(path)
    with game_lock(path, f"cleaning profiles for {mod_name}"):
        ensure_installable(repo)
        mod = resolve_mod(current_mods(repo), mod_name)
        return clean_profiles_inline(repo, mod, profiles)


def clean_profiles_inline(
    repo: Repo, mod: ModInfo, profiles: list[str] | None = None
) -> CleanupResult:
    """Clean the profiles of ``mod`` without taking the lock.

    Backs a profile up before it is written, so the copy in the game's own
    backups folder always holds the state before the cleanup; the snapshot
    afterwards leaves the tree clean for whatever the caller does next.
    """
    layout = detect_layout(repo.path)
    ids = collect_mod_ids(repo, install_commit(repo, mod.name), layout)
    snapshot_message = f"cleaned profiles for {mod.name}"
    stamp = datetime.now(UTC).strftime(_BACKUP_TIME_FORMAT)
    backup_dir = layout.root / layout.profile_backups_dir / stamp
    warnings = list(ids.warnings)
    changed: list[str] = []
    backups: list[Path] = []
    removed: list[ProfileResidue] = []
    untouched: list[UntouchedResidue] = []
    for target in _selected_profiles(layout, profiles):
        parsed = parse_profile(target)
        if parsed is None:
            warnings.append(_unreadable_profile(target))
            continue
        cleaned = clean_document(parsed, ids)
        untouched.extend(_untouched_in(cleaned.untouched, target.stem))
        if not cleaned.removed:
            continue
        backup = backup_dir / target.name
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            write_profile(target, cleaned.document)
        except OSError as error:
            if changed:
                take_snapshot(repo.path, snapshot_message)
            raise ModFileWriteError(
                _cleanup_failed(target, changed, error)
            ) from error
        changed.append(target.stem)
        backups.append(backup.relative_to(layout.root))
        removed.extend(_found_in(cleaned.removed, target.stem))
    snapshot_hash = ""
    if changed:
        # No mod trailer: the youngest commit carrying one is taken as the
        # mod's own, which would point it at a profile commit holding none of
        # its files.
        snapshot_hash = take_snapshot(repo.path, snapshot_message).commit
    return CleanupResult(
        mod=mod.name,
        profiles=changed,
        backups=backups,
        removed=removed,
        untouched=untouched,
        snapshot=snapshot_hash,
        warnings=warnings,
    )


def check_profile_residue(repo: Repo, mod: ModInfo, force: bool) -> list[str]:
    """Search every profile for content IDs of ``mod``.

    Returns the collection warnings of ``collect_mod_ids`` and raises
    ``ProfileResidueError`` when a profile still references content of ``mod``
    unless ``force`` is set.
    """
    layout = detect_layout(repo.path)
    ids = collect_mod_ids(repo, mod.commit, layout)
    residues = [
        residue
        for profile in profile_paths(layout)
        for residue in find_residues(profile, ids)
    ]
    if residues and not force:
        raise ProfileResidueError(_residue_message(mod.name, residues), residues)
    return ids.warnings


def _cleanup_failed(target: Path, changed: Sequence[str], error: OSError) -> str:
    """Return the message for a cleanup that stopped at ``target``.

    Names the profiles that were already cleaned in the same run, so nobody
    has to guess how far the cleanup got.
    """
    message = f"the player profile {target.name} could not be changed: {error}"
    if not changed:
        return message
    return (
        f"{message}. Already cleaned: {', '.join(changed)} -- a copy of each "
        "is in the game's backups folder"
    )


def _selected_profiles(layout: GameLayout, profiles: list[str] | None) -> list[Path]:
    """Return the profile files named by ``profiles``, or all of them."""
    available = profile_paths(layout)
    if profiles is None:
        return available
    by_name = {path.stem: path for path in available}
    missing = sorted(set(profiles) - set(by_name))
    if missing:
        raise InvalidPathError(f"there is no player profile named {', '.join(missing)}")
    return [by_name[name] for name in profiles]


def _found_in(residues: Sequence[ProfileResidue], name: str) -> list[ProfileResidue]:
    """Return ``residues`` marked as found in the profile ``name``."""
    return [replace(residue, profile=name) for residue in residues]


def _untouched_in(
    residues: Sequence[ProfileResidue], name: str
) -> list[UntouchedResidue]:
    """Return the residues of profile ``name`` a cleanup has to leave alone."""
    return [
        UntouchedResidue(residue=residue, reason=_UNTOUCHED_REASON)
        for residue in _found_in(residues, name)
    ]


def _unreadable_profile(target: Path) -> str:
    return f"{target.name} could not be read and was left unchanged"


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
