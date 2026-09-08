"""Subprocess adapter: one JSON request on stdin, NDJSON documents on stdout.

``sptmod rpc <name>`` calls the service function ``name`` with the JSON object
read from stdin, validated by that function's own request model. Every
progress callback becomes one ``{"event": "progress", ...}`` line; the call ends
with a single ``{"event": "result", "data": ...}`` or
``{"event": "error", "data": ...}`` line. stdout carries nothing else, so a
driver process can parse it line by line; stderr is free for diagnostics.
"""

import inspect
import json
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Final
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from sptmod.core import services
from sptmod.core.models import Progress
from sptmod.server import schemas
from sptmod.server.errors import ErrorCode, error_code, error_details

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
EXIT_REQUEST: Final = 2


@dataclass(frozen=True)
class RpcSpec:
    """How one service function is called and how its result is serialised.

    ``wrap`` names the response field a bare result value is put under, and
    ``operation_id`` marks the responses echoing the progress operation ID.
    """

    function: Callable[..., object]
    request: type[schemas.CoreModel]
    response: type[schemas.CoreModel] | type[Sequence[schemas.CoreModel]]
    wrap: str | None = None
    operation_id: bool = False


RPC_FUNCTIONS: Final[dict[str, RpcSpec]] = {
    "list_directories": RpcSpec(
        services.list_directories,
        schemas.ListDirectoriesRequest,
        schemas.DirectoryListing,
    ),
    "locate_directory": RpcSpec(
        services.locate_directory,
        schemas.LocateDirectoryRequest,
        list[schemas.LocatedDirectory],
    ),
    "init_game_dir": RpcSpec(
        services.init_game_dir,
        schemas.InitGameDirRequest,
        schemas.InitResult,
    ),
    "set_spt_version": RpcSpec(
        services.set_spt_version,
        schemas.SetSptVersionRequest,
        schemas.SetSptVersionResponse,
        wrap="spt_version",
    ),
    "install_from_archive": RpcSpec(
        services.install_from_archive,
        schemas.InstallFromArchiveRequest,
        schemas.InstallFromArchiveResponse,
        operation_id=True,
    ),
    "list_mods": RpcSpec(
        services.list_mods,
        schemas.ListModsRequest,
        list[schemas.ModInfo],
    ),
    "mod_files": RpcSpec(
        services.mod_files,
        schemas.ModFilesRequest,
        list[schemas.ModFile],
    ),
    "remove_mod": RpcSpec(
        services.remove_mod,
        schemas.RemoveModRequest,
        schemas.RemoveResult,
    ),
    "list_profiles": RpcSpec(
        services.list_profiles,
        schemas.ListProfilesRequest,
        list[schemas.ProfileSummary],
    ),
    "check_profiles": RpcSpec(
        services.check_profiles,
        schemas.CheckProfilesRequest,
        schemas.CheckResult,
    ),
    "plan_profile_cleanup": RpcSpec(
        services.plan_profile_cleanup,
        schemas.PlanProfileCleanupRequest,
        schemas.CleanupPlan,
    ),
    "clean_profile": RpcSpec(
        services.clean_profile,
        schemas.CleanProfileRequest,
        schemas.CleanupResult,
    ),
    "history": RpcSpec(
        services.history,
        schemas.HistoryRequest,
        list[schemas.CommitInfo],
    ),
    "sync_catalog": RpcSpec(
        services.sync_catalog,
        schemas.SyncCatalogRequest,
        schemas.SyncCatalogResponse,
        operation_id=True,
    ),
    "search_catalog": RpcSpec(
        services.search_catalog,
        schemas.SearchCatalogRequest,
        list[schemas.CatalogEntry],
    ),
    "browse_catalog": RpcSpec(
        services.browse_catalog,
        schemas.BrowseCatalogRequest,
        list[schemas.CatalogEntry],
    ),
    "disable_mod": RpcSpec(
        services.disable_mod,
        schemas.DisableModRequest,
        schemas.ToggleResult,
    ),
    "enable_mod": RpcSpec(
        services.enable_mod,
        schemas.EnableModRequest,
        schemas.ToggleResult,
    ),
    "adopt_mod": RpcSpec(
        services.adopt_mod,
        schemas.AdoptModRequest,
        schemas.AdoptResult,
    ),
    "status": RpcSpec(
        services.status,
        schemas.StatusRequest,
        schemas.StatusReport,
    ),
    "snapshot": RpcSpec(
        services.snapshot,
        schemas.SnapshotRequest,
        schemas.SnapshotResult,
    ),
    "export_mods": RpcSpec(
        services.export_mods,
        schemas.ExportModsRequest,
        schemas.ExportModsResponse,
        wrap="content",
    ),
    "import_list": RpcSpec(
        services.import_list,
        schemas.ImportListRequest,
        schemas.ImportDiff,
    ),
    "clear_catalog_cache": RpcSpec(
        services.clear_catalog_cache,
        schemas.ClearCatalogCacheRequest,
        schemas.ClearCacheResult,
    ),
    "get_mod_details": RpcSpec(
        services.get_mod_details,
        schemas.GetModDetailsRequest,
        schemas.ModDetails,
    ),
    "plan_mod_install": RpcSpec(
        services.plan_mod_install,
        schemas.PlanModInstallRequest,
        schemas.InstallPlan,
    ),
    "install_mod": RpcSpec(
        services.install_mod,
        schemas.InstallModRequest,
        schemas.InstallModResponse,
        operation_id=True,
    ),
    "check_updates": RpcSpec(
        services.check_updates,
        schemas.CheckUpdatesRequest,
        schemas.UpdatesReport,
    ),
    "update_mod": RpcSpec(
        services.update_mod,
        schemas.UpdateModRequest,
        schemas.UpdateModResponse,
        operation_id=True,
    ),
    "list_favorites": RpcSpec(
        services.list_favorites,
        schemas.ListFavoritesRequest,
        schemas.FavoritesResponse,
        wrap="mod_ids",
    ),
    "set_favorite": RpcSpec(
        services.set_favorite,
        schemas.SetFavoriteRequest,
        schemas.FavoritesResponse,
        wrap="mod_ids",
    ),
    "list_mod_sets": RpcSpec(
        services.list_mod_sets,
        schemas.ListModSetsRequest,
        schemas.ModSetOverview,
    ),
    "save_mod_set": RpcSpec(
        services.save_mod_set,
        schemas.SaveModSetRequest,
        schemas.ModSet,
    ),
    "delete_mod_set": RpcSpec(
        services.delete_mod_set,
        schemas.DeleteModSetRequest,
        schemas.ModSetOverview,
    ),
    "rename_mod_set": RpcSpec(
        services.rename_mod_set,
        schemas.RenameModSetRequest,
        schemas.ModSet,
    ),
    "export_mod_set": RpcSpec(
        services.export_mod_set,
        schemas.ExportModSetRequest,
        schemas.ModSetExport,
    ),
    "import_mod_set": RpcSpec(
        services.import_mod_set,
        schemas.ImportModSetRequest,
        schemas.ModSet,
    ),
    "bind_profile_set": RpcSpec(
        services.bind_profile_set,
        schemas.BindProfileSetRequest,
        schemas.ModSetOverview,
    ),
    "plan_mod_set": RpcSpec(
        services.plan_mod_set,
        schemas.PlanModSetRequest,
        schemas.ModSetPlan,
    ),
    "apply_mod_set": RpcSpec(
        services.apply_mod_set,
        schemas.ApplyModSetRequest,
        schemas.ApplyModSetResponse,
        operation_id=True,
    ),
    "launch_profile": RpcSpec(
        services.launch_profile,
        schemas.LaunchProfileRequest,
        schemas.LaunchProfileResponse,
        operation_id=True,
    ),
    "launch_status": RpcSpec(
        services.launch_status,
        schemas.LaunchStatusRequest,
        schemas.LaunchStatus,
    ),
    "stop_game": RpcSpec(
        services.stop_game,
        schemas.StopGameRequest,
        schemas.StopResult,
    ),
    "list_spt_releases": RpcSpec(
        services.list_spt_releases,
        schemas.ListSptReleasesRequest,
        schemas.SptReleaseList,
    ),
    "apply_spt_release": RpcSpec(
        services.apply_spt_release,
        schemas.ApplySptReleaseRequest,
        schemas.ApplySptReleaseResponse,
        operation_id=True,
    ),
    "revert_spt_update": RpcSpec(
        services.revert_spt_update,
        schemas.RevertSptUpdateRequest,
        schemas.SptApplyResult,
    ),
    "get_fika_settings": RpcSpec(
        services.get_fika_settings,
        schemas.GetFikaSettingsRequest,
        schemas.FikaSettings,
    ),
    "set_fika_settings": RpcSpec(
        services.set_fika_settings,
        schemas.SetFikaSettingsRequest,
        schemas.FikaSettings,
    ),
    "list_host_profiles": RpcSpec(
        services.list_host_profiles,
        schemas.ListHostProfilesRequest,
        list[schemas.HostProfile],
    ),
    "plan_fika_install": RpcSpec(
        services.plan_fika_install,
        schemas.PlanFikaInstallRequest,
        schemas.FikaInstallPlan,
    ),
}


def main(name: str) -> int:
    """Run the service function ``name`` on the request read from stdin."""
    spec = RPC_FUNCTIONS.get(name)
    if spec is None:
        return _write_error(f"unknown function: {name}", ErrorCode.VALIDATION_ERROR)
    try:
        payload = json.loads(sys.stdin.buffer.read())
    except ValueError as error:
        return _write_error(f"invalid JSON request: {error}", ErrorCode.VALIDATION_ERROR)
    try:
        request = spec.request.model_validate(payload)
    except ValidationError as error:
        return _write_error(str(error), ErrorCode.VALIDATION_ERROR)
    return _dispatch(spec, request)


class _ServiceFailed(Exception):
    """Raised at the RPC error boundary for any failure of a service function.

    Carries the original exception, whose type decides the error code and the
    structured details of the response.
    """

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def _dispatch(spec: RpcSpec, request: schemas.CoreModel) -> int:
    """Execute the service function from ``spec`` with validated ``request`` fields.

    Returns EXIT_ERROR if an exception occurs; otherwise serializes the result
    and returns EXIT_OK. One result or error line is always written to stdout.
    """
    operation_id = _operation_id(request)
    try:
        data = _call(spec, request, operation_id)
    except _ServiceFailed as failure:
        body = schemas.ErrorResponse(
            error=str(failure.error) or type(failure.error).__name__,
            code=error_code(type(failure.error)),
            details=error_details(failure.error),
        )
        _write({"event": "error", "data": body.model_dump(mode="json")})
        return EXIT_ERROR
    _write({"event": "result", "data": data})
    return EXIT_OK


def _call(spec: RpcSpec, request: schemas.CoreModel, operation_id: str) -> JsonValue:
    """Call the service function of ``spec`` and render its result.

    Any failure of the service function is the caller's error response, so the
    traceback goes to stderr here and the exception is re-raised wrapped.
    """
    kwargs = request.service_kwargs()
    if "progress" in inspect.signature(spec.function).parameters:
        kwargs["progress"] = _progress(operation_id)
    try:
        result = spec.function(**kwargs)
        return _serialise(spec, result, operation_id)
    except Exception as error:
        traceback.print_exc()
        raise _ServiceFailed(error) from error


def _serialise(spec: RpcSpec, result: object, operation_id: str) -> JsonValue:
    """Render ``result`` as the JSON body of the response model of ``spec``."""
    adapter = TypeAdapter(spec.response)
    if spec.wrap is not None:
        payload: object = {spec.wrap: result}
    elif spec.operation_id:
        payload = {"operation_id": operation_id, **vars(result)}
    else:
        payload = result
    validated = adapter.validate_python(payload, from_attributes=True)
    dumped: JsonValue = adapter.dump_python(validated, mode="json")
    return dumped


def _operation_id(request: schemas.CoreModel) -> str:
    """Return the requested operation ID or a fresh one."""
    requested = getattr(request, "operation_id", None)
    return requested if isinstance(requested, str) and requested else uuid4().hex


def _progress(operation_id: str) -> Progress:
    """Return a core progress callback writing one NDJSON line per report."""

    def report(step: str, percent: int, message: str) -> None:
        event = schemas.ProgressEvent(
            operation_id=operation_id,
            step=step,
            percent=percent,
            message=message,
        )
        with suppress(BrokenPipeError):
            _write({"event": "progress", **event.model_dump(mode="json")})

    return report


def _write_error(message: str, code: ErrorCode) -> int:
    """Write an error envelope to stdout and return EXIT_REQUEST (code 2).

    Used for validation/request errors (malformed JSON, unknown function).
    """
    body = schemas.ErrorResponse(error=message, code=code)
    _write({"event": "error", "data": body.model_dump(mode="json")})
    return EXIT_REQUEST


def _write(document: dict[str, JsonValue]) -> None:
    """Write one JSON line (``document`` + newline) to stdout, UTF-8 encoded, flushed.

    Used for all output: progress events, result, and error envelopes.
    """
    line = json.dumps(document, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(line.encode())
    sys.stdout.buffer.flush()
