"""The ``version`` and ``init`` commands: putting a game directory under sptmod."""

from pathlib import Path
from typing import Annotated

import typer

from sptmod import __version__
from sptmod.cli.base import SHORT_HASH_LENGTH as _SHORT_HASH_LENGTH
from sptmod.cli.base import app, console
from sptmod.cli.base import handled as _handled
from sptmod.core import services
from sptmod.core.errors import SptVersionUnknownError


@app.command()
def version() -> None:
    """Print the sptmod version."""
    typer.echo(f"sptmod {__version__}")


@app.command()
def init(
    path: Annotated[
        Path | None,
        typer.Argument(help="SPT game directory (default: current directory)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Adopt pre-existing mods without asking."),
    ] = False,
    spt_version: Annotated[
        str | None,
        typer.Option(
            "--spt-version",
            help="SPT version to record when it cannot be detected.",
        ),
    ] = None,
) -> None:
    """Track an SPT installation as a git repository."""
    game_dir = path or Path.cwd()
    with _handled():
        try:
            result = services.init_game_dir(
                game_dir, adopt=yes, spt_version=spt_version
            )
        except SptVersionUnknownError:
            result = services.init_game_dir(
                game_dir, adopt=yes, spt_version=_ask_spt_version()
            )
        for warning in result.warnings:
            console.print(f"warning: {warning}", markup=False)
        console.print(
            f"initialized SPT {result.spt_version} "
            f"at {result.commit[:_SHORT_HASH_LENGTH]}",
            markup=False,
        )
        console.print(str(result.game_dir), markup=False)
        for mod in result.adopted:
            console.print(f"adopted {mod.name} {mod.version}".rstrip(), markup=False)
        if yes:
            return
        for mod_path in result.pre_existing_mods:
            _adopt_pre_existing(result.game_dir, mod_path)


def _ask_spt_version() -> str:
    """Ask for the SPT version the detection could not determine."""
    console.print(
        "the SPT version of this installation cannot be detected", markup=False
    )
    answer: str = typer.prompt("SPT version (for example 4.1.2)")
    return answer


def _adopt_pre_existing(game_dir: Path, mod_path: Path) -> None:
    if not typer.confirm(
        f"adopt pre-existing mod {mod_path.as_posix()}?", default=True
    ):
        console.print(
            f"left untracked, run 'sptmod adopt {mod_path.as_posix()}' later",
            markup=False,
        )
        return
    result = services.adopt_mod(game_dir, mod_path)
    console.print(
        f"adopted {result.mod.name} {result.mod.version}".rstrip()
        + f" ({len(result.files)} files)"
        + f" as {result.mod.commit[:_SHORT_HASH_LENGTH]}",
        markup=False,
    )
