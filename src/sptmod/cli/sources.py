"""The Forge catalog commands: sync, search and browse.

Named ``sources`` and not ``catalog`` on purpose: the package facade imports its
modules in alphabetical order, and that order is the order typer prints the
commands in ``sptmod --help``. A module named ``catalog`` would sort first and
move sync, search and browse to the top of the help text.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from sptmod.cli.base import app, console
from sptmod.cli.base import handled as _handled
from sptmod.cli.base import report_progress as _report_progress
from sptmod.core import services
from sptmod.core.forge.catalog import CatalogEntry


def _catalog_table(entries: Sequence[CatalogEntry]) -> Table:
    table = Table()
    for column in ("Name", "Author", "Installed", "Latest", "State"):
        table.add_column(column)
    for entry in entries:
        table.add_row(
            entry.mod.name,
            entry.mod.author,
            entry.installed_version or "",
            "" if entry.latest_version is None else entry.latest_version.version,
            entry.state,
        )
    return table


def _browse_filter(
    installed: bool, available: bool, all_: bool
) -> services.BrowseFilter:
    if sum((installed, available, all_)) > 1:
        raise typer.BadParameter(
            "--installed, --available and --all are mutually exclusive"
        )
    if installed:
        return "installed"
    if available:
        return "available"
    return "all"


@app.command()
def sync(
    full: Annotated[
        bool, typer.Option("--full", help="Fetch the whole catalog, not just changes.")
    ] = False,
) -> None:
    """Refresh the local Forge catalog cache."""
    with _handled():
        result = services.sync_catalog(Path.cwd(), full=full, progress=_report_progress)
        console.print(
            f"{'full' if result.full else 'delta'} sync over {result.pages} pages: "
            f"{result.new_mods} new and {result.updated_mods} updated mods, "
            f"{result.new_versions} new and "
            f"{result.updated_versions} updated versions",
            markup=False,
        )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Substring of the mod name or author.")],
    compatible: Annotated[
        bool,
        typer.Option("--compatible", help="Only mods compatible with the SPT version."),
    ] = False,
) -> None:
    """Search the cached Forge catalog."""
    with _handled():
        console.print(
            _catalog_table(
                services.search_catalog(Path.cwd(), query, compatible_only=compatible)
            )
        )


@app.command()
def browse(
    installed: Annotated[
        bool, typer.Option("--installed", help="Only mods installed in this game dir.")
    ] = False,
    available: Annotated[
        bool, typer.Option("--available", help="Only mods that are not installed.")
    ] = False,
    all_: Annotated[
        bool, typer.Option("--all", help="Every cached mod (default).")
    ] = False,
    compatible: Annotated[
        bool,
        typer.Option("--compatible", help="Only mods compatible with the SPT version."),
    ] = False,
) -> None:
    """List the cached Forge catalog with the installed state."""
    with _handled():
        console.print(
            _catalog_table(
                services.browse_catalog(
                    Path.cwd(),
                    _browse_filter(installed, available, all_),
                    compatible_only=compatible,
                )
            )
        )
