"""Dataclasses and trailer constants shared across the sptmod core."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, Protocol

type ModType = Literal["server", "client", "hybrid"]
type ModState = Literal["installed", "updated", "disabled", "enabled", "removed"]
type FileChange = Literal["added", "modified", "deleted"]

MOD_TYPES: Final[tuple[ModType, ...]] = ("server", "client", "hybrid")
MOD_STATES: Final[tuple[ModState, ...]] = (
    "installed",
    "updated",
    "disabled",
    "enabled",
    "removed",
)

TRAILER_MOD: Final = "Mod"
TRAILER_MOD_VERSION: Final = "Mod-Version"
TRAILER_MOD_TYPE: Final = "Mod-Type"
TRAILER_MOD_AUTHOR: Final = "Mod-Author"
TRAILER_MOD_SPT: Final = "Mod-SPT"
TRAILER_MOD_SOURCE: Final = "Mod-Source"
TRAILER_MOD_FORGE_ID: Final = "Mod-Forge-Id"
TRAILER_MOD_FORGE_VERSION_ID: Final = "Mod-Forge-Version-Id"
TRAILER_MOD_GUID: Final = "Mod-Guid"
TRAILER_MOD_STATE: Final = "Mod-State"
TRAILER_MOD_PREVIOUS_VERSION: Final = "Mod-Previous-Version"

MOD_TRAILER_KEYS: Final[tuple[str, ...]] = (
    TRAILER_MOD,
    TRAILER_MOD_VERSION,
    TRAILER_MOD_TYPE,
    TRAILER_MOD_AUTHOR,
    TRAILER_MOD_SPT,
    TRAILER_MOD_SOURCE,
    TRAILER_MOD_FORGE_ID,
    TRAILER_MOD_FORGE_VERSION_ID,
    TRAILER_MOD_GUID,
    TRAILER_MOD_STATE,
)


@dataclass(frozen=True)
class ModInfo:
    """A mod as described by the trailers of its most recent commit."""

    name: str
    version: str
    mod_type: ModType
    author: str
    spt_range: str
    state: ModState
    commit: str
    date: datetime
    forge_id: str
    forge_version_id: str
    guid: str
    source: str


@dataclass(frozen=True)
class ModFile:
    """A file touched by a commit, relative to the game directory."""

    path: Path
    change: FileChange


@dataclass(frozen=True)
class CommitInfo:
    """A commit with its parsed trailers."""

    hash: str
    subject: str
    date: datetime
    trailers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RepoStatus:
    """Parsed porcelain status of the game repository."""

    modified: list[Path] = field(default_factory=list)
    untracked: list[Path] = field(default_factory=list)


type ConflictKind = Literal[
    "tracked-file",
    "untracked-file",
    "dll-duplicate",
    "missing-dependency",
    "dependency-version",
    "spt-version",
]
type ConflictSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class Conflict:
    """A finding of the pre-install conflict and dependency checks.

    Attributes:
        kind: The machine-readable check that produced the finding.
        severity: ``'error'`` blocks the install unless ``force`` is set,
            ``'warning'`` is only reported.
        message: The human-readable line for the CLI, including the hint at
            ``--force`` or ``adopt`` where one applies.
        path: The game-directory-relative file the finding is about, or
            ``None`` for dependency and SPT-version findings.
        mod: The mod the finding points at: the owning mod for file
            conflicts, the dependency name for dependency findings, empty
            when no mod can be named.
    """

    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    path: Path | None = None
    mod: str = ""


class Progress(Protocol):
    """Callback reporting the progress of a long-running operation.

    ``step`` is a stable machine-readable step name, ``percent`` the overall
    progress of the operation in the range 0 to 100, and ``message`` a
    human-readable detail line. Implementations must not raise.
    """

    def __call__(self, step: str, percent: int, message: str) -> None: ...


@dataclass(frozen=True)
class PlanEntry:
    """One mod to install as part of an :class:`InstallPlan`.

    Attributes:
        mod_id: The numeric Forge mod ID.
        name: The mod name as known to Forge.
        guid: The mod GUID, or None if Forge knows none.
        version: The version string to install.
        version_id: The numeric Forge version ID.
        link: The download URL, or None if Forge published none.
        content_length: The archive size in bytes, or None if Forge does not
            report one.
        is_dependency: True when the entry was pulled in as a dependency
            rather than requested by the user.
    """

    mod_id: int
    name: str
    guid: str | None
    version: str
    version_id: int
    link: str | None
    content_length: int | None
    is_dependency: bool


@dataclass(frozen=True)
class InstallPlan:
    """What an install would download and install, in install order.

    Attributes:
        entries: The mods to install, dependencies before the mods that need
            them and the requested mod last. Already satisfied dependencies
            are absent.
        spt_version: The SPT version the plan was resolved for.
    """

    entries: list[PlanEntry]
    spt_version: str
