"""Command-line interface for sptmod."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final

import typer
from rich.console import Console
from rich.table import Table

from sptmod.core import services
from sptmod.core.errors import (
    AmbiguousModError,
    ArchiveError,
    ConflictError,
    DependencyError,
    DirtyTreeError,
    GameRunningError,
    GitError,
    ModNotFoundError,
    NotAGameDirError,
    ProfileResidueError,
    SptmodError,
)
from sptmod.core.models import (
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    TRAILER_MOD_VERSION,
    CommitInfo,
    ModFile,
    ModInfo,
)
from sptmod.core.modmeta import InvalidMetadataError

app = typer.Typer(help="sptmod: mod manager for SPT (Single Player Tarkov).")

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
    ProfileResidueError: 8,
    ArchiveError: 9,
    GitError: 10,
    ModNotFoundError: 11,
    AmbiguousModError: 12,
    InvalidMetadataError: 13,
}

_DATE_FORMAT: Final = "%Y-%m-%d %H:%M"
_SHORT_HASH_LENGTH: Final = 7


@contextmanager
def _handled() -> Iterator[None]:
    """Turn a typed core error into a stderr message and an exit code."""
    try:
        yield
    except SptmodError as error:
        error_console.print(f"error: {error}", markup=False)
        raise typer.Exit(EXIT_CODES.get(type(error), GENERIC_EXIT_CODE)) from error


def _report_progress(step: str, percent: int, message: str) -> None:
    console.print(f"{percent:3d}% {step}: {message}", markup=False)


def _mod_table(mods: Sequence[ModInfo]) -> Table:
    table = Table()
    for column in ("Name", "Version", "Type", "SPT", "State", "Date", "Commit"):
        table.add_column(column)
    for mod in mods:
        table.add_row(
            mod.name,
            mod.version,
            mod.mod_type,
            mod.spt_range,
            mod.state,
            mod.date.strftime(_DATE_FORMAT),
            mod.commit[:_SHORT_HASH_LENGTH],
        )
    return table


def _file_table(files: Sequence[ModFile]) -> Table:
    table = Table()
    table.add_column("File")
    table.add_column("Change")
    for mod_file in files:
        table.add_row(mod_file.path.as_posix(), mod_file.change)
    return table


def _history_table(commits: Sequence[CommitInfo]) -> Table:
    table = Table()
    for column in ("Commit", "Date", "Subject", "Mod", "Version", "State"):
        table.add_column(column)
    for commit in commits:
        table.add_row(
            commit.hash[:_SHORT_HASH_LENGTH],
            commit.date.strftime(_DATE_FORMAT),
            commit.subject,
            commit.trailers.get(TRAILER_MOD, ""),
            commit.trailers.get(TRAILER_MOD_VERSION, ""),
            commit.trailers.get(TRAILER_MOD_STATE, ""),
        )
    return table


@app.callback()
def callback() -> None:
    """sptmod command-line interface."""


@app.command()
def version() -> None:
    """Print the sptmod version."""
    typer.echo("sptmod 0.1.0")


@app.command()
def init(
    path: Annotated[
        Path | None,
        typer.Argument(help="SPT game directory (default: current directory)."),
    ] = None,
) -> None:
    """Track an SPT installation as a git repository."""
    with _handled():
        result = services.init_game_dir(path or Path.cwd())
        console.print(
            f"initialized SPT {result.spt_version} "
            f"at {result.commit[:_SHORT_HASH_LENGTH]}",
            markup=False,
        )
        console.print(str(result.game_dir), markup=False)
        for mod_path in result.pre_existing_mods:
            console.print(
                f"pre-existing mod, run 'sptmod adopt': {mod_path.as_posix()}",
                markup=False,
            )


@app.command()
def install(
    file: Annotated[
        list[Path],
        typer.Option("--file", "-f", help="Local mod archive; repeat for several."),
    ],
    name: Annotated[
        str | None, typer.Option("--name", help="Override the mod name.")
    ] = None,
    version: Annotated[
        str | None, typer.Option("--version", help="Override the mod version.")
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Install despite conflicts or missing deps."),
    ] = False,
) -> None:
    """Install mods from local archives."""
    with _handled():
        for archive in file:
            result = services.install_from_archive(
                Path.cwd(),
                archive,
                name_override=name,
                version_override=version,
                force=force,
                progress=_report_progress,
            )
            console.print(
                f"installed {result.mod.name} {result.mod.version}".rstrip()
                + f" ({len(result.files)} files)"
                + f" as {result.mod.commit[:_SHORT_HASH_LENGTH]}",
                markup=False,
            )


@app.command("list")
def list_mods(
    all_: Annotated[
        bool, typer.Option("--all", help="Include disabled and removed mods.")
    ] = False,
) -> None:
    """List the installed mods."""
    with _handled():
        console.print(_mod_table(services.list_mods(Path.cwd(), include_all=all_)))


@app.command()
def files(
    name: Annotated[str, typer.Argument(help="Mod name (case-insensitive).")],
) -> None:
    """List the files of a mod."""
    with _handled():
        console.print(_file_table(services.mod_files(Path.cwd(), name)))


@app.command()
def remove(
    name: Annotated[str, typer.Argument(help="Mod name (case-insensitive).")],
    force: Annotated[
        bool, typer.Option("--force", help="Remove despite profile residue.")
    ] = False,
) -> None:
    """Remove an installed mod."""
    with _handled():
        result = services.remove_mod(Path.cwd(), name, force=force)
        console.print(
            f"removed {result.name} ({len(result.files)} files) "
            f"as {result.commit[:_SHORT_HASH_LENGTH]}",
            markup=False,
        )


@app.command()
def history(
    name: Annotated[
        str | None, typer.Argument(help="Only show commits of this mod.")
    ] = None,
) -> None:
    """Show the commit history of the game directory."""
    with _handled():
        console.print(_history_table(services.history(Path.cwd(), name)))


def main() -> None:
    """Entry point for the sptmod console script."""
    app()
