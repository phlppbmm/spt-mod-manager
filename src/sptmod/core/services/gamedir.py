"""The game directory itself: browsing, locating, setup and launching.

Everything that happens before or around mod management: finding an SPT
installation on disk, turning one into a managed directory, recording its SPT
version and starting or stopping the game.
"""

import os.path
import shutil
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from sptmod.core.errors import (
    AlreadyInitializedError,
    InvalidPathError,
    SptVersionUnknownError,
)
from sptmod.core.game_dir import (
    GameLayout,
    is_game_dir,
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
    ModInfo,
    Progress,
)
from sptmod.core.repo import Repo
from sptmod.core.services.common import (
    mod_prefix_of,
)
from sptmod.core.services.lifecycle import (
    adopt_mod_at,
)
from sptmod.core.workspace import (
    game_lock,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


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
    reported as well, and marked as one sptmod cannot close.
    """
    return _launch_status(path)


def stop_game(path: Path) -> StopResult:
    """Close the game and the SPT server sptmod started at ``path``.

    Only what sptmod started here is closed, together with everything that game
    started; a server or game that was already running is left alone. Progress
    the game has not saved yet is lost, so callers ask before calling this. The
    result says in plain words what was closed.
    """
    return _stop_game(path)


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
