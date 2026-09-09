"""The reporting commands: profile residue (``check``) and commit history."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from sptmod.cli.base import DATE_FORMAT as _DATE_FORMAT
from sptmod.cli.base import SHORT_HASH_LENGTH as _SHORT_HASH_LENGTH
from sptmod.cli.base import app, console
from sptmod.cli.base import handled as _handled
from sptmod.core import services
from sptmod.core.models import (
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    TRAILER_MOD_VERSION,
    CommitInfo,
    ProfileResidue,
)


def _residue_table(residues: Mapping[str, Sequence[ProfileResidue]]) -> Table:
    table = Table()
    for column in ("Mod", "Profile", "Category", "Count", "IDs"):
        table.add_column(column)
    for mod, found in residues.items():
        for residue in found:
            table.add_row(
                mod,
                residue.profile,
                residue.category,
                str(residue.count),
                ", ".join(residue.ids),
            )
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


@app.command()
def check() -> None:
    """Report mod content the player profiles still reference."""
    with _handled():
        result = services.check_profiles(Path.cwd())
        for warning in result.warnings:
            console.print(f"warning: {warning}", markup=False)
        if not result.residues:
            console.print("no profile residue found", markup=False)
            return
        console.print(_residue_table(result.residues))


@app.command()
def history(
    name: Annotated[
        str | None, typer.Argument(help="Only show commits of this mod.")
    ] = None,
) -> None:
    """Show the commit history of the game directory."""
    with _handled():
        console.print(_history_table(services.history(Path.cwd(), name)))
