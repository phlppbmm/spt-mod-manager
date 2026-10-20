"""Building blocks shared by the service modules.

Constants, repo mod lookups, game-layout path translation and the progress and
catalog plumbing every other module in :mod:`sptmod.core.services` builds on.
Nothing here is part of the public service surface; the package facade does not
re-export it.
"""

import shutil
import sqlite3
import stat
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from sptmod.core.archive import (
    CLIENT_PATCHERS_TARGET,
    SERVER_MODS_TARGET,
    ArchiveLayout,
)
from sptmod.core.errors import (
    AmbiguousModError,
    CatalogUnavailableError,
    ModFileWriteError,
    ModNotFoundError,
    NotAGameDirError,
)
from sptmod.core.forge.catalog import Catalog
from sptmod.core.game_dir import (
    PLUGINS_DIR,
    GameLayout,
    detect_layout,
    is_spt_own_path,
    resolve_spt_version,
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
    ModInfo,
    ModState,
    Progress,
)
from sptmod.core.modmeta import mod_info_from_commit
from sptmod.core.repo import Repo


class InstallStep(StrEnum):
    """The phases an install or update reports progress for."""

    CHECK = "check"
    EXTRACT = "extract"
    ANCHOR = "anchor"
    METADATA = "metadata"
    CONFLICTS = "conflicts"
    COPY = "copy"
    COMMIT = "commit"


INSTALL_STEPS: Final[tuple[InstallStep, ...]] = tuple(InstallStep)


_STEP_PERCENT: Final[dict[InstallStep, int]] = {
    InstallStep.CHECK: 0,
    InstallStep.EXTRACT: 10,
    InstallStep.ANCHOR: 30,
    InstallStep.METADATA: 45,
    InstallStep.CONFLICTS: 60,
    InstallStep.COPY: 75,
    InstallStep.COMMIT: 90,
}


DEPENDENCY_KINDS: Final[tuple[ConflictKind, ...]] = (
    "missing-dependency",
    "dependency-version",
)


UNSET_DATE: Final = datetime.min

# Snapshot commits secure profiles, mod settings and updated game files; they
# carry no trailers and must not take a path's owner away from it.
SNAPSHOT_PREFIX: Final = "snapshot: "

# The subject of the snapshot securing game files an SPT update rewrote; both
# the snapshot and the undo of a version change go by it.
GAME_FILES_SNAPSHOT: Final = f"{SNAPSHOT_PREFIX}game files updated"


def current_mods(repo: Repo) -> list[ModInfo]:
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


def resolve_mod(mods: Sequence[ModInfo], name: str) -> ModInfo:
    """Return the one mod of ``mods`` named ``name``, matched case-insensitively.

    Raises:
        ModNotFoundError: no mod carries that name.
        AmbiguousModError: several mods differ only in casing.
    """
    matches = [mod for mod in mods if mod.name.casefold() == name.casefold()]
    candidates = {mod.name for mod in matches}
    if not matches:
        raise ModNotFoundError(f"no installed mod named {name!r}")
    if len(candidates) > 1:
        raise AmbiguousModError(name, sorted(candidates))
    return matches[0]


def install_commit(repo: Repo, name: str) -> str:
    """Return the youngest commit of ``name`` carrying the full trailer set.

    That is the commit an ``enable`` cherry-picks: the install commit, the
    commit of the newest update, or the cherry-pick of a previous ``enable``,
    which copies the message it was picked from. State-only commits -- reverts
    from ``disable`` and ``remove`` -- carry no ``Mod-Type`` and are skipped.
    """
    return next(
        commit.hash
        for commit in repo.log(trailer_filter={TRAILER_MOD: name})
        if _described_mod(commit) is not None
    )


def last_touching_mod(repo: Repo, path: Path) -> str:
    """Return the mod name of the youngest mod commit touching ``path``, or ``""``.

    Snapshot commits are skipped, because securing a profile, a mod's own
    settings or an updated game file must not take a path's owner away from
    it. A path only snapshots ever touched stays without an owner, and so does
    one whose youngest other commit carries no ``Mod`` trailer.
    """
    for commit in repo.commits_touching(path):
        if commit.subject.startswith(SNAPSHOT_PREFIX):
            continue
        return commit.trailers.get(TRAILER_MOD, "")
    return ""


def mod_prefixes(layout: GameLayout) -> tuple[Path, ...]:
    """Return the game-dir-relative paths mod content lives under."""
    return (layout.mods_dir, PLUGINS_DIR)


def owned_prefixes(layout: GameLayout) -> tuple[Path, ...]:
    """Return every game-dir-relative path a mod may own a directory under."""
    return (*mod_prefixes(layout), CLIENT_PATCHERS_TARGET)


def owning_mod_dir(entry: Path, layout: GameLayout) -> Path | None:
    """Return the mod directory ``entry`` lies inside, or ``None``.

    A file directly below a mod path -- a loose DLL in ``BepInEx/plugins/`` --
    belongs to no directory and yields ``None``.
    """
    for prefix in owned_prefixes(layout):
        if not entry.is_relative_to(prefix):
            continue
        top = prefix / entry.relative_to(prefix).parts[0]
        return top if top != entry else None
    return None


def mod_prefix_of(entry: Path, layout: GameLayout) -> Path | None:
    """Return the mod path ``entry`` lies under, or ``None`` if SPT owns it."""
    if is_spt_own_path(entry):
        return None
    return next(
        (prefix for prefix in mod_prefixes(layout) if entry.is_relative_to(prefix)),
        None,
    )


def prune_empty(root: Path, directory: Path, boundary: tuple[Path, ...]) -> None:
    """Delete ``directory`` and its empty parents, stopping at ``boundary``."""
    current = directory
    while (
        current != root
        and current.relative_to(root) not in boundary
        and current.is_dir()
        and not any(current.iterdir())
    ):
        current.rmdir()
        current = current.parent


# Leading bytes of the files Linux runs or maps as program code: a native
# binary or shared library (ELF) and a script naming its interpreter (shebang).
_PROGRAM_MAGIC: Final[tuple[bytes, ...]] = (b"\x7fELF", b"#!")
_EXECUTE_BITS: Final = 0o111


def copy_game_file(source: Path, destination: Path) -> None:
    """Copy ``source`` to ``destination``, keeping it executable when it has to run.

    The copy takes the mode ``source`` was extracted with, which carries no
    executable bit whenever the archive stored none or its extractor restores
    none (zip never does). So the file also comes out executable when the file
    it replaces was executable, or when its content is an ELF binary or shared
    library or a shebang script: content says reliably what Linux runs, a name
    such as ``SPT.Server.Linux`` or ``*.so`` does not. Execute permission goes
    to whoever may read the file.
    """
    was_executable = destination.is_file() and bool(destination.stat().st_mode & _EXECUTE_BITS)
    shutil.copy2(source, destination)
    with source.open("rb") as handle:
        is_program = handle.read(len(_PROGRAM_MAGIC[0])).startswith(_PROGRAM_MAGIC)
    if was_executable or is_program:
        mode = stat.S_IMODE(destination.stat().st_mode)
        destination.chmod(mode | (mode & 0o444) >> 2)


def write_mod_files(
    root: Path, layout: ArchiveLayout, removed: Sequence[Path] = ()
) -> None:
    """Delete the ``removed`` files below ``root`` and copy ``layout`` in.

    Raises:
        ModFileWriteError: a file could not be deleted or written; the message
            names the file it stopped at and what the system said.
    """
    reached = Path()
    try:
        for entry in removed:
            reached = entry
            (root / entry).unlink(missing_ok=True)
        for mapping in layout.mappings:
            for source in sorted(mapping.source.rglob("*")):
                if not source.is_file():
                    continue
                reached = mapping.target / source.relative_to(mapping.source)
                destination = root / reached
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_game_file(source, destination)
    except OSError as error:
        raise ModFileWriteError(
            f"The mod files could not be written: {error.strerror}. The change "
            f"stopped at {reached.as_posix()}; your previous files are one "
            f"snapshot back."
        ) from error


def for_game_layout(layout: ArchiveLayout, game_layout: GameLayout) -> ArchiveLayout:
    """Point the canonical server targets of ``layout`` at the detected runtime dir.

    Archives ship their content below ``SPT/`` or ``SPT_Runtime/`` regardless
    of the installation they are meant for, so ``find_anchor`` normalizes both
    to the canonical ``SPT/user/mods`` and the install translates that prefix
    to the runtime directory the game directory actually uses.
    """
    if game_layout.mods_dir == SERVER_MODS_TARGET:
        return layout
    return replace(
        layout,
        mappings=[
            replace(mapping, target=_to_game_layout(mapping.target, game_layout))
            for mapping in layout.mappings
        ],
        files=[_to_game_layout(file, game_layout) for file in layout.files],
    )


def _to_game_layout(target: Path, game_layout: GameLayout) -> Path:
    """Translate one canonical archive target to the detected layout."""
    if not target.is_relative_to(SERVER_MODS_TARGET):
        return target
    return game_layout.mods_dir / target.relative_to(SERVER_MODS_TARGET)


def report_step(
    progress: Progress | None,
    step: InstallStep,
    message: str,
    percent: int | None = None,
) -> None:
    """Report ``step`` through ``progress``, at its canonical percentage by default."""
    if progress is not None:
        progress(
            step=step,
            percent=_STEP_PERCENT[step] if percent is None else percent,
            message=message,
        )


def prefixed_progress(progress: Progress | None, prefix: str) -> Progress | None:
    """Return ``progress`` with ``prefix`` put in front of every message."""
    if progress is None:
        return None

    def report(step: str, percent: int, message: str) -> None:
        progress(step, percent, f"{prefix}: {message}")

    return report


def open_catalog(db_path: Path | None) -> Catalog:
    """Open the catalog cache, mapping filesystem and SQLite failures to a type.

    Raises:
        CatalogUnavailableError: the cache file or its directory cannot be
            created or opened.
    """
    try:
        return Catalog(db_path)
    except (OSError, sqlite3.Error) as error:
        raise CatalogUnavailableError(
            f"the catalog cache cannot be opened: {error}"
        ) from error


def detected_spt_version(path: Path) -> str | None:
    """Return the SPT version of the game directory at ``path``, or ``None``."""
    return resolve_spt_version(detect_layout(path), Repo(path))


def required_spt_version(path: Path) -> str:
    """Return the SPT version of ``path``, which Forge queries need exactly.

    Raises:
        NotAGameDirError: the version cannot be read.
    """
    version = detected_spt_version(path)
    if version is None:
        raise NotAGameDirError(
            f"the SPT version of {path} cannot be read, so Forge cannot be "
            f"queried for it"
        )
    return version


def blocked_message(name: str, blocking: Sequence[Conflict]) -> str:
    """Return the error text listing why installing ``name`` is blocked."""
    details = "; ".join(finding.message for finding in blocking)
    return f"cannot install {name}: {details} (use --force to install anyway)"
