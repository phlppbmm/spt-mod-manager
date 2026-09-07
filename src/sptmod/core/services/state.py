"""Read-only state queries, the installability guard and snapshots.

What the manager knows about a game directory without changing it: the mod
list, a mod's files, the commit history and the interpreted working-tree
status. The guard :func:`ensure_installable` and the lock-free
:func:`take_snapshot` live here too, because both build on :func:`status`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from sptmod.core.errors import DirtyTreeError, GitError
from sptmod.core.game_dir import (
    BEPINEX_CONFIG_DIR,
    GameLayout,
    detect_layout,
    ensure_game_not_running,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    TRAILER_MOD,
    CommitInfo,
    ModFile,
    ModInfo,
)
from sptmod.core.repo import Repo
from sptmod.core.services.common import (
    SNAPSHOT_PREFIX,
    current_mods,
    last_touching_mod,
    mod_prefix_of,
    owning_mod_dir,
    resolve_mod,
)
from sptmod.core.workspace import game_lock

_SNAPSHOT_TIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
_GAME_FILES_MESSAGE: Final = f"{SNAPSHOT_PREFIX}game files updated"
_MOD_SETTINGS_MESSAGE: Final = f"{SNAPSHOT_PREFIX}mod settings changed"


@dataclass(frozen=True)
class ManualMod:
    """Untracked files below a mod path, grouped by their mod folder.

    ``path`` is the mod folder, or the file itself for a loose plugin,
    relative to the game directory; ``files`` are the untracked files it
    contains.
    """

    path: Path
    files: list[Path] = field(default_factory=list[Path])


@dataclass(frozen=True)
class StatusReport:
    """The interpreted working-tree status of a game directory.

    ``modified_mod_settings`` holds the tracked files an active mod rewrote
    inside its own directory; ``game_files_changed`` holds the changed and
    deleted files of the SPT installation itself, which no mod is responsible
    for. ``other_modified`` and ``other_untracked`` hold everything that is
    neither a profile, a BepInEx config, a mod setting, a game file nor an
    untracked file below a mod path. ``spt_version`` is the locally resolved
    SPT version, empty when unknown.
    """

    spt_version: str = ""
    modified_profiles: list[Path] = field(default_factory=list[Path])
    modified_configs: list[Path] = field(default_factory=list[Path])
    modified_mod_settings: list[Path] = field(default_factory=list[Path])
    manual_mods: list[ManualMod] = field(default_factory=list[ManualMod])
    game_files_changed: list[Path] = field(default_factory=list[Path])
    other_modified: list[Path] = field(default_factory=list[Path])
    other_untracked: list[Path] = field(default_factory=list[Path])


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of :func:`snapshot`."""

    commit: str
    message: str
    files: list[Path] = field(default_factory=list[Path])


def list_mods(path: Path, include_all: bool = False) -> list[ModInfo]:
    """Return the mods known to the repository at ``path``, newest first.

    The state of a mod is the state of the youngest commit carrying its
    ``Mod:`` trailer. By default only active mods are returned, meaning mods
    whose state is one of ``ACTIVE_STATES``; ``include_all`` also returns
    disabled and removed ones.

    Raises:
        GitError: git failed.
    """
    mods = current_mods(Repo(path))
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
    mod = resolve_mod(current_mods(repo), name)
    return repo.show_files(mod.commit)


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
    mod = resolve_mod(current_mods(repo), name)
    return repo.log(trailer_filter={TRAILER_MOD: mod.name})


def status(path: Path) -> StatusReport:
    """Interpret the working-tree status of the game directory at ``path``.

    Sorts the modified files into player profiles, which change normally
    while playing and belong into a :func:`snapshot`, BepInEx configs,
    settings an active mod rewrote inside its own directory, files of the SPT
    installation itself and everything else. A changed or deleted tracked
    file below an active mod's directory is that mod's own doing and is
    reported under ``modified_mod_settings``; one no mod commit accounts for
    at all belongs to SPT, not to a mod -- an SPT update rewrote it -- and is
    reported under ``game_files_changed``. Untracked files below the mod
    paths are grouped per mod folder as manual installs a caller can offer to
    :func:`adopt_mod`; all other untracked files are reported as they are. Untracked files below the
    profile and config paths join the modified ones there, because a
    :func:`snapshot` commits them together. Untracked files an active mod
    writes inside its own directory are runtime data and are left out of the
    report entirely.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        GitError: git failed.
    """
    layout = detect_layout(path)
    repo = Repo(path)
    raw = repo.status()
    modified_profiles: list[Path] = []
    modified_configs: list[Path] = []
    changed_rest: list[Path] = []
    modified_mod_settings: list[Path] = []
    game_files_changed: list[Path] = []
    other_modified: list[Path] = []
    other_untracked: list[Path] = []
    manual_files: dict[Path, list[Path]] = {}

    for entry in raw.modified:
        _group_snapshot_path(
            entry, layout, modified_profiles, modified_configs, changed_rest
        )
    changed_in = _active_mod_dirs(repo, changed_rest, layout)
    for entry in changed_rest:
        if owning_mod_dir(entry, layout) in changed_in:
            modified_mod_settings.append(entry)
        elif last_touching_mod(repo, entry):
            other_modified.append(entry)
        else:
            game_files_changed.append(entry)

    owned = _active_mod_dirs(repo, raw.untracked, layout)
    for entry in raw.untracked:
        if owning_mod_dir(entry, layout) in owned:
            continue
        prefix = mod_prefix_of(entry, layout)
        if prefix is not None:
            top = prefix / entry.relative_to(prefix).parts[0]
            manual_files.setdefault(top, []).append(entry)
            continue
        _group_snapshot_path(
            entry, layout, modified_profiles, modified_configs, other_untracked
        )

    return StatusReport(
        spt_version=resolve_spt_version(layout, repo) or "",
        modified_profiles=modified_profiles,
        modified_configs=modified_configs,
        modified_mod_settings=modified_mod_settings,
        manual_mods=[
            ManualMod(path=top, files=files) for top, files in manual_files.items()
        ],
        game_files_changed=game_files_changed,
        other_modified=other_modified,
        other_untracked=other_untracked,
    )


def snapshot(path: Path, message: str | None = None) -> SnapshotResult:
    """Commit the profile and config changes of the game directory at ``path``.

    Stages only the changed files below the profile directory and
    ``BepInEx/config/`` and commits them as ``snapshot: <message>``, defaulting
    to a timestamp, without any ``Mod:`` trailer. Changes to tracked mod files
    stay uncommitted.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        GitError: neither profiles nor configs changed, or git failed.
    """
    with game_lock(path, "securing your profile"):
        return take_snapshot(path, message)


def take_snapshot(path: Path, message: str | None) -> SnapshotResult:
    """Commit profile and config changes without taking the lock."""
    repo = Repo(path)
    text = message or datetime.now().strftime(_SNAPSHOT_TIME_FORMAT)
    report = status(path)
    paths = [*report.modified_profiles, *report.modified_configs]
    if not paths:
        raise GitError(f"{path} has no profile or config changes to snapshot")
    commit_hash = repo.commit(f"{SNAPSHOT_PREFIX}{text}", paths=paths)
    return SnapshotResult(
        commit=commit_hash,
        message=text,
        files=[mod_file.path for mod_file in repo.show_files(commit_hash)],
    )


def ensure_installable(repo: Repo) -> None:
    """Ensure the game is closed and the working tree is clean.

    Changed profiles and BepInEx configs are the normal aftermath of playing
    and are silently secured in an automatic snapshot commit, so the user
    never has to know about the underlying version control. Settings an active
    mod rewrote inside its own directory are secured the same way, and so are
    game files an SPT update rewrote or deleted; after the latter the SPT
    version is read again and stored, because an update usually raises it.
    Only untracked files the manager cannot account for (manually copied mods)
    and changed mod files no active mod owns stop the operation, in plain
    language.
    """
    ensure_game_not_running()
    if repo.is_clean():
        return
    report = status(repo.path)
    _reject_unmanaged(report)
    if report.game_files_changed:
        repo.commit(_GAME_FILES_MESSAGE, paths=report.game_files_changed)
        _store_detected_version(repo)
    if report.modified_mod_settings:
        repo.commit(_MOD_SETTINGS_MESSAGE, paths=report.modified_mod_settings)
    if report.modified_profiles or report.modified_configs:
        take_snapshot(repo.path, "automatic snapshot before changing mods")


def _store_detected_version(repo: Repo) -> None:
    """Store the SPT version the game directory now reports, if it reports one."""
    version = resolve_spt_version(detect_layout(repo.path), repo)
    if version is not None:
        store_spt_version(repo, version)


def _reject_unmanaged(report: StatusReport) -> None:
    blockers = [str(mod.path) for mod in report.manual_mods]
    blockers += [str(p) for p in report.other_modified + report.other_untracked]
    if not blockers:
        return
    listed = ", ".join(blockers[:5]) + (" …" if len(blockers) > 5 else "")
    raise DirtyTreeError(
        "there are files in the game folder sptmod does not manage: "
        f"{listed}. If these are mods you installed manually, add them "
        "via adopt; otherwise move them out of the game folder."
    )


def _group_snapshot_path(
    entry: Path,
    layout: GameLayout,
    profiles: list[Path],
    configs: list[Path],
    rest: list[Path],
) -> None:
    if entry.is_relative_to(layout.profiles_dir):
        profiles.append(entry)
    elif entry.is_relative_to(BEPINEX_CONFIG_DIR):
        configs.append(entry)
    else:
        rest.append(entry)


def _active_mod_dirs(
    repo: Repo, entries: Sequence[Path], layout: GameLayout
) -> set[Path]:
    """Return the directories among ``entries`` an active mod provides.

    Mods write runtime data -- editor defaults, generated overrides -- into
    their own directory while the game runs. Those files are neither a manual
    install nor tampering, so callers drop them.
    """
    candidates = {
        directory
        for entry in entries
        if (directory := owning_mod_dir(entry, layout)) is not None
    }
    if not candidates:
        return candidates
    active = {mod.name for mod in current_mods(repo) if mod.state in ACTIVE_STATES}
    return {
        directory
        for directory in candidates
        if last_touching_mod(repo, directory) in active
    }
