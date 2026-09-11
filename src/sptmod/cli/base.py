"""The typer apps, consoles, exit codes and helpers every command group shares.

This module is the hub of the ``sptmod.cli`` package: it imports nothing from
its siblings, they all import from it. ``spt_app`` and ``local_app`` are
created here rather than in :mod:`sptmod.cli.spt` and :mod:`sptmod.cli.local`
because ``app.add_typer`` needs them.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

import typer
from rich.console import Console

from sptmod.core import services
from sptmod.core.errors import (
    AlreadyInitializedError,
    AmbiguousModError,
    ArchiveError,
    ClientBuildMismatchError,
    ConflictError,
    DependencyError,
    DirtyTreeError,
    ForgeUnavailableError,
    GameRunningError,
    GitError,
    GitMissingError,
    ModFileWriteError,
    ModNotFoundError,
    NotAGameDirError,
    NotImplementedYetError,
    ProfileResidueError,
    SptmodError,
    SptModsIncompatibleError,
    SptReleaseRateLimitedError,
    SptReleaseUnavailableError,
    SptReleaseWriteError,
    SptVersionUnknownError,
    UnresolvableDependencyError,
)
from sptmod.core.modmeta import InvalidMetadataError

app = typer.Typer(help="sptmod: mod manager for SPT (Single Player Tarkov).")
spt_app = typer.Typer(help="Change the SPT version of the game directory.")
app.add_typer(spt_app, name="spt")
local_app = typer.Typer(help="Mods you build in a folder of your own.")
app.add_typer(local_app, name="local")

console = Console()
error_console = Console(stderr=True)

# Exit code mapping for core errors. Unhandled errors use GENERIC_EXIT_CODE.
# Allows external scripts to distinguish error types by exit status.
GENERIC_EXIT_CODE: Final = 1
EXIT_CODES: Final[dict[type[SptmodError], int]] = {
    NotAGameDirError: 3,
    DirtyTreeError: 4,
    GameRunningError: 5,
    ConflictError: 6,
    DependencyError: 7,
    UnresolvableDependencyError: 7,
    ProfileResidueError: 8,
    ArchiveError: 9,
    GitError: 10,
    AlreadyInitializedError: 10,
    ModNotFoundError: 11,
    AmbiguousModError: 12,
    InvalidMetadataError: 13,
    ForgeUnavailableError: 14,
    NotImplementedYetError: 15,
    SptVersionUnknownError: 16,
    GitMissingError: 17,
    SptReleaseUnavailableError: 18,
    SptReleaseRateLimitedError: 18,
    ClientBuildMismatchError: 19,
    SptReleaseWriteError: 20,
    ModFileWriteError: 21,
    SptModsIncompatibleError: 22,
}

# What a core message cannot say, because it is shown on every transport: the
# option of this command line that goes ahead regardless.
ERROR_HINTS: Final[dict[type[SptmodError], str]] = {
    SptModsIncompatibleError: " Repeat the command with --allow-incompatible-mods to do that.",
}

DATE_FORMAT: Final = "%Y-%m-%d %H:%M"
SHORT_HASH_LENGTH: Final = 7


@contextmanager
def handled() -> Generator[None]:
    """Turn a typed core error into a stderr message and an exit code."""
    try:
        yield
    except SptmodError as error:
        hint = ERROR_HINTS.get(type(error), "")
        error_console.print(f"error: {error}{hint}", markup=False)
        raise typer.Exit(EXIT_CODES.get(type(error), GENERIC_EXIT_CODE)) from error


def report_progress(step: str, percent: int, message: str) -> None:
    """Print one progress report of a long-running service call."""
    console.print(f"{percent:3d}% {step}: {message}", markup=False)


def confirmed(question: str) -> bool:
    """Ask ``question``, refusing it when no answer can be read."""
    try:
        return typer.confirm(question, default=True)
    except typer.Abort:
        return False


def print_install(result: services.InstallResult) -> None:
    """Report one installed mod with its file count and commit."""
    console.print(
        f"installed {result.mod.name} {result.mod.version}".rstrip()
        + f" ({len(result.files)} files)"
        + f" as {result.mod.commit[:SHORT_HASH_LENGTH]}",
        markup=False,
    )


@app.callback()
def callback() -> None:
    """sptmod command-line interface."""


def main() -> None:
    """Entry point for the sptmod console script."""
    app()
