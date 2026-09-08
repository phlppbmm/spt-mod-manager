"""Mapping of core exceptions onto the stable error contract of the adapters.

Every typed core exception has a machine-readable code; the structured payload
an exception carries becomes an :class:`ErrorDetails` body.
"""

from enum import StrEnum

from sptmod.core.errors import (
    AlreadyInitializedError,
    AmbiguousModError,
    ArchiveError,
    CatalogUnavailableError,
    ClientBuildMismatchError,
    ConflictError,
    DependencyError,
    DirtyTreeError,
    ForgeApiError,
    ForgeUnavailableError,
    GameRunningError,
    GitError,
    GitMissingError,
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
    SptReleaseUnavailableError,
    SptReleaseWriteError,
    SptVersionUnknownError,
    UnresolvableDependencyError,
)
from sptmod.core.forge.catalog import NoCompatibleVersionError
from sptmod.core.modmeta import InvalidMetadataError, UnsupportedRangeError
from sptmod.server import schemas


class ErrorCode(StrEnum):
    """Stable machine-readable error codes of the adapter contract."""

    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_A_GAME_DIR = "NOT_A_GAME_DIR"
    ARCHIVE_ERROR = "ARCHIVE_ERROR"
    INVALID_METADATA = "INVALID_METADATA"
    INVALID_PATH = "INVALID_PATH"
    UNSUPPORTED_RANGE = "UNSUPPORTED_RANGE"
    SPT_VERSION_UNKNOWN = "SPT_VERSION_UNKNOWN"
    MOD_NOT_FOUND = "MOD_NOT_FOUND"
    NO_COMPATIBLE_VERSION = "NO_COMPATIBLE_VERSION"
    AMBIGUOUS_MOD = "AMBIGUOUS_MOD"
    DIRTY_TREE = "DIRTY_TREE"
    GAME_RUNNING = "GAME_RUNNING"
    LAUNCH_FAILED = "LAUNCH_FAILED"
    SERVER_PORT_BUSY = "SERVER_PORT_BUSY"
    LOGIN_FAILED = "LOGIN_FAILED"
    LAUNCHER_SETTINGS_MISSING = "LAUNCHER_SETTINGS_MISSING"
    OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"
    CONFLICT = "CONFLICT"
    DEPENDENCY = "DEPENDENCY"
    UNRESOLVABLE_DEPENDENCY = "UNRESOLVABLE_DEPENDENCY"
    PROFILE_RESIDUE = "PROFILE_RESIDUE"
    PARTIAL_INSTALL = "PARTIAL_INSTALL"
    ALREADY_INITIALIZED = "ALREADY_INITIALIZED"
    GIT_ERROR = "GIT_ERROR"
    GIT_MISSING = "GIT_MISSING"
    SPTMOD_ERROR = "SPTMOD_ERROR"
    NOT_IMPLEMENTED_YET = "NOT_IMPLEMENTED_YET"
    FORGE_API_ERROR = "FORGE_API_ERROR"
    FORGE_UNAVAILABLE = "FORGE_UNAVAILABLE"
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
    SPT_RELEASE_UNAVAILABLE = "SPT_RELEASE_UNAVAILABLE"
    SPT_RELEASE_WRITE_FAILED = "SPT_RELEASE_WRITE_FAILED"
    CLIENT_BUILD_MISMATCH = "CLIENT_BUILD_MISMATCH"


# Stable machine-readable code per core exception class. The lookup walks the
# MRO, so subclasses inherit the entry of their base.
ERROR_MAPPING: dict[type[Exception], ErrorCode] = {
    NotAGameDirError: ErrorCode.NOT_A_GAME_DIR,
    ArchiveError: ErrorCode.ARCHIVE_ERROR,
    InvalidMetadataError: ErrorCode.INVALID_METADATA,
    InvalidPathError: ErrorCode.INVALID_PATH,
    UnsupportedRangeError: ErrorCode.UNSUPPORTED_RANGE,
    SptVersionUnknownError: ErrorCode.SPT_VERSION_UNKNOWN,
    ModNotFoundError: ErrorCode.MOD_NOT_FOUND,
    NoCompatibleVersionError: ErrorCode.NO_COMPATIBLE_VERSION,
    AmbiguousModError: ErrorCode.AMBIGUOUS_MOD,
    DirtyTreeError: ErrorCode.DIRTY_TREE,
    GameRunningError: ErrorCode.GAME_RUNNING,
    LaunchError: ErrorCode.LAUNCH_FAILED,
    ServerPortBusyError: ErrorCode.SERVER_PORT_BUSY,
    LoginFailedError: ErrorCode.LOGIN_FAILED,
    LauncherSettingsError: ErrorCode.LAUNCHER_SETTINGS_MISSING,
    OperationInProgressError: ErrorCode.OPERATION_IN_PROGRESS,
    ConflictError: ErrorCode.CONFLICT,
    DependencyError: ErrorCode.DEPENDENCY,
    UnresolvableDependencyError: ErrorCode.UNRESOLVABLE_DEPENDENCY,
    ProfileResidueError: ErrorCode.PROFILE_RESIDUE,
    PartialInstallError: ErrorCode.PARTIAL_INSTALL,
    AlreadyInitializedError: ErrorCode.ALREADY_INITIALIZED,
    GitError: ErrorCode.GIT_ERROR,
    GitMissingError: ErrorCode.GIT_MISSING,
    SptmodError: ErrorCode.SPTMOD_ERROR,
    NotImplementedYetError: ErrorCode.NOT_IMPLEMENTED_YET,
    NotImplementedError: ErrorCode.NOT_IMPLEMENTED_YET,
    ForgeApiError: ErrorCode.FORGE_API_ERROR,
    ForgeUnavailableError: ErrorCode.FORGE_UNAVAILABLE,
    CatalogUnavailableError: ErrorCode.CATALOG_UNAVAILABLE,
    SptReleaseUnavailableError: ErrorCode.SPT_RELEASE_UNAVAILABLE,
    SptReleaseWriteError: ErrorCode.SPT_RELEASE_WRITE_FAILED,
    ClientBuildMismatchError: ErrorCode.CLIENT_BUILD_MISMATCH,
}


def error_code(error_type: type[Exception]) -> ErrorCode:
    """Return the stable code mapped to ``error_type``."""
    for base in error_type.__mro__:
        mapped = ERROR_MAPPING.get(base)
        if mapped is not None:
            return mapped
    return ErrorCode.INTERNAL_ERROR


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
