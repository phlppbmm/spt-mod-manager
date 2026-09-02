"""Dataclasses and trailer constants shared across the sptmod core."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

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
