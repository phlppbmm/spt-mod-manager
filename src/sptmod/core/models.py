"""Dataclasses and trailer constants shared across the sptmod core."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Protocol

# Any value json.load/json.loads can produce. Declaring a parsed document as
# Json keeps isinstance narrowing typed instead of falling back to Any.
type Json = str | int | float | bool | None | list[Json] | dict[str, Json]

type FileChange = Literal["added", "modified", "deleted"]


class ModType(StrEnum):
    """How a mod hooks into the game: server-side, client-side or both."""

    SERVER = "server"
    CLIENT = "client"
    HYBRID = "hybrid"


class ModState(StrEnum):
    """The lifecycle state a mod's youngest commit puts it in."""

    INSTALLED = "installed"
    UPDATED = "updated"
    DISABLED = "disabled"
    ENABLED = "enabled"
    REMOVED = "removed"


MOD_TYPES: Final[tuple[ModType, ...]] = tuple(ModType)
MOD_STATES: Final[tuple[ModState, ...]] = tuple(ModState)
ACTIVE_STATES: Final[tuple[ModState, ...]] = (
    ModState.INSTALLED,
    ModState.UPDATED,
    ModState.ENABLED,
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
    trailers: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class RepoStatus:
    """Parsed porcelain status of the game repository."""

    modified: list[Path] = field(default_factory=list[Path])
    untracked: list[Path] = field(default_factory=list[Path])


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


class ResidueCategory(StrEnum):
    """Which part of a player profile a mod's IDs were found in."""

    ITEM = "item"
    ENCYCLOPEDIA = "encyclopedia"
    WISHLIST = "wishlist"
    TRADER = "trader"
    TRADER_PURCHASE = "trader-purchase"
    QUEST = "quest"
    REPEATABLE_QUEST = "repeatable-quest"
    TASK_COUNTER = "task-counter"
    HIDEOUT_PRODUCTION = "hideout-production"
    HIDEOUT_AREA = "hideout-area"
    UNLOCKED_RECIPE = "unlocked-recipe"
    INSURED_ITEM = "insured-item"
    ACHIEVEMENT = "achievement"
    INSURANCE = "insurance"
    DIALOGUE = "dialogue"
    USERBUILD = "userbuild"
    STRING = "string"


@dataclass(frozen=True)
class ProfileSummary:
    """One player profile file of the installation, as it presents itself.

    Attributes:
        file: The name of the profile file, for example ``'abc.json'``.
        profile_id: The file name without the extension, the same value
            :class:`ProfileResidue` names a profile by.
        nickname: The name of the PMC character, empty when the profile
            carries none yet.
        level: The level of the PMC character, ``0`` when the profile carries
            none yet.
        side: The faction of the PMC character, for example ``'Usec'``, empty
            when the profile carries none yet.
        modified: When the file was last written, as an ISO 8601 timestamp in
            UTC.
        readable: Whether the file could be read and understood.
        error: Why the file could not be read, empty when it could.
    """

    file: str
    profile_id: str
    nickname: str = ""
    level: int = 0
    side: str = ""
    modified: str = ""
    readable: bool = True
    error: str = ""


@dataclass(frozen=True)
class ProfileResidue:
    """IDs of one mod that one player profile still references.

    Attributes:
        profile: The profile the IDs were found in, named by the stem of its
            profile file.
        category: Which part of the profile the IDs were found in:

            ``'item'``
                Inventory items whose template is the mod's, plus every item
                stored inside them.
            ``'encyclopedia'``
                Encyclopedia entries for the mod's item templates.
            ``'wishlist'``
                Wish list entries for the mod's item templates.
            ``'trader'``
                Trader standing entries of the mod's traders.
            ``'trader-purchase'``
                Purchase counters of the mod's traders.
            ``'quest'``
                Accepted or finished quests of the mod.
            ``'repeatable-quest'``
                Daily and weekly quests belonging to the mod's quests or
                traders.
            ``'task-counter'``
                Progress counters of the mod's quests.
            ``'hideout-production'``
                Running hideout productions of the mod's recipes.
            ``'hideout-area'``
                Hideout area slots holding the mod's items.
            ``'unlocked-recipe'``
                Unlocked hideout recipes of the mod.
            ``'insured-item'``
                Insurance entries for the mod's traders or items.
            ``'achievement'``
                Achievements of the mod.
            ``'insurance'``
                Pending insurance returns from the mod's traders or holding
                the mod's items.
            ``'dialogue'``
                Messages of the mod's traders or about the mod's quests and
                items.
            ``'userbuild'``
                Saved weapon, equipment and magazine builds using the mod's
                items.
            ``'string'``
                The conservative fallback that only found the ID somewhere in
                the profile text, without knowing what it is used for.
        ids: The distinct IDs found, sorted.
        count: How many entries of that part of the profile the IDs account
            for, so an item template used by five inventory items counts five.
        character: The character the IDs were found in, ``'pmc'`` or
            ``'scav'``; empty for the shared profile sections and for
            ``'string'``.
        path: JSON pointer of the container the IDs were found in, for example
            ``'/characters/pmc/Inventory/items'``; empty for ``'string'``.
    """

    profile: str
    category: ResidueCategory
    ids: list[str]
    count: int
    character: str = ""
    path: str = ""
