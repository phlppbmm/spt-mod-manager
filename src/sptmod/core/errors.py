"""Typed exceptions raised by the sptmod core."""

from collections.abc import Sequence
from pathlib import Path

from sptmod.core.models import Conflict, ProfileResidue


class SptmodError(Exception):
    """Base class for every error raised by the sptmod core."""


class NotAGameDirError(SptmodError):
    """The given path is not a usable SPT game directory."""


class DirtyTreeError(SptmodError):
    """The repository has uncommitted changes blocking the operation."""


class GameRunningError(SptmodError):
    """The SPT server or client is running."""


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


class GitError(SptmodError):
    """A git operation failed.

    Carries the conflicting paths for failed revert/cherry-pick operations.
    """

    def __init__(self, message: str, conflicting_paths: Sequence[Path] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.conflicting_paths: list[Path] = list(conflicting_paths)


class ForgeUnavailableError(SptmodError):
    """The Forge API is unreachable or still failing after all retries.

    Raised on network errors, server 5xx responses, or when the maximum retry
    count is exhausted.
    """


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


class NotImplementedYetError(SptmodError):
    """The requested operation is wired but has no implementation yet."""


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
