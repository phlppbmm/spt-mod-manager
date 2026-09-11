"""The ``local`` command group: mods a player builds in a folder of their own."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from sptmod.cli.base import DATE_FORMAT as _DATE_FORMAT
from sptmod.cli.base import console, local_app
from sptmod.cli.base import handled as _handled
from sptmod.cli.base import report_progress as _report_progress
from sptmod.core import services

# What a folder that has built nothing yet reads as, everywhere it is listed.
NOTHING_BUILT = "nothing built yet"

# Taking a folder off the list uninstalls what it installed, while removing a
# mod on its own leaves its folder registered; both surfaces say so.
REMOVAL_UNINSTALLS = (
    "The mods it installed were uninstalled with it. Removing a mod on its "
    "own never takes its folder off this list."
)


def _source_table(sources: Sequence[services.LocalSourceInfo]) -> Table:
    table = Table(title="local mod folders")
    for column in ("Folder", "Mod", "Version", "Built", "Installed"):
        table.add_column(column)
    for source in sources:
        if not source.mods:
            table.add_row(str(source.folder), NOTHING_BUILT, "", "", "")
            continue
        for offered in source.mods:
            table.add_row(
                str(source.folder),
                offered.name,
                offered.version,
                offered.built_at.strftime(_DATE_FORMAT),
                f"{offered.mod.version} ({offered.mod.state})" if offered.mod else "",
            )
    return table


@local_app.command("add")
def local_add(
    folder: Annotated[
        Path,
        typer.Argument(metavar="FOLDER", help="Project folder you build the mod in."),
    ],
) -> None:
    """Register a folder you build mods in.

    sptmod never builds anything; it offers the mods the folder's ``build``
    folder holds and installs the newest build of the one you name.
    """
    with _handled():
        console.print(_source_table(services.add_local_source(Path.cwd(), folder)))


@local_app.command("list")
def local_list() -> None:
    """List the folders you build mods in and the mods each of them offers."""
    with _handled():
        console.print(_source_table(services.list_local_sources(Path.cwd())))


@local_app.command("remove")
def local_remove(
    folder: Annotated[
        Path, typer.Argument(metavar="FOLDER", help="Registered project folder.")
    ],
) -> None:
    """Take a folder off the list of folders you build mods in.

    The mods installed from that folder are uninstalled with it.
    """
    with _handled():
        sources = services.remove_local_source(Path.cwd(), folder)
        console.print(f"{folder} is no longer a local mod folder", markup=False)
        console.print(REMOVAL_UNINSTALLS, markup=False)
        console.print(_source_table(sources))


@local_app.command("install")
def local_install(
    folder: Annotated[
        Path, typer.Argument(metavar="FOLDER", help="Registered project folder.")
    ],
    mod: Annotated[
        str, typer.Argument(metavar="MOD", help="Mod of that folder to install.")
    ],
) -> None:
    """Install the newest build of one mod of a folder you build mods in.

    A build of this mod that is installed already is taken out first, so no
    file of the previous build survives into the new one, and a rebuild
    carrying the same version is installed like any other.
    """
    with _handled():
        result = services.install_local_source(
            Path.cwd(), folder, mod, progress=_report_progress
        )
        for warning in result.warnings:
            console.print(f"warning: {warning.message}", markup=False)
        console.print(
            f"{'reinstalled' if result.replaced else 'installed'} "
            f"{result.mod.name} {result.mod.version}".rstrip()
            + f" ({len(result.files)} files) from {result.archive.name}",
            markup=False,
        )
