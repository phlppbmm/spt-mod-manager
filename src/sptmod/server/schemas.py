"""Pydantic models mirroring the core dataclasses: they are the API contract.

Every service function of :mod:`sptmod.core.services` has one request model
carrying its parameters and one response model carrying its return shape, with
identical snake_case names for both functions and fields.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sptmod.core.forge.catalog import CatalogState
from sptmod.core.models import (
    ConflictKind,
    ConflictSeverity,
    FileChange,
    ModState,
    ModType,
    ResidueCategory,
)
from sptmod.core.services import BrowseFilter, ExportFormat


class CoreModel(BaseModel):
    """Base of every contract model; reads its values off the core dataclasses."""

    model_config = ConfigDict(from_attributes=True)


class ModInfo(CoreModel):
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


class ModFile(CoreModel):
    """A file touched by a commit, relative to the game directory."""

    path: Path
    change: FileChange


class CommitInfo(CoreModel):
    """A commit with its parsed trailers."""

    hash: str
    subject: str
    date: datetime
    trailers: dict[str, str] = {}


class Conflict(CoreModel):
    """A finding of the pre-install conflict and dependency checks."""

    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    path: Path | None = None
    mod: str = ""


class ProfileResidue(CoreModel):
    """IDs of one mod that one player profile still references."""

    profile: str
    category: ResidueCategory
    ids: list[str]
    count: int


class CheckResult(CoreModel):
    """What a residue check over all mods and all profiles found."""

    residues: dict[str, list[ProfileResidue]] = {}
    warnings: list[str] = []


class CatalogMod(CoreModel):
    """A mod as cached in the local Forge catalog."""

    id: int
    hub_id: int | None
    guid: str | None
    name: str
    slug: str
    teaser: str | None
    thumbnail: str | None
    author: str
    downloads: int
    detail_url: str
    published_at: str | None
    created_at: str | None
    updated_at: str | None
    favourites_count: int = 0
    endorsements_count: int = 0
    fika_compatibility: bool | None = None
    featured: bool = False
    contains_ads: bool = False
    contains_ai_content: bool = False
    category: str | None = None
    license_name: str | None = None
    license_link: str | None = None
    source_links: list["CatalogSourceLink"] = []


class CatalogSourceLink(CoreModel):
    """A labeled source-code link of a catalog mod."""

    url: str
    label: str


class CatalogVersion(CoreModel):
    """A mod version as cached in the local Forge catalog."""

    id: int
    mod_id: int
    version: str
    link: str | None
    content_length: int | None
    spt_version_constraint: str
    downloads: int
    published_at: str | None
    updated_at: str | None


class CatalogEntry(CoreModel):
    """A cached mod joined with the installed state of the game repository."""

    mod: CatalogMod
    state: CatalogState
    installed_version: str | None
    latest_version: CatalogVersion | None


class SyncResult(CoreModel):
    """Counts of what a catalog sync run changed."""

    new_mods: int
    updated_mods: int
    new_versions: int
    updated_versions: int
    pages: int
    full: bool


class InitResult(CoreModel):
    """Outcome of ``init_game_dir``."""

    game_dir: Path
    commit: str
    spt_version: str
    warnings: list[str] = []
    pre_existing_mods: list[Path] = []
    adopted: list[ModInfo] = []


class InstallResult(CoreModel):
    """Outcome of ``install_from_archive``."""

    mod: ModInfo
    files: list[Path] = []
    warnings: list[Conflict] = []


class RemoveResult(CoreModel):
    """Outcome of ``remove_mod``."""

    name: str
    commit: str
    files: list[Path] = []
    warnings: list[str] = []


class ToggleResult(CoreModel):
    """Outcome of ``disable_mod`` and ``enable_mod``."""

    name: str
    state: ModState
    commit: str = ""
    files: list[Path] = []
    notice: str = ""


class AdoptResult(CoreModel):
    """Outcome of ``adopt_mod``."""

    mod: ModInfo
    files: list[Path] = []


class ManualMod(CoreModel):
    """Untracked files below a mod path, grouped by their mod folder."""

    path: Path
    files: list[Path] = []


class StatusReport(CoreModel):
    """The interpreted working-tree status of a game directory."""

    modified_profiles: list[Path] = []
    modified_configs: list[Path] = []
    manual_mods: list[ManualMod] = []
    other_modified: list[Path] = []
    other_untracked: list[Path] = []


class SnapshotResult(CoreModel):
    """Outcome of ``snapshot``."""

    commit: str
    message: str
    files: list[Path] = []


class ImportEntry(CoreModel):
    """One mod an imported list and the installed mods disagree about."""

    name: str
    wanted_version: str = ""
    installed_version: str = ""
    link: str = ""


class PlanEntry(CoreModel):
    """One mod to install as part of an ``InstallPlan``."""

    mod_id: int
    name: str
    guid: str | None
    version: str
    version_id: int
    link: str | None
    content_length: int | None
    is_dependency: bool


class InstallPlan(CoreModel):
    """What an install would download and install, in install order."""

    spt_version: str
    entries: list[PlanEntry] = []


class InstallModResult(CoreModel):
    """Outcome of ``install_mod``."""

    plan: InstallPlan
    installed: list[InstallResult] = []


class ImportDiff(CoreModel):
    """The difference between an imported mod list and the installed mods."""

    missing: list[ImportEntry] = []
    different: list[ImportEntry] = []
    extra: list[ImportEntry] = []
    installed: list[InstallModResult] = []


class UpdateCandidate(CoreModel):
    """An installed mod Forge recommends a newer version for."""

    name: str
    installed_version: str
    new_version: str
    version_id: int
    link: str = ""
    content_length: int | None = None
    reason: str = ""


class BlockedUpdate(CoreModel):
    """An installed mod whose newer version other installed mods block."""

    name: str
    installed_version: str
    new_version: str
    reason: str = ""
    blocking_mods: list[str] = []


class IncompatibleMod(CoreModel):
    """An installed mod without any version for the queried SPT release."""

    name: str
    installed_version: str
    reason: str = ""
    latest_compatible_version: str = ""


class UpdatesReport(CoreModel):
    """What ``check_updates`` found for the installed mods."""

    spt_version: str
    available: list[UpdateCandidate] = []
    blocked: list[BlockedUpdate] = []
    up_to_date: list[str] = []
    incompatible: list[IncompatibleMod] = []
    skipped: list[str] = []


class UpdateResult(CoreModel):
    """Outcome of ``update_mod``."""

    name: str
    previous_version: str
    version: str = ""
    commit: str = ""
    files: list[ModFile] = []
    warnings: list[Conflict] = []
    notice: str = ""


class DirectoryEntry(CoreModel):
    """One subdirectory of a browsed directory."""

    name: str
    path: Path
    is_game_dir: bool


class DirectoryListing(CoreModel):
    """Outcome of ``list_directories``."""

    path: Path
    parent: Path | None = None
    entries: list[DirectoryEntry] = []


class ListDirectoriesRequest(CoreModel):
    """Parameters of ``list_directories``."""

    path: Path | None = None


class LocatedDirectory(CoreModel):
    """A directory matched by ``locate_directory``."""

    path: Path
    is_game_dir: bool


class LocateDirectoryRequest(CoreModel):
    """Parameters of ``locate_directory``."""

    name: str
    fingerprint: list[str] = []


class InitGameDirRequest(CoreModel):
    """Parameters of ``init_game_dir``.

    ``spt_version`` answers the ``SPT_VERSION_UNKNOWN`` error of an earlier
    call, which the setup flow repeats with the version a user entered.
    """

    path: Path
    adopt: bool | list[Path] = False
    spt_version: str | None = None


class SetSptVersionRequest(CoreModel):
    """Parameters of ``set_spt_version``."""

    path: Path
    version: str


class SetSptVersionResponse(CoreModel):
    """The SPT version stored for a game directory."""

    spt_version: str


class InstallFromArchiveRequest(CoreModel):
    """Parameters of ``install_from_archive``.

    ``operation_id`` names the progress stream of this install; a generated one
    is used and echoed back when the caller sends none.
    """

    path: Path
    archive: Path
    name_override: str | None = None
    version_override: str | None = None
    force: bool = False
    forge_match: bool = False
    db_path: Path | None = None
    operation_id: str | None = None


class InstallFromArchiveResponse(InstallResult):
    """Outcome of ``install_from_archive`` plus its progress operation ID."""

    operation_id: str


class ListModsRequest(CoreModel):
    """Parameters of ``list_mods``."""

    path: Path
    include_all: bool = False


class ModFilesRequest(CoreModel):
    """Parameters of ``mod_files``."""

    path: Path
    name: str


class RemoveModRequest(CoreModel):
    """Parameters of ``remove_mod``."""

    path: Path
    name: str
    force: bool = False


class CheckProfilesRequest(CoreModel):
    """Parameters of ``check_profiles``."""

    path: Path


class HistoryRequest(CoreModel):
    """Parameters of ``history``."""

    path: Path
    name: str | None = None


class SyncCatalogRequest(CoreModel):
    """Parameters of ``sync_catalog``.

    ``operation_id`` names the progress stream of this sync; a generated one is
    used and echoed back when the caller sends none.
    """

    path: Path
    full: bool = False
    db_path: Path | None = None
    operation_id: str | None = None


class SyncCatalogResponse(SyncResult):
    """Outcome of ``sync_catalog`` plus its progress operation ID."""

    operation_id: str


class SearchCatalogRequest(CoreModel):
    """Parameters of ``search_catalog``."""

    path: Path
    query: str
    compatible_only: bool = False
    db_path: Path | None = None


class BrowseCatalogRequest(CoreModel):
    """Parameters of ``browse_catalog``."""

    path: Path
    filter: BrowseFilter = "all"
    compatible_only: bool = False
    db_path: Path | None = None


class DisableModRequest(CoreModel):
    """Parameters of ``disable_mod``."""

    path: Path
    name: str


class EnableModRequest(CoreModel):
    """Parameters of ``enable_mod``."""

    path: Path
    name: str


class AdoptModRequest(CoreModel):
    """Parameters of ``adopt_mod``."""

    path: Path
    target: Path
    name_override: str | None = None
    version_override: str | None = None


class StatusRequest(CoreModel):
    """Parameters of ``status``."""

    path: Path


class SnapshotRequest(CoreModel):
    """Parameters of ``snapshot``."""

    path: Path
    message: str | None = None


class ExportModsRequest(CoreModel):
    """Parameters of ``export_mods``."""

    path: Path
    format: ExportFormat = "text"
    client_only: bool = False
    db_path: Path | None = None


class ExportModsResponse(CoreModel):
    """The rendered mod list of ``export_mods``."""

    content: str


class ImportListRequest(CoreModel):
    """Parameters of ``import_list``.

    Exactly one of ``list_file`` and ``content`` carries the mod list.
    """

    path: Path
    list_file: Path | None = None
    apply: bool = False
    content: str | None = None
    db_path: Path | None = None


class ClearCacheResult(CoreModel):
    """Outcome of ``clear_catalog_cache``."""

    db_path: Path
    removed: bool


class ClearCatalogCacheRequest(CoreModel):
    """Parameters of ``clear_catalog_cache``."""

    db_path: Path | None = None


class ListFavoritesRequest(CoreModel):
    """Parameters of ``list_favorites``."""

    favorites_path: Path | None = None


class SetFavoriteRequest(CoreModel):
    """Parameters of ``set_favorite``."""

    mod_id: int
    favorite: bool
    favorites_path: Path | None = None


class FavoritesResponse(CoreModel):
    """The Forge ids of all mods marked as favorites."""

    mod_ids: list[int] = []


class ModDetails(CoreModel):
    """Outcome of ``get_mod_details``: cached data plus live description."""

    mod: CatalogMod | None
    versions: list[CatalogVersion]
    description: str


class GetModDetailsRequest(CoreModel):
    """Parameters of ``get_mod_details``."""

    mod_id: int
    db_path: Path | None = None


class PlanModInstallRequest(CoreModel):
    """Parameters of ``plan_mod_install``."""

    path: Path
    mod_id_or_name: int | str
    version: str | None = None
    db_path: Path | None = None


class InstallModRequest(CoreModel):
    """Parameters of ``install_mod``.

    ``operation_id`` names the progress stream of this install; a generated one
    is used and echoed back when the caller sends none.
    """

    path: Path
    mod_id_or_name: int | str
    version: str | None = None
    force: bool = False
    db_path: Path | None = None
    operation_id: str | None = None


class InstallModResponse(InstallModResult):
    """Outcome of ``install_mod`` plus its progress operation ID."""

    operation_id: str


class CheckUpdatesRequest(CoreModel):
    """Parameters of ``check_updates``."""

    path: Path
    names: list[str] | None = None
    db_path: Path | None = None


class UpdateModRequest(CoreModel):
    """Parameters of ``update_mod``.

    ``operation_id`` names the progress stream of this update; a generated one
    is used and echoed back when the caller sends none.
    """

    path: Path
    name: str
    force: bool = False
    db_path: Path | None = None
    operation_id: str | None = None


class UpdateModResponse(UpdateResult):
    """Outcome of ``update_mod`` plus its progress operation ID."""

    operation_id: str


class ProgressEvent(CoreModel):
    """One progress line of a long-running RPC operation on stdout."""

    operation_id: str
    step: str
    percent: int
    message: str


class ErrorDetails(CoreModel):
    """The structured payload a failed operation carries, empty where it has none."""

    conflicts: list[Conflict] = []
    residues: list[ProfileResidue] = []
    conflicting_paths: list[Path] = []
    installed: list[str] = []
    name: str = ""
    candidates: list[str] = []
    dependency: str = ""
    reason: str = ""
    api_code: str = ""
    api_status: int = 0


class ErrorResponse(CoreModel):
    """Body of every failed request.

    ``code`` is the stable machine-readable name of the core exception class,
    ``error`` its human-readable message.
    """

    error: str
    code: str
    details: ErrorDetails = Field(default_factory=ErrorDetails)
