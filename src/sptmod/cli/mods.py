"""The mod commands: install, update, list, files, remove, disable, enable, adopt."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final

import typer
from rich.table import Table

from sptmod.cli.base import DATE_FORMAT as _DATE_FORMAT
from sptmod.cli.base import SHORT_HASH_LENGTH as _SHORT_HASH_LENGTH
from sptmod.cli.base import app, console
from sptmod.cli.base import confirmed as _confirmed
from sptmod.cli.base import handled as _handled
from sptmod.cli.base import print_install as _print_install
from sptmod.cli.base import report_progress as _report_progress
from sptmod.core import services
from sptmod.core.errors import PartialInstallError
from sptmod.core.models import InstallPlan, ModFile, ModInfo

_BYTES_PER_MB: Final = 1024 * 1024


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


def _file_table(mod_files: Sequence[ModFile]) -> Table:
    table = Table()
    table.add_column("File")
    table.add_column("Change")
    for mod_file in mod_files:
        table.add_row(mod_file.path.as_posix(), mod_file.change)
    return table


def _size(content_length: int | None) -> str:
    if content_length is None:
        return ""
    return f"{content_length / _BYTES_PER_MB:.1f} MB"


def _plan_table(plan: InstallPlan) -> Table:
    table = Table(title=f"install plan for SPT {plan.spt_version}")
    for column in ("Name", "Version", "Size", "Reason"):
        table.add_column(column)
    for entry in plan.entries:
        table.add_row(
            entry.name,
            entry.version,
            _size(entry.content_length),
            "dependency" if entry.is_dependency else "requested",
        )
    return table


def _updates_table(report: services.UpdatesReport) -> Table:
    table = Table(title=f"updates for SPT {report.spt_version}")
    for column in ("Mod", "Installed", "Available", "State", "Detail"):
        table.add_column(column)
    for candidate in report.available:
        table.add_row(
            candidate.name,
            candidate.installed_version,
            candidate.new_version,
            "update",
            candidate.reason,
        )
    for blocked in report.blocked:
        table.add_row(
            blocked.name,
            blocked.installed_version,
            blocked.new_version,
            "blocked",
            f"{blocked.reason} ({', '.join(blocked.blocking_mods)})",
        )
    for incompatible in report.incompatible:
        table.add_row(
            incompatible.name,
            incompatible.installed_version,
            incompatible.latest_compatible_version,
            "incompatible",
            incompatible.reason,
        )
    for name in report.up_to_date:
        table.add_row(name, "", "", "up to date", "")
    return table


def _print_update(result: services.UpdateResult) -> None:
    if result.notice:
        console.print(f"notice: {result.notice}", markup=False)
        return
    console.print(
        f"updated {result.name} {result.previous_version} → {result.version} "
        f"({len(result.files)} files) as {result.commit[:_SHORT_HASH_LENGTH]}",
        markup=False,
    )


def _print_toggle(result: services.ToggleResult) -> None:
    if result.notice:
        console.print(f"notice: {result.notice}", markup=False)
        return
    console.print(
        f"{result.state} {result.name} ({len(result.files)} files) "
        f"as {result.commit[:_SHORT_HASH_LENGTH]}",
        markup=False,
    )


@app.command()
def install(
    mod: Annotated[
        str | None,
        typer.Argument(metavar="MOD", help="Forge mod ID or name to install."),
    ] = None,
    file: Annotated[
        list[Path] | None,
        typer.Option("--file", "-f", help="Local mod archive; repeat for several."),
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Override the mod name (--file only).")
    ] = None,
    mod_version: Annotated[
        str | None,
        typer.Option("--version", help="Version to install, or override for --file."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Install despite conflicts or missing deps."),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Install without confirming the plan.")
    ] = False,
) -> None:
    """Install mods from Forge or from local archives.

    With ``--file`` one or more local archives are installed directly. Without it
    a Forge install plan is resolved, shown for confirmation (skipped with ``--yes``),
    and then installed. Each mod becomes its own commit.
    """
    if file:
        if mod is not None:
            raise typer.BadParameter("pass either MOD or --file, not both")
        with _handled():
            for archive in file:
                archive_result = services.install_from_archive(
                    Path.cwd(),
                    archive,
                    name_override=name,
                    version_override=mod_version,
                    force=force,
                    progress=_report_progress,
                )
                _print_install(archive_result)
                if not archive_result.mod.version:
                    console.print(
                        f"hint: {archive.name} carries no readable version; "
                        "pass --version to record one",
                        markup=False,
                    )
        return
    if mod is None:
        raise typer.BadParameter("pass a mod ID or name, or --file ARCHIVE")
    with _handled():
        plan = services.plan_mod_install(Path.cwd(), mod, mod_version)
        console.print(_plan_table(plan))
        if not yes and not typer.confirm("install this plan?", default=True):
            console.print("cancelled", markup=False)
            return
        try:
            result = services.install_mod(
                Path.cwd(), mod, mod_version, force=force, progress=_report_progress
            )
        except PartialInstallError as error:
            for installed in error.installed:
                _print_install(installed)
            raise
        for installed in result.installed:
            _print_install(installed)


@app.command()
def update(
    names: Annotated[
        list[str] | None,
        typer.Argument(metavar="[NAME]...", help="Mods to update; default: all."),
    ] = None,
    check_only: Annotated[
        bool, typer.Option("--check", help="Only report the available updates.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Update despite conflicts or missing deps.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Update without confirming.")
    ] = False,
) -> None:
    """Update installed mods to the versions Forge recommends.

    Queries Forge for updates, shows the available ones (skipped with ``--check``),
    asks for confirmation (skipped with ``--yes``), and applies them one at a time.
    """
    with _handled():
        report = services.check_updates(Path.cwd(), names)
        console.print(_updates_table(report))
        for skipped in report.skipped:
            console.print(f"not identified on Forge: {skipped}", markup=False)
        if check_only:
            return
        if not report.available:
            console.print("nothing to update", markup=False)
            return
        if not yes and not typer.confirm("apply these updates?", default=True):
            console.print("cancelled", markup=False)
            return
        for candidate in report.available:
            _print_update(
                services.update_mod(
                    Path.cwd(),
                    candidate.name,
                    force=force,
                    progress=_report_progress,
                )
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
    clean_profiles: Annotated[
        bool,
        typer.Option(
            "--clean-profiles", help="Also remove the mod's content from the profiles."
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Remove without confirming.")
    ] = False,
) -> None:
    """Remove an installed mod."""
    if not yes and not _confirmed(f"remove {name}?"):
        raise typer.Abort()
    with _handled():
        result = services.remove_mod(
            Path.cwd(), name, force=force, clean_profiles=clean_profiles
        )
        for warning in result.warnings:
            console.print(f"warning: {warning}", markup=False)
        console.print(
            f"removed {result.name} ({len(result.files)} files) "
            f"as {result.commit[:_SHORT_HASH_LENGTH]}",
            markup=False,
        )
        if result.cleanup is not None:
            _print_cleanup(result.cleanup)


def _print_cleanup(cleanup: services.CleanupResult) -> None:
    """Report what the profile cleanup changed and what it left alone."""
    if cleanup.profiles:
        console.print(f"cleaned profiles: {', '.join(cleanup.profiles)}", markup=False)
    else:
        console.print("no profile needed cleaning", markup=False)
    console.print(f"removed {len(cleanup.removed)} profile entries", markup=False)
    if cleanup.untouched:
        console.print(
            f"{len(cleanup.untouched)} entries stayed: sptmod cannot tell what "
            "they are used for, so it leaves them alone",
            markup=False,
        )


@app.command()
def disable(
    name: Annotated[str, typer.Argument(help="Mod name (case-insensitive).")],
) -> None:
    """Disable an installed mod without losing its files."""
    with _handled():
        _print_toggle(services.disable_mod(Path.cwd(), name))


@app.command()
def enable(
    name: Annotated[str, typer.Argument(help="Mod name (case-insensitive).")],
) -> None:
    """Enable a disabled mod again."""
    with _handled():
        _print_toggle(services.enable_mod(Path.cwd(), name))


@app.command()
def adopt(
    target: Annotated[
        Path,
        typer.Argument(
            metavar="PATH", help="Untracked mod folder or DLL in the game directory."
        ),
    ],
    name: Annotated[
        str | None, typer.Option("--name", help="Override the mod name.")
    ] = None,
    mod_version: Annotated[
        str | None, typer.Option("--version", help="Override the mod version.")
    ] = None,
) -> None:
    """Take a manually installed mod into the repository."""
    with _handled():
        result = services.adopt_mod(
            Path.cwd(), target, name_override=name, version_override=mod_version
        )
        console.print(
            f"adopted {result.mod.name} {result.mod.version}".rstrip()
            + f" ({len(result.files)} files)"
            + f" as {result.mod.commit[:_SHORT_HASH_LENGTH]}",
            markup=False,
        )
