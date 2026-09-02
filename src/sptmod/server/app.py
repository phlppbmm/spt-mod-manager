"""FastAPI adapter: one endpoint per service function of the sptmod core.

Every service function is reachable as ``POST /api/<function_name>`` with a JSON
body mirroring its parameters and a JSON response mirroring its return value, so
the contract maps 1:1 onto Tauri commands later. Typed core exceptions become
``{error, code, details}`` bodies with a stable ``code`` per exception class.
"""

import os
from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from sptmod.core import services
from sptmod.core.errors import (
    AlreadyInitializedError,
    AmbiguousModError,
    ArchiveError,
    CatalogUnavailableError,
    ConflictError,
    DependencyError,
    DirtyTreeError,
    ForgeApiError,
    ForgeUnavailableError,
    GameRunningError,
    GitError,
    InvalidPathError,
    ModNotFoundError,
    NotAGameDirError,
    NotImplementedYetError,
    PartialInstallError,
    ProfileResidueError,
    SptmodError,
    SptVersionUnknownError,
    UnresolvableDependencyError,
)
from sptmod.core.forge.catalog import CatalogEntry, NoCompatibleVersionError
from sptmod.core.models import CommitInfo, InstallPlan, ModFile, ModInfo
from sptmod.core.modmeta import InvalidMetadataError, UnsupportedRangeError
from sptmod.core.profile import CheckResult
from sptmod.server import events, schemas

PACKAGE_NAME = "sptmod"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
STATIC_DIR_ENV_VAR = "SPTMOD_STATIC_DIR"

FALLBACK_ERROR_CODE = "INTERNAL_ERROR"
FALLBACK_ERROR_STATUS = 500

# Stable machine-readable code and HTTP status per core exception class. The
# lookup walks the MRO, so subclasses inherit the entry of their base.
ERROR_MAPPING: dict[type[Exception], tuple[str, int]] = {
    NotAGameDirError: ("NOT_A_GAME_DIR", 400),
    ArchiveError: ("ARCHIVE_ERROR", 400),
    InvalidMetadataError: ("INVALID_METADATA", 400),
    InvalidPathError: ("INVALID_PATH", 400),
    UnsupportedRangeError: ("UNSUPPORTED_RANGE", 400),
    SptVersionUnknownError: ("SPT_VERSION_UNKNOWN", 400),
    ModNotFoundError: ("MOD_NOT_FOUND", 404),
    NoCompatibleVersionError: ("NO_COMPATIBLE_VERSION", 404),
    AmbiguousModError: ("AMBIGUOUS_MOD", 409),
    DirtyTreeError: ("DIRTY_TREE", 409),
    GameRunningError: ("GAME_RUNNING", 409),
    ConflictError: ("CONFLICT", 409),
    DependencyError: ("DEPENDENCY", 409),
    UnresolvableDependencyError: ("UNRESOLVABLE_DEPENDENCY", 409),
    ProfileResidueError: ("PROFILE_RESIDUE", 409),
    PartialInstallError: ("PARTIAL_INSTALL", 409),
    AlreadyInitializedError: ("ALREADY_INITIALIZED", 409),
    GitError: ("GIT_ERROR", 500),
    SptmodError: ("SPTMOD_ERROR", 500),
    NotImplementedYetError: ("NOT_IMPLEMENTED_YET", 501),
    NotImplementedError: ("NOT_IMPLEMENTED_YET", 501),
    ForgeApiError: ("FORGE_API_ERROR", 502),
    ForgeUnavailableError: ("FORGE_UNAVAILABLE", 503),
    CatalogUnavailableError: ("CATALOG_UNAVAILABLE", 503),
}

router = APIRouter(
    prefix="/api", responses={"default": {"model": schemas.ErrorResponse}}
)


@router.get("/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    """Report that the server is up."""
    return schemas.HealthResponse()


@router.post("/list_directories", response_model=schemas.DirectoryListing)
def list_directories(
    request: schemas.ListDirectoriesRequest,
) -> services.DirectoryListing:
    return services.list_directories(request.path)


@router.post("/locate_directory", response_model=list[schemas.LocatedDirectory])
def locate_directory(
    request: schemas.LocateDirectoryRequest,
) -> list[services.LocatedDirectory]:
    return services.locate_directory(request.name, request.fingerprint)


@router.post("/init_game_dir", response_model=schemas.InitResult)
def init_game_dir(request: schemas.InitGameDirRequest) -> services.InitResult:
    return services.init_game_dir(request.path, request.adopt, request.spt_version)


@router.post("/set_spt_version", response_model=schemas.SetSptVersionResponse)
def set_spt_version(
    request: schemas.SetSptVersionRequest,
) -> schemas.SetSptVersionResponse:
    return schemas.SetSptVersionResponse(
        spt_version=services.set_spt_version(request.path, request.version)
    )


@router.post("/install_from_archive", response_model=schemas.InstallFromArchiveResponse)
def install_from_archive(
    request: schemas.InstallFromArchiveRequest,
) -> schemas.InstallFromArchiveResponse:
    operation_id = request.operation_id or events.new_operation_id()
    result = services.install_from_archive(
        request.path,
        request.archive,
        request.name_override,
        request.version_override,
        request.force,
        events.progress_callback(operation_id),
        forge_match=request.forge_match,
        db_path=request.db_path,
    )
    return schemas.InstallFromArchiveResponse(
        operation_id=operation_id,
        mod=schemas.ModInfo.model_validate(result.mod),
        files=result.files,
        warnings=[schemas.Conflict.model_validate(item) for item in result.warnings],
    )


@router.post("/list_mods", response_model=list[schemas.ModInfo])
def list_mods(request: schemas.ListModsRequest) -> list[ModInfo]:
    return services.list_mods(request.path, request.include_all)


@router.post("/mod_files", response_model=list[schemas.ModFile])
def mod_files(request: schemas.ModFilesRequest) -> list[ModFile]:
    return services.mod_files(request.path, request.name)


@router.post("/remove_mod", response_model=schemas.RemoveResult)
def remove_mod(request: schemas.RemoveModRequest) -> services.RemoveResult:
    return services.remove_mod(request.path, request.name, request.force)


@router.post("/check_profiles", response_model=schemas.CheckResult)
def check_profiles(request: schemas.CheckProfilesRequest) -> CheckResult:
    return services.check_profiles(request.path)


@router.post("/history", response_model=list[schemas.CommitInfo])
def history(request: schemas.HistoryRequest) -> list[CommitInfo]:
    return services.history(request.path, request.name)


@router.post("/sync_catalog", response_model=schemas.SyncCatalogResponse)
def sync_catalog(request: schemas.SyncCatalogRequest) -> schemas.SyncCatalogResponse:
    operation_id = request.operation_id or events.new_operation_id()
    result = services.sync_catalog(
        request.path,
        request.full,
        events.progress_callback(operation_id),
        db_path=request.db_path,
    )
    return schemas.SyncCatalogResponse(
        operation_id=operation_id,
        new_mods=result.new_mods,
        updated_mods=result.updated_mods,
        new_versions=result.new_versions,
        updated_versions=result.updated_versions,
        pages=result.pages,
        full=result.full,
    )


@router.post("/search_catalog", response_model=list[schemas.CatalogEntry])
def search_catalog(request: schemas.SearchCatalogRequest) -> list[CatalogEntry]:
    return services.search_catalog(
        request.path,
        request.query,
        request.compatible_only,
        db_path=request.db_path,
    )


@router.post("/browse_catalog", response_model=list[schemas.CatalogEntry])
def browse_catalog(request: schemas.BrowseCatalogRequest) -> list[CatalogEntry]:
    return services.browse_catalog(
        request.path,
        request.filter,
        request.compatible_only,
        db_path=request.db_path,
    )


@router.post("/disable_mod", response_model=schemas.ToggleResult)
def disable_mod(request: schemas.DisableModRequest) -> services.ToggleResult:
    return services.disable_mod(request.path, request.name)


@router.post("/enable_mod", response_model=schemas.ToggleResult)
def enable_mod(request: schemas.EnableModRequest) -> services.ToggleResult:
    return services.enable_mod(request.path, request.name)


@router.post("/adopt_mod", response_model=schemas.AdoptResult)
def adopt_mod(request: schemas.AdoptModRequest) -> services.AdoptResult:
    return services.adopt_mod(
        request.path,
        request.target,
        request.name_override,
        request.version_override,
    )


@router.post("/status", response_model=schemas.StatusReport)
def status(request: schemas.StatusRequest) -> services.StatusReport:
    return services.status(request.path)


@router.post("/snapshot", response_model=schemas.SnapshotResult)
def snapshot(request: schemas.SnapshotRequest) -> services.SnapshotResult:
    return services.snapshot(request.path, request.message)


@router.post("/export_mods", response_model=schemas.ExportModsResponse)
def export_mods(request: schemas.ExportModsRequest) -> schemas.ExportModsResponse:
    return schemas.ExportModsResponse(
        content=services.export_mods(
            request.path,
            request.format,
            request.client_only,
            db_path=request.db_path,
        )
    )


@router.post("/import_list", response_model=schemas.ImportDiff)
def import_list(request: schemas.ImportListRequest) -> services.ImportDiff:
    return services.import_list(
        request.path,
        request.list_file,
        request.apply,
        content=request.content,
        db_path=request.db_path,
    )


@router.post("/clear_catalog_cache", response_model=schemas.ClearCacheResult)
def clear_catalog_cache(
    request: schemas.ClearCatalogCacheRequest,
) -> services.ClearCacheResult:
    return services.clear_catalog_cache(request.db_path)


@router.post("/get_mod_description", response_model=schemas.ModDescription)
def get_mod_description(
    request: schemas.GetModDescriptionRequest,
) -> services.ModDescription:
    return services.get_mod_description(request.mod_id)


@router.post("/plan_mod_install", response_model=schemas.InstallPlan)
def plan_mod_install(request: schemas.PlanModInstallRequest) -> InstallPlan:
    return services.plan_mod_install(
        request.path,
        request.mod_id_or_name,
        request.version,
        db_path=request.db_path,
    )


@router.post("/install_mod", response_model=schemas.InstallModResponse)
def install_mod(request: schemas.InstallModRequest) -> schemas.InstallModResponse:
    operation_id = request.operation_id or events.new_operation_id()
    result = services.install_mod(
        request.path,
        request.mod_id_or_name,
        request.version,
        request.force,
        events.progress_callback(operation_id),
        db_path=request.db_path,
    )
    return schemas.InstallModResponse(
        operation_id=operation_id,
        plan=schemas.InstallPlan.model_validate(result.plan),
        installed=[
            schemas.InstallResult.model_validate(item) for item in result.installed
        ],
    )


@router.post("/check_updates", response_model=schemas.UpdatesReport)
def check_updates(request: schemas.CheckUpdatesRequest) -> services.UpdatesReport:
    return services.check_updates(request.path, request.names, db_path=request.db_path)


@router.post("/update_mod", response_model=schemas.UpdateModResponse)
def update_mod(request: schemas.UpdateModRequest) -> schemas.UpdateModResponse:
    operation_id = request.operation_id or events.new_operation_id()
    result = services.update_mod(
        request.path,
        request.name,
        request.force,
        events.progress_callback(operation_id),
        db_path=request.db_path,
    )
    return schemas.UpdateModResponse(
        operation_id=operation_id,
        name=result.name,
        previous_version=result.previous_version,
        version=result.version,
        commit=result.commit,
        files=[schemas.ModFile.model_validate(item) for item in result.files],
        warnings=[schemas.Conflict.model_validate(item) for item in result.warnings],
        notice=result.notice,
    )


def error_code_and_status(error_type: type[Exception]) -> tuple[str, int]:
    """Return the stable code and HTTP status mapped to ``error_type``."""
    for base in error_type.__mro__:
        mapped = ERROR_MAPPING.get(base)
        if mapped is not None:
            return mapped
    return FALLBACK_ERROR_CODE, FALLBACK_ERROR_STATUS


def error_details(error: Exception) -> schemas.ErrorDetails:
    """Return the structured payload of ``error``, empty when it carries none."""
    if isinstance(error, ConflictError | DependencyError):
        conflicts = [schemas.Conflict.model_validate(item) for item in error.conflicts]
        if isinstance(error, UnresolvableDependencyError):
            return schemas.ErrorDetails(
                conflicts=conflicts,
                dependency=error.dependency,
                reason=error.reason,
            )
        return schemas.ErrorDetails(conflicts=conflicts)
    if isinstance(error, PartialInstallError):
        return schemas.ErrorDetails(
            installed=[item.mod.name for item in error.installed]
        )
    if isinstance(error, ProfileResidueError):
        return schemas.ErrorDetails(
            residues=[
                schemas.ProfileResidue.model_validate(item) for item in error.residues
            ]
        )
    if isinstance(error, GitError):
        return schemas.ErrorDetails(conflicting_paths=error.conflicting_paths)
    if isinstance(error, AmbiguousModError):
        return schemas.ErrorDetails(name=error.name, candidates=error.candidates)
    if isinstance(error, ForgeApiError):
        return schemas.ErrorDetails(api_code=error.code, api_status=error.status)
    return schemas.ErrorDetails()


def error_response(error: Exception) -> JSONResponse:
    """Render ``error`` as the structured error body of the API."""
    code, status_code = error_code_and_status(type(error))
    body = schemas.ErrorResponse(
        error=str(error) or type(error).__name__,
        code=code,
        details=error_details(error),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def handle_error(request: Request, exc: Exception) -> Response:
    """Exception handler turning a core error into an error response."""
    return error_response(exc)


def create_app(static_dir: Path | None = None) -> FastAPI:
    """Build the FastAPI application serving the sptmod service functions.

    Args:
        static_dir: Directory of the built frontend; without it the
            ``SPTMOD_STATIC_DIR`` environment variable is read. When either
            names a directory, its files are served below ``/`` and unknown
            paths outside ``/api`` fall back to ``index.html`` so client-side
            routing works.
    """
    app = FastAPI(
        title=PACKAGE_NAME,
        version=version(PACKAGE_NAME),
        description="HTTP adapter over the sptmod core services.",
    )
    app.include_router(router)
    app.include_router(events.router, prefix="/api")
    app.add_exception_handler(SptmodError, handle_error)
    app.add_exception_handler(NotImplementedError, handle_error)
    from_env = os.environ.get(STATIC_DIR_ENV_VAR)
    resolved = static_dir if static_dir is not None else _optional_path(from_env)
    if resolved is not None:
        _serve_frontend(app, resolved)
    return app


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _serve_frontend(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()

    @app.get("/{asset_path:path}", include_in_schema=False)
    def frontend(asset_path: str) -> FileResponse:
        if asset_path == "api" or asset_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        asset = (root / asset_path).resolve()
        if asset_path and asset.is_relative_to(root) and asset.is_file():
            return FileResponse(asset)
        return FileResponse(root / "index.html")


app = create_app()
