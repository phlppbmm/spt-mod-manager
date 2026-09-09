"""The repository commands: status, snapshot, export and import of the mod list."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from sptmod.cli.base import SHORT_HASH_LENGTH as _SHORT_HASH_LENGTH
from sptmod.cli.base import app, console
from sptmod.cli.base import handled as _handled
from sptmod.cli.base import print_install as _print_install
from sptmod.cli.base import report_progress as _report_progress
from sptmod.core import services


def _export_format(value: str) -> services.ExportFormat:
    for known in services.EXPORT_FORMATS:
        if value == known:
            return known
    raise typer.BadParameter(
        f"--format must be one of {', '.join(services.EXPORT_FORMATS)}"
    )


def _import_table(diff: services.ImportDiff) -> Table:
    table = Table()
    for column in ("Change", "Name", "Wanted", "Installed", "Link"):
        table.add_column(column)
    groups = (
        ("missing", diff.missing),
        ("version differs", diff.different),
        ("extra", diff.extra),
    )
    for change, entries in groups:
        for entry in entries:
            table.add_row(
                change,
                entry.name,
                entry.wanted_version,
                entry.installed_version,
                entry.link,
            )
    return table


def _print_paths(headline: str, paths: Sequence[Path]) -> None:
    if not paths:
        return
    console.print(headline, markup=False)
    for path in paths:
        console.print(f"  {path.as_posix()}", markup=False)


def _print_status(report: services.StatusReport) -> None:
    _print_paths(
        "modified profiles (normal after playing, keep them with 'sptmod snapshot'):",
        report.modified_profiles,
    )
    _print_paths(
        "modified configs (keep them with 'sptmod snapshot'):",
        report.modified_configs,
    )
    for manual in report.manual_mods:
        console.print(
            f"manually installed ({len(manual.files)} files), take it over "
            f"with 'sptmod adopt {manual.path.as_posix()}'",
            markup=False,
        )
    if report.modified_mod_settings:
        console.print(
            f"Mod settings changed since the last snapshot "
            f"({len(report.modified_mod_settings)} files).",
            markup=False,
        )
    if report.game_files_changed:
        console.print(
            f"Game files changed since the last snapshot "
            f"({len(report.game_files_changed)} files), probably an SPT update.",
            markup=False,
        )
    _print_paths("other changes:", report.other_modified)
    _print_paths("other untracked files:", report.other_untracked)
    if not any(
        (
            report.modified_profiles,
            report.modified_configs,
            report.modified_mod_settings,
            report.manual_mods,
            report.game_files_changed,
            report.other_modified,
            report.other_untracked,
        )
    ):
        console.print("nothing changed", markup=False)


@app.command()
def status() -> None:
    """Show what changed in the game directory since the last commit."""
    with _handled():
        _print_status(services.status(Path.cwd()))


@app.command()
def snapshot(
    message: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Snapshot message (default: timestamp)."),
    ] = None,
) -> None:
    """Secure updated game files, changed mod settings, profiles and configs."""
    with _handled():
        result = services.snapshot(Path.cwd(), message)
        if result.game_files:
            console.print(
                f"secured {len(result.game_files)} updated game files",
                markup=False,
            )
        if result.mod_settings:
            console.print(
                f"secured {len(result.mod_settings)} changed mod settings",
                markup=False,
            )
        if result.commit:
            console.print(
                f"snapshot: {result.message} ({len(result.files)} files) "
                f"as {result.commit[:_SHORT_HASH_LENGTH]}",
                markup=False,
            )


@app.command()
def export(
    output_format: Annotated[
        str, typer.Option("--format", help="Output format: text or json.")
    ] = "text",
    client_only: Annotated[
        bool, typer.Option("--client-only", help="Only mods with a client part.")
    ] = False,
) -> None:
    """Write the active mod list as text or JSON."""
    with _handled():
        typer.echo(
            services.export_mods(
                Path.cwd(), _export_format(output_format), client_only=client_only
            ),
            nl=False,
        )


@app.command("import")
def import_mods(
    list_file: Annotated[
        Path, typer.Argument(metavar="LIST", help="Exported mod list (text or JSON).")
    ],
    apply: Annotated[
        bool, typer.Option("--apply", help="Install the missing mods from Forge.")
    ] = False,
) -> None:
    """Compare an exported mod list with the installed mods."""
    with _handled():
        diff = services.import_list(Path.cwd(), list_file, apply, _report_progress)
        console.print(_import_table(diff))
        for applied in diff.installed:
            for installed in applied.installed:
                _print_install(installed)
