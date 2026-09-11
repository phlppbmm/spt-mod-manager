"""Request and response models of the mod, profile and catalog service functions.

The progress and error envelopes every RPC call shares live here as well.
"""

from pathlib import Path

from pydantic import ConfigDict, Field

from sptmod.core.services import BrowseFilter, ExportFormat

from .base import CoreModel
from .mods import (
    CatalogMod,
    CatalogVersion,
    Conflict,
    InstallModResult,
    InstallResult,
    ProfileResidue,
    SyncResult,
    UpdateResult,
)


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
    clean_profiles: bool = False


class ListProfilesRequest(CoreModel):
    """Parameters of ``list_profiles``."""

    path: Path


class CheckProfilesRequest(CoreModel):
    """Parameters of ``check_profiles``."""

    path: Path


class PlanProfileCleanupRequest(CoreModel):
    """Parameters of ``plan_profile_cleanup``."""

    path: Path
    mod_name: str


class CleanProfileRequest(CoreModel):
    """Parameters of ``clean_profile``."""

    path: Path
    mod_name: str
    profiles: list[str] | None = None


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
    """Parameters of ``browse_catalog``.

    ``spt_version`` judges the entries against another SPT version than the one
    the game directory has, so a player can see what is ready for the release
    they are about to install.
    """

    # ``catalog_filter`` keeps the wire name ``filter`` through its alias, so neither the
    # model field nor the service parameter shadows the builtin.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    path: Path
    catalog_filter: BrowseFilter = Field("all", alias="filter")
    compatible_only: bool = False
    spt_version: str | None = None
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

    # ``export_format`` keeps the wire name ``format`` through its alias, so neither the
    # model field nor the service parameter shadows the builtin.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    path: Path
    export_format: ExportFormat = Field("text", alias="format")
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
    downloads_path: Path | None = None
    downloads_freed_bytes: int = 0


class ClearCatalogCacheRequest(CoreModel):
    """Parameters of ``clear_catalog_cache``."""

    db_path: Path | None = None
    downloads: bool = False


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


class GetThumbnailsRequest(CoreModel):
    """Parameters of ``get_thumbnails``."""

    mod_ids: list[int]
    db_path: Path | None = None
    cache_dir: Path | None = None


class ThumbnailsResponse(CoreModel):
    """The image of every requested mod as a data URL, keyed by Forge id.

    A mod the catalog knows no image for, and one whose image cannot be
    fetched, carries None: the caller shows its placeholder then.
    """

    thumbnails: dict[int, str | None]


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
    missing: list[str] = []
    mods: list[str] = []
    spt_version: str = ""


class ErrorResponse(CoreModel):
    """Body of every failed request.

    ``code`` is the stable machine-readable name of the core exception class,
    ``error`` its human-readable message.
    """

    error: str
    code: str
    details: ErrorDetails = Field(default_factory=ErrorDetails)
