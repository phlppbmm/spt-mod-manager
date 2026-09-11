"""Typed exceptions raised by the sptmod core."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sptmod.core.models import Conflict, ProfileResidue

if TYPE_CHECKING:
    from sptmod.core.services import InstallResult


class SptmodError(Exception):
    """Base class for every error raised by the sptmod core."""


class NotAGameDirError(SptmodError):
    """The given path is not a usable SPT game directory."""


class SptVersionUnknownError(SptmodError):
    """The SPT version of a game directory cannot be determined.

    Raised where an operation needs the version and no step of the detection
    chain found one, so the caller has to ask the user and pass the answer
    back.
    """


class DirtyTreeError(SptmodError):
    """The repository has uncommitted changes blocking the operation."""


class GameRunningError(SptmodError):
    """The SPT server or client is running."""


class LaunchError(SptmodError):
    """The game or its server could not be started."""


class ServerPortBusyError(SptmodError):
    """The address the SPT server would use is held by another program."""


class LoginFailedError(SptmodError):
    """The SPT server does not accept the selected player profile."""


class LauncherSettingsError(SptmodError):
    """The settings the official SPT launcher writes are missing or unusable."""


class FikaHostUnreachableError(SptmodError):
    """The Fika host does not answer as an SPT server."""


class OperationInProgressError(SptmodError):
    """Another operation is changing the game directory and did not finish.

    Attributes:
        operation: The plain-language description of the blocking operation,
            empty when it could not be read.
    """

    def __init__(self, operation: str) -> None:
        detail = f" ({operation})" if operation else ""
        super().__init__(
            f"Another operation is still running{detail}. Try again when it finishes."
        )
        self.operation = operation


class ConflictError(SptmodError):
    """A file, DLL or install conflict blocks the operation.

    Carries the structured findings of the pre-install checks when it was
    raised by an install; ``conflicts`` holds every finding, warnings
    included, so callers can report the full picture.
    """

    def __init__(self, message: str, conflicts: Sequence[Conflict] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.conflicts: list[Conflict] = list(conflicts)


class DependencyError(SptmodError):
    """A required mod dependency is missing or has an incompatible version.

    Shares the ``conflicts`` contract with :class:`ConflictError`.
    """

    def __init__(self, message: str, conflicts: Sequence[Conflict] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.conflicts: list[Conflict] = list(conflicts)


class ProfileResidueError(SptmodError):
    """Removing a mod would leave residue in at least one player profile.

    ``residues`` holds the findings per profile, so callers can report which
    profiles still reference which content of the mod.
    """

    def __init__(self, message: str, residues: Sequence[ProfileResidue] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.residues: list[ProfileResidue] = list(residues)


class ArchiveError(SptmodError):
    """A mod archive cannot be read, is unsafe or has an unknown structure."""


class GitMissingError(SptmodError):
    """The git binary is missing or cannot be executed.

    Deliberately not a :class:`GitError`: the handlers that translate a failed
    git call into an operation-specific message would hide the plain-language
    installation hint this error carries.
    """


class GitError(SptmodError):
    """A git operation failed.

    Carries the conflicting paths for failed revert/cherry-pick operations.
    """

    def __init__(self, message: str, conflicting_paths: Sequence[Path] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.conflicting_paths: list[Path] = list(conflicting_paths)


class AlreadyInitializedError(GitError):
    """The game directory is already an initialized sptmod repository.

    Raised by init on a repository that has commits; callers such as the
    setup flow treat this as "setup is done" rather than as a failure.
    """


class ForgeUnavailableError(SptmodError):
    """The Forge API is unreachable or still failing after all retries.

    Raised on network errors, server 5xx responses, or when the maximum retry
    count is exhausted.
    """


class InvalidPathError(SptmodError):
    """A browsed path is missing, is no directory or cannot be read."""


class CatalogUnavailableError(SptmodError):
    """The local catalog cache cannot be opened or created."""


class SptReleaseUnavailableError(SptmodError):
    """The list of published SPT releases cannot be read or holds no usable entry.

    Raised when the release source is unreachable, answers with an error
    status, or sends a body that is not a list of releases.
    """


class SptReleaseWriteError(SptmodError):
    """The files of an SPT release could not be written into the game directory."""


class ModFileWriteError(SptmodError):
    """The files of a mod could not be written into the game directory."""


class ClientBuildMismatchError(SptmodError):
    """The installed Escape from Tarkov build does not match the SPT release."""


class SptModsIncompatibleError(SptmodError):
    """Installed mods have no version for the SPT version that was asked for.

    Attributes:
        spt_version: The SPT version the change was refused for.
        mods: The installed mods the mod list has no version of for it.
    """

    def __init__(self, spt_version: str, mods: Sequence[str]) -> None:
        super().__init__(
            f"these installed mods have no version for SPT {spt_version}: "
            f"{', '.join(mods)}. Update or remove them first, or change the "
            f"SPT version anyway."
        )
        self.spt_version = spt_version
        self.mods: list[str] = list(mods)


class ForgeApiError(SptmodError):
    """The Forge API answered with an error envelope.

    Attributes:
        code: The error code from the API response (e.g., 'VALIDATION_FAILED',
            'NOT_FOUND', or 'UNKNOWN' if missing).
        message: The human-readable error message from the API.
        status: The HTTP status code of the response.
    """

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ModNotFoundError(SptmodError):
    """No mod matching the requested name is known to the repository."""


class AmbiguousModError(SptmodError):
    """Several mods match the requested name case-insensitively."""

    def __init__(self, name: str, candidates: Sequence[str]) -> None:
        super().__init__(
            f"{name!r} matches several mods: {', '.join(sorted(candidates))}"
        )
        self.name = name
        self.candidates: list[str] = list(candidates)


class ModSetNotFoundError(SptmodError):
    """The game directory has no mod set with the requested name."""


class ModSetExistsError(SptmodError):
    """The game directory already has a mod set with the requested name."""


class ModSetIncompleteError(SptmodError):
    """A mod set names mods the game directory does not have.

    Attributes:
        name: The name of the mod set.
        missing: The names of the mods the set needs and the installation
            does not have.
    """

    def __init__(self, name: str, missing: Sequence[str]) -> None:
        super().__init__(
            f"the mod set {name!r} needs mods that are not installed: "
            f"{', '.join(missing)}"
        )
        self.name = name
        self.missing: list[str] = list(missing)


class NotImplementedYetError(SptmodError):
    """The requested operation is wired but has no implementation yet."""


class PartialInstallError(SptmodError):
    """An install plan failed after some of its mods were already installed.

    The commits of the finished mods stay in place, so the game directory is
    consistent and the install can be repeated once the cause is fixed.

    Attributes:
        installed: The mods installed before the failure, in install order.
        cause: The error that stopped the run.
    """

    def __init__(
        self, message: str, installed: Sequence["InstallResult"], cause: Exception
    ) -> None:
        super().__init__(message)
        self.message = message
        self.installed: list[InstallResult] = list(installed)
        self.cause = cause


class UnresolvableDependencyError(DependencyError):
    """A dependency of a requested mod cannot be resolved for an install plan.

    Raised when Forge reports a version conflict for the dependency, publishes
    no compatible version for the queried SPT release, or when the dependency
    graph contains a cycle.

    Attributes:
        dependency: The name of the dependency the failure is about.
        reason: The human-readable cause, already part of the message.
    """

    def __init__(self, dependency: str, reason: str) -> None:
        super().__init__(f"dependency {dependency!r} cannot be resolved: {reason}")
        self.dependency = dependency
        self.reason = reason
