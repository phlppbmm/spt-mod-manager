"""Command-line interface for sptmod."""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final

import typer
from rich.console import Console
from rich.table import Table

from sptmod import __version__
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
    ModNotFoundError,
    NotAGameDirError,
    NotImplementedYetError,
    PartialInstallError,
    ProfileResidueError,
    SptmodError,
    SptReleaseUnavailableError,
    SptReleaseWriteError,
    SptVersionUnknownError,
    UnresolvableDependencyError,
)
from sptmod.core.forge.catalog import CatalogEntry
from sptmod.core.models import (
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    TRAILER_MOD_VERSION,
    CommitInfo,
    InstallPlan,
    ModFile,
    ModInfo,
    ProfileResidue,
)
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.sptrelease.apply import compare_versions
from sptmod.server.rpc import main as run_rpc

app = typer.Typer(help="sptmod: mod manager for SPT (Single Player Tarkov).")
spt_app = typer.Typer(help="Change the SPT version of the game directory.")
app.add_typer(spt_app, name="spt")

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
    ClientBuildMismatchError: 19,
    SptReleaseWriteError: 20,
}

_DATE_FORMAT: Final = "%Y-%m-%d %H:%M"
_SHORT_HASH_LENGTH: Final = 7
_BYTES_PER_MB: Final = 1024 * 1024


@contextmanager
def _handled() -> Generator[None]:
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


def _file_table(mod_files: Sequence[ModFile]) -> Table:
    table = Table()
    table.add_column("File")
    table.add_column("Change")
    for mod_file in mod_files:
        table.add_row(mod_file.path.as_posix(), mod_file.change)
    return table


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


def _confirmed(question: str) -> bool:
    """Ask ``question``, refusing it when no answer can be read."""
    try:
        return typer.confirm(question, default=True)
    except typer.Abort:
        return False


def _print_install(result: services.InstallResult) -> None:
    console.print(
        f"installed {result.mod.name} {result.mod.version}".rstrip()
        + f" ({len(result.files)} files)"
        + f" as {result.mod.commit[:_SHORT_HASH_LENGTH]}",
        markup=False,
    )


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


@app.callback()
def callback() -> None:
    """sptmod command-line interface."""


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
                    forge_match=True,
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


def _spt_table(releases: services.SptReleaseList) -> Table:
    table = Table(
        title=f"SPT releases (installed: {releases.current_version or 'unknown'})"
    )
    for column in ("Version", "EFT build", "Released", "State", "Download"):
        table.add_column(column)
    for release in releases.releases:
        table.add_row(
            release.version,
            release.client_build,
            release.published_at.strftime(_DATE_FORMAT),
            _release_state(release),
            "yes" if release.downloadable else "no",
        )
    return table


def _release_state(release: services.SptReleaseInfo) -> str:
    """Describe how ``release`` stands to the installed version."""
    if release.installed:
        return "installed"
    return "newer" if release.newer else "older"


def _print_spt_result(result: services.SptApplyResult) -> None:
    """Report the version change and what it means for the installed mods."""
    if not result.commit:
        console.print(
            f"SPT {result.version} is already in place, nothing changed",
            markup=False,
        )
        return
    console.print(
        f"SPT is now {result.version} ({len(result.files)} files)", markup=False
    )
    console.print(
        "mods made for the old version may no longer fit; check them with "
        "'sptmod update --check'",
        markup=False,
    )


def _confirm_spt_change(wanted: str, current: str, yes: bool) -> None:
    """Ask before the version changes, naming the safety copy in plain words."""
    if yes:
        return
    if not _confirmed(
        f"change SPT from {current or 'the installed version'} to {wanted}? "
        "Your profiles, settings and installed mods stay as they are, and the "
        "current state of the game folder is secured first, so you can go back"
    ):
        raise typer.Abort()


def _confirm_spt_undo(yes: bool) -> None:
    """Ask before the last SPT change is taken back."""
    if yes:
        return
    if not _confirmed(
        "Undo the last SPT change? sptmod secures the current state of your "
        "game folder first"
    ):
        raise typer.Abort()


def _spt_releases(wanted: str, newer: bool) -> services.SptReleaseList:
    """Return the release list, refusing ``wanted`` in the wrong direction.

    While the installed version cannot be read there is nothing to compare
    against, so both directions are allowed.
    """
    releases = services.list_spt_releases(Path.cwd())
    if not releases.current_version:
        return releases
    order = compare_versions(wanted, releases.current_version)
    installed = releases.current_version
    if newer and order <= 0:
        raise typer.BadParameter(
            f"SPT {wanted} is not newer than the installed {installed}; "
            f"go back to it with 'sptmod spt downgrade'"
        )
    if not newer and order >= 0:
        raise typer.BadParameter(
            f"SPT {wanted} is not older than the installed {installed}; "
            f"move up to it with 'sptmod spt update'"
        )
    return releases


def _change_spt_version(wanted: str, newer: bool, yes: bool) -> None:
    """Confirm and then put the SPT version ``wanted`` into the game directory."""
    releases = _spt_releases(wanted, newer)
    _confirm_spt_change(wanted, releases.current_version, yes)
    _print_spt_result(services.apply_spt_release(Path.cwd(), wanted, _report_progress))


@spt_app.command("list")
def spt_list() -> None:
    """List the SPT versions the game directory can change to."""
    with _handled():
        releases = services.list_spt_releases(Path.cwd())
        console.print(_spt_table(releases))
        console.print(
            f"installed Escape from Tarkov build: {releases.client_build or 'unknown'}",
            markup=False,
        )


@spt_app.command("update")
def spt_update(
    spt_version: Annotated[
        str, typer.Argument(metavar="VERSION", help="SPT version to change to.")
    ],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Change the version without asking.")
    ] = False,
) -> None:
    """Change the game directory to a newer SPT version."""
    with _handled():
        _change_spt_version(spt_version, newer=True, yes=yes)


@spt_app.command("downgrade")
def spt_downgrade(
    spt_version: Annotated[
        str | None,
        typer.Argument(
            metavar="[VERSION]",
            help="SPT version to go back to; default: undo the last change.",
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Change the version without asking.")
    ] = False,
) -> None:
    """Go back to an older SPT version, or undo the last SPT change."""
    with _handled():
        if spt_version is None:
            _confirm_spt_undo(yes)
            _print_spt_result(services.revert_spt_update(Path.cwd()))
            return
        _change_spt_version(spt_version, newer=False, yes=yes)


@app.command(hidden=True)
def rpc(
    name: Annotated[str, typer.Argument(help="Name of the service function to call.")],
) -> None:
    """Call a service function with a JSON request on stdin.

    Writes NDJSON to stdout: one line per progress report, then one result or
    error line. Meant for a driver process, not for interactive use.
    """
    raise typer.Exit(run_rpc(name))


def main() -> None:
    """Entry point for the sptmod console script."""
    app()
