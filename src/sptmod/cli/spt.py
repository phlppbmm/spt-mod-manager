"""The ``spt`` command group: listing, updating and downgrading the SPT version."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from sptmod.cli.base import DATE_FORMAT as _DATE_FORMAT
from sptmod.cli.base import confirmed as _confirmed
from sptmod.cli.base import console, spt_app
from sptmod.cli.base import handled as _handled
from sptmod.cli.base import report_progress as _report_progress
from sptmod.core import services
from sptmod.core.sptrelease.apply import compare_versions


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


def _released_version(wanted: str, releases: services.SptReleaseList) -> str:
    """Return the published version ``wanted`` names, or ``wanted`` when none does.

    The version a player types can be shorter than the published one, and the
    mods are judged against the version that will actually be put in place.
    """
    return next(
        (
            release.version
            for release in releases.releases
            if compare_versions(release.version, wanted) == 0
        ),
        wanted,
    )


def _report_mod_check(version: str) -> None:
    """Name the installed mods that stand against the change to ``version``."""
    report = services.check_spt_compatibility(Path.cwd(), version)
    if not report.checked:
        console.print(report.notice, markup=False)
        return
    if report.incompatible:
        console.print(
            f"these installed mods have no version for SPT {version}: "
            f"{', '.join(report.incompatible)}",
            markup=False,
        )
    if report.skipped:
        console.print(
            f"these installed mods could not be checked against SPT {version}: "
            f"{', '.join(report.skipped)}",
            markup=False,
        )


def _change_spt_version(
    wanted: str, newer: bool, yes: bool, allow_incompatible_mods: bool
) -> None:
    """Confirm and then put the SPT version ``wanted`` into the game directory."""
    releases = _spt_releases(wanted, newer)
    _report_mod_check(_released_version(wanted, releases))
    _confirm_spt_change(wanted, releases.current_version, yes)
    _print_spt_result(
        services.apply_spt_release(
            Path.cwd(),
            wanted,
            _report_progress,
            allow_incompatible_mods=allow_incompatible_mods,
        )
    )


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
    allow_incompatible_mods: Annotated[
        bool,
        typer.Option(
            "--allow-incompatible-mods",
            help="Change the version even when installed mods have no version for it.",
        ),
    ] = False,
) -> None:
    """Change the game directory to a newer SPT version."""
    with _handled():
        _change_spt_version(
            spt_version,
            newer=True,
            yes=yes,
            allow_incompatible_mods=allow_incompatible_mods,
        )


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
    allow_incompatible_mods: Annotated[
        bool,
        typer.Option(
            "--allow-incompatible-mods",
            help=(
                "Change the version even when installed mods have no version "
                "for it; without VERSION there is nothing to check, so it does "
                "nothing there."
            ),
        ),
    ] = False,
) -> None:
    """Go back to an older SPT version, or undo the last SPT change."""
    with _handled():
        if spt_version is None:
            _confirm_spt_undo(yes)
            _print_spt_result(services.revert_spt_update(Path.cwd()))
            return
        _change_spt_version(
            spt_version,
            newer=False,
            yes=yes,
            allow_incompatible_mods=allow_incompatible_mods,
        )
