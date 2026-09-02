"""Typed exceptions raised by the sptmod core."""

from collections.abc import Sequence
from pathlib import Path


class SptmodError(Exception):
    """Base class for every error raised by the sptmod core."""


class NotAGameDirError(SptmodError):
    """The given path is not a usable SPT game directory."""


class DirtyTreeError(SptmodError):
    """The repository has uncommitted changes blocking the operation."""


class GameRunningError(SptmodError):
    """The SPT server or client is running."""


class ConflictError(SptmodError):
    """A file, DLL or install conflict blocks the operation."""


class DependencyError(SptmodError):
    """A required mod dependency is missing or has an incompatible version."""


class ProfileResidueError(SptmodError):
    """Removing a mod would leave residue in at least one player profile."""


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
    """The Forge API is unreachable or still failing after all retries."""


class ForgeApiError(SptmodError):
    """The Forge API answered with an error envelope."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
