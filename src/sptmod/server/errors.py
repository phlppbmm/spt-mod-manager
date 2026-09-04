"""Mapping of core exceptions onto the stable error contract of the adapters.

Every typed core exception has a machine-readable code; the structured payload
an exception carries becomes an :class:`ErrorDetails` body.
"""

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
    LaunchError,
    LauncherSettingsError,
    LoginFailedError,
    ModNotFoundError,
    NotAGameDirError,
    NotImplementedYetError,
    OperationInProgressError,
    PartialInstallError,
    ProfileResidueError,
    ServerPortBusyError,
    SptmodError,
    SptVersionUnknownError,
    UnresolvableDependencyError,
)
from sptmod.core.forge.catalog import NoCompatibleVersionError
from sptmod.core.modmeta import InvalidMetadataError, UnsupportedRangeError
from sptmod.server import schemas

FALLBACK_ERROR_CODE = "INTERNAL_ERROR"

# Stable machine-readable code per core exception class. The lookup walks the
# MRO, so subclasses inherit the entry of their base.
ERROR_MAPPING: dict[type[Exception], str] = {
    NotAGameDirError: "NOT_A_GAME_DIR",
    ArchiveError: "ARCHIVE_ERROR",
    InvalidMetadataError: "INVALID_METADATA",
    InvalidPathError: "INVALID_PATH",
    UnsupportedRangeError: "UNSUPPORTED_RANGE",
    SptVersionUnknownError: "SPT_VERSION_UNKNOWN",
    ModNotFoundError: "MOD_NOT_FOUND",
    NoCompatibleVersionError: "NO_COMPATIBLE_VERSION",
    AmbiguousModError: "AMBIGUOUS_MOD",
    DirtyTreeError: "DIRTY_TREE",
    GameRunningError: "GAME_RUNNING",
    LaunchError: "LAUNCH_FAILED",
    ServerPortBusyError: "SERVER_PORT_BUSY",
    LoginFailedError: "LOGIN_FAILED",
    LauncherSettingsError: "LAUNCHER_SETTINGS_MISSING",
    OperationInProgressError: "OPERATION_IN_PROGRESS",
    ConflictError: "CONFLICT",
    DependencyError: "DEPENDENCY",
    UnresolvableDependencyError: "UNRESOLVABLE_DEPENDENCY",
    ProfileResidueError: "PROFILE_RESIDUE",
    PartialInstallError: "PARTIAL_INSTALL",
    AlreadyInitializedError: "ALREADY_INITIALIZED",
    GitError: "GIT_ERROR",
    SptmodError: "SPTMOD_ERROR",
    NotImplementedYetError: "NOT_IMPLEMENTED_YET",
    NotImplementedError: "NOT_IMPLEMENTED_YET",
    ForgeApiError: "FORGE_API_ERROR",
    ForgeUnavailableError: "FORGE_UNAVAILABLE",
    CatalogUnavailableError: "CATALOG_UNAVAILABLE",
}


def error_code(error_type: type[Exception]) -> str:
    """Return the stable code mapped to ``error_type``."""
    for base in error_type.__mro__:
        mapped = ERROR_MAPPING.get(base)
        if mapped is not None:
            return mapped
    return FALLBACK_ERROR_CODE


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
