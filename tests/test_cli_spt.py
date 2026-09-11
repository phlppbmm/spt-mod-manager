"""The ``sptmod spt`` commands: ``list``, ``update`` and ``downgrade``.

Covers the release table with its state and download columns, the installed
client build, the confirmation, its ``--yes`` bypass and the refusals a "no"
and a missing answer produce, the refusal of a
version change in the wrong direction, the exit codes of an unpublished
release, a mismatching game build and a missing version change to undo, the
hint printed after a successful change, both forms of going back, an
update to a release only the current release repository publishes, and how
the commands stand to a release source that cannot be read: the list keeps
its installed facts, ``--refresh`` fetches again, and a version change is
refused while no list can judge it.
"""

import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import clean_repo_at
from fixture_types import MakeArchive, MakeSptRelease
from fixtures_spt import SptReleaseSource, spt_release_members, write_client_exe
from pe_builder import write_server_exe
from sptmod.cli import app
from sptmod.core import services

runner = CliRunner()

INSTALLED = "4.1.0"
NEW = "4.1.1"
NEWEST = "4.1.2"
# The release the current repository publishes; the archived one it replaced
# stopped at NEWEST.
CONTINUED = "4.1.5"
OLDER = "4.0.9"
BUILD = "40743"
OTHER_BUILD = "40999"

# Exit codes cli.EXIT_CODES maps the refusals of a version change to.
BAD_PARAMETER = 2
RELEASE_UNAVAILABLE = 18
BUILD_MISMATCH = 19
WRITE_FAILED = 20
GIT_FAILED = 10


# Rich draws the header row of a table with heavy and its body with light
# vertical borders.
_BORDERS = "│┃"


def _rows(output: str) -> list[list[str]]:
    """Return the cells of every table row in ``output``, header included."""
    return [
        [cell.strip() for cell in re.split(f"[{_BORDERS}]", line.strip(f" {_BORDERS}"))]
        for line in output.splitlines()
        if line.strip()[:1] in _BORDERS
    ]


def _text(output: str) -> str:
    """Return ``output`` with every run of whitespace turned into one space.

    The console wraps its lines at the terminal width, which puts line breaks
    into the middle of the sentences a test looks for.
    """
    return " ".join(output.split())


def _offer(
    source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    version: str,
    *,
    client_build: str = BUILD,
) -> None:
    """Publish ``version`` with a matching archive behind its download link."""
    source.offer(
        make_spt_release(version=version, client_build=client_build),
        version=version,
        client_build=client_build,
    )


def _subjects(path: Path) -> list[str]:
    """Return the commit subjects of the game repository at ``path``, newest first."""
    return [commit.subject for commit in services.history(path)]


def _invoke(args: Sequence[str], answer: str = "") -> tuple[int, str, str]:
    """Run ``sptmod`` with ``args``, answering a confirmation with ``answer``."""
    result = runner.invoke(app, list(args), input=answer)
    return result.exit_code, result.stdout, result.stderr


# --- spt list ------------------------------------------------------------


def test_list_shows_every_release_of_the_series_with_its_state(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` prints one row per release of the installed series.

    Each row names the version, the Escape from Tarkov build it needs, its
    publication date, how it stands to the installed version and whether it
    can still be downloaded.
    """
    _offer(spt_release_source, make_spt_release, OLDER)
    _offer(spt_release_source, make_spt_release, INSTALLED)
    _offer(spt_release_source, make_spt_release, NEW)
    spt_release_source.announce(NEWEST)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "list"])

    assert exit_code == 0
    assert _rows(output) == [
        ["Version", "EFT build", "Released", "State", "Download"],
        [NEWEST, BUILD, "2025-01-02 03:04", "newer", "no"],
        [NEW, BUILD, "2025-01-02 03:04", "newer", "yes"],
        [INSTALLED, BUILD, "2025-01-02 03:04", "installed", "yes"],
    ]
    assert f"SPT releases (installed: {INSTALLED})" in _text(output)
    assert f"installed Escape from Tarkov build: {BUILD}" in _text(output)


def test_list_calls_an_unreadable_version_and_build_unknown(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` says ``unknown`` where version or build cannot be read.

    Without a server executable and without a game client neither is
    readable, and every published release is offered as newer.
    """
    clean_repo_at(fake_game_dir_runtime)
    _offer(spt_release_source, make_spt_release, OLDER)
    monkeypatch.chdir(fake_game_dir_runtime)

    exit_code, output, _ = _invoke(["spt", "list"])

    assert exit_code == 0
    assert "SPT releases (installed: unknown)" in _text(output)
    assert "installed Escape from Tarkov build: unknown" in _text(output)
    assert _rows(output)[1:] == [[OLDER, BUILD, "2025-01-02 03:04", "newer", "yes"]]


def test_list_still_shows_the_installed_facts_when_the_release_source_fails(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` prints the table, the installed build and why the list is empty.

    The source answers with an error and nothing was downloaded before; the
    command still succeeds, because the installed version and the game build
    are read from the game folder and are what a player came to see, and the
    failure is said in words rather than swallowed.
    """
    spt_release_source.unavailable(503)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "list"])

    assert exit_code == 0
    assert f"SPT releases (installed: {INSTALLED})" in _text(output)
    assert f"installed Escape from Tarkov build: {BUILD}" in _text(output)
    assert "The SPT release list is not available" in _text(output)


def test_list_serves_the_stored_release_list_until_a_refresh_is_asked_for(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` reads the source once; ``--refresh`` reads it again.

    A release published after the first run shows up only once the refresh is
    asked for, because every run would otherwise spend from the sixty
    anonymous requests an hour the source allows.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "list"])
    _offer(spt_release_source, make_spt_release, NEWEST)

    stored_code, stored, _ = _invoke(["spt", "list"])
    refreshed_code, refreshed, _ = _invoke(["spt", "list", "--refresh"])

    assert (stored_code, refreshed_code) == (0, 0)
    assert [row[0] for row in _rows(stored)[1:]] == [NEW]
    assert [row[0] for row in _rows(refreshed)[1:]] == [NEWEST, NEW]
    assert spt_release_source.list_calls == 2


def test_list_shows_the_stored_release_list_when_a_refresh_fails(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list --refresh`` keeps the stored releases and says they are from earlier.

    The refusal names the hourly limit for anonymous requests, so the player
    reads that the shown list is the one downloaded before and that the
    source will answer again later.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "list"])
    spt_release_source.unavailable(
        403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1767225600"}
    )

    exit_code, output, _ = _invoke(["spt", "list", "--refresh"])

    assert exit_code == 0
    assert [row[0] for row in _rows(output)[1:]] == [NEW]
    assert "hourly limit for anonymous requests" in _text(output)
    assert "downloaded on" in _text(output)


def test_update_refuses_while_there_is_no_release_list_at_all(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` exits 18 when nothing can say whether the version exists.

    The source fails and nothing was downloaded before, so no release can be
    resolved; the change is refused loudly instead of being attempted against
    an empty list.
    """
    spt_release_source.unavailable(503)
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == RELEASE_UNAVAILABLE
    assert _text(errors).startswith("error: The SPT release list is not available")


def test_update_refuses_when_the_series_has_no_published_release(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` says so when the source answered but published nothing usable.

    The source is readable and publishes only another SPT series, so the list
    is empty without anything having failed; the refusal names that instead
    of an empty reason, because there is no failure to report here.
    """
    _offer(spt_release_source, make_spt_release, "4.2.0")
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == RELEASE_UNAVAILABLE
    assert _text(errors).startswith(
        "error: There are no SPT releases this installation can change to."
    )


def test_update_to_a_version_the_stored_listing_lacks_names_the_failed_source(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` reports the unreadable source for a version the stored listing does not hold.

    The stored listing holds 4.1.1 only, so the wanted 4.1.2 is looked for at
    the source, which refuses; the refusal names that failure and the day the
    listing was downloaded rather than claiming 4.1.2 does not exist, and the
    presence of a stored listing does not turn the change away by itself.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "list"])
    spt_release_source.unavailable(503)

    exit_code, _, errors = _invoke(["spt", "update", NEWEST, "--yes"])

    assert exit_code == RELEASE_UNAVAILABLE
    assert "not available right now" in _text(errors)
    assert "downloaded on" in _text(errors)


def test_update_uses_the_stored_release_list_while_the_source_fails(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` changes the version from the stored list without reading the source.

    The release was listed before the source started failing, so its download
    link is known from the stored list; the change goes through and asks the
    source nothing, which is what keeps a version change possible once the
    request budget of the hour is spent.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "list"])
    spt_release_source.unavailable(503)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == 0
    assert f"SPT is now {NEW}" in _text(output)
    assert spt_release_source.list_calls == 1


# --- spt update ----------------------------------------------------------


def test_update_changes_the_version_after_the_confirmation(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` asks first, then puts the release in place.

    The question names both versions and promises that profiles, settings and
    mods survive; afterwards the new version, the number of files and the
    hint to check the installed mods are printed.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW], answer="y\n")

    assert exit_code == 0
    assert f"change SPT from {INSTALLED} to {NEW}?" in _text(output)
    assert f"SPT is now {NEW} (9 files)" in _text(output)
    assert "'sptmod update --check'" in _text(output)
    assert _subjects(spt_game_dir) == [f"spt: {NEW}", "chore: clean SPT 4.1.0"]


def test_update_without_asking_when_yes_is_given(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update --yes`` changes the version without a question.

    Nothing is asked even though no answer is available on the input, and the
    change happens all the same.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == 0
    assert "change SPT from" not in _text(output)
    assert _subjects(spt_game_dir)[0] == f"spt: {NEW}"


@pytest.mark.parametrize("answer", ["n\n", ""], ids=["answered-no", "no-input-at-all"])
def test_update_changes_nothing_unless_the_change_is_confirmed(
    answer: str,
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` without ``--yes`` leaves the installation alone unless confirmed.

    Offers a newer release and runs ``spt update`` once answering "n" and once with no
    input at all, as a command without a terminal has; asserts both end aborted with exit
    code 1, leave the commit history at the untouched baseline and write no file of the
    release. Reason: a missing answer must count as a refusal, so a scripted run never
    swaps the SPT installation by accident.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, errors = _invoke(["spt", "update", NEW], answer=answer)

    assert exit_code == 1
    assert f"change SPT from {INSTALLED} to {NEW}?" in _text(output)
    assert "Aborted" in _text(errors)
    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]
    assert not (spt_game_dir / "winhttp.dll").exists()


def test_update_reports_a_release_that_is_already_in_place(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` says nothing changed when the release is already there.

    The game directory holds exactly this release, but its server executable
    carries no readable version, so the change is not refused as pointless --
    it simply finds nothing to do.
    """
    members = spt_release_members(NEW, BUILD) | {
        "SPT_Runtime/SPT.Server.exe": b"no version resource in here"
    }
    for name, content in members.items():
        if name.startswith("SPT_Runtime/user/"):
            continue
        target = fake_game_dir_runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            content.encode("utf-8") if isinstance(content, str) else content
        )
    write_client_exe(fake_game_dir_runtime)
    clean_repo_at(fake_game_dir_runtime)
    spt_release_source.offer(
        make_archive(members, "zip"), version=NEW, client_build=BUILD
    )
    monkeypatch.chdir(fake_game_dir_runtime)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == 0
    assert f"SPT {NEW} is already in place, nothing changed" in _text(output)
    assert _subjects(fake_game_dir_runtime) == ["chore: clean SPT 4.1.0"]


@pytest.mark.parametrize(
    ("command", "version", "expected"),
    [
        ("update", OLDER, "is not newer than the installed 4.1.0"),
        ("update", INSTALLED, "is not newer than the installed 4.1.0"),
        ("downgrade", NEW, "is not older than the installed 4.1.0"),
        ("downgrade", INSTALLED, "is not older than the installed 4.1.0"),
    ],
    ids=[
        "update-to-older",
        "update-to-installed",
        "downgrade-to-newer",
        "downgrade-to-installed",
    ],
)
def test_a_version_change_in_the_wrong_direction_is_refused(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    version: str,
    expected: str,
) -> None:
    """``update`` only goes up and ``downgrade`` only goes down.

    A version on the wrong side of the installed one is a usage error, exit
    code 2, and the message names the command that would do it.
    """
    for published in (OLDER, INSTALLED, NEW):
        _offer(spt_release_source, make_spt_release, published)
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", command, version, "--yes"])

    assert exit_code == BAD_PARAMETER
    assert expected in _text(errors)
    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_update_to_a_version_that_is_not_published_exits_seventeen(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` exits 17 for a version the release source does not have."""
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", "update", "4.1.9", "--yes"])

    assert exit_code == RELEASE_UNAVAILABLE
    assert _text(errors).startswith("error: There is no published SPT 4.1.9.")


def test_update_to_a_release_for_another_game_build_exits_eighteen(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` exits 18 when the release needs another game build.

    The message names both builds and the installer that changes the game
    itself, and nothing is written.
    """
    _offer(spt_release_source, make_spt_release, NEW, client_build=OTHER_BUILD)
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == BUILD_MISMATCH
    assert f"needs Escape from Tarkov build {OTHER_BUILD}" in _text(errors)
    assert f"your game is build {BUILD}" in _text(errors)
    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_update_that_cannot_write_a_game_file_exits_nineteen(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` exits 19 and says in plain words which file stopped it.

    The core configuration of the installation is made read-only, so writing
    the release stops there. The user has to read the file, the reason and
    where the previous files are, and no commit may be made.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    blocked = spt_game_dir / "SPT_Runtime/SPT_Data/configs/core.json"
    blocked.chmod(0o444)

    try:
        exit_code, _, errors = _invoke(["spt", "update", NEW, "--yes"])
    finally:
        blocked.chmod(0o644)

    assert exit_code == WRITE_FAILED
    assert _text(errors) == (
        "error: The game files could not be written: Permission denied. The "
        "update stopped at SPT_Runtime/SPT_Data/configs/core.json; your "
        "previous files are one snapshot back."
    )
    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


# --- spt downgrade -------------------------------------------------------


def test_downgrade_without_a_version_undoes_the_last_change(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt downgrade`` without a version goes back to the version before.

    The question speaks of undoing the last change, and the installation
    reports the old version again afterwards.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "update", NEW, "--yes"])

    exit_code, output, _ = _invoke(["spt", "downgrade"], answer="y\n")

    assert exit_code == 0
    assert "Undo the last SPT change?" in _text(output)
    assert f"SPT is now {INSTALLED}" in _text(output)
    assert _subjects(spt_game_dir)[0] == f'Revert "spt: {NEW}"'
    assert not (spt_game_dir / "winhttp.dll").exists()


def test_downgrade_without_anything_to_undo_exits_ten(
    spt_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``spt downgrade`` exits 10 when no version change was ever made."""
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", "downgrade", "--yes"])

    assert exit_code == GIT_FAILED
    assert _text(errors).startswith("error: There is no SPT version change to undo.")


def test_downgrade_to_a_named_version_puts_that_release_in_place(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt downgrade <version>`` writes that older release, not the last one.

    Two updates happened; the named version is a step below the newest one,
    and going back to it writes it as its own change instead of undoing.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    _offer(spt_release_source, make_spt_release, NEWEST)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "update", NEWEST, "--yes"])

    exit_code, output, _ = _invoke(["spt", "downgrade", NEW, "--yes"])

    assert exit_code == 0
    assert f"SPT is now {NEW}" in _text(output)
    assert _subjects(spt_game_dir) == [
        f"spt: {NEW}",
        f"spt: {NEWEST}",
        "chore: clean SPT 4.1.0",
    ]
    assert (spt_game_dir / "winhttp.dll").read_text(encoding="utf-8") == (
        f"doorstop of {NEW}"
    )


def test_a_version_change_reports_its_progress_while_it_runs(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` prints the steps of the change as they happen.

    The percentage and the step name of every report reach the user, so a
    download of hundreds of megabytes does not look like a hang.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == 0
    assert f"0% check: SPT {NEW}: downloading the release" in _text(output)
    assert f"100% commit: SPT {NEW}: SPT {NEW} is in place" in _text(output)


def test_the_installed_version_survives_an_update_of_the_spt_server(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` reports the new version after ``spt update`` changed it.

    The two commands see the same installation, so the version the update
    wrote is the one the list marks as installed afterwards.
    """
    _offer(spt_release_source, make_spt_release, INSTALLED)
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "update", NEW, "--yes"])

    exit_code, output, _ = _invoke(["spt", "list"])

    assert exit_code == 0
    assert f"SPT releases (installed: {NEW})" in _text(output)
    assert [row[3] for row in _rows(output)[1:]] == ["installed", "older"]


def test_downgrade_keeps_the_version_when_the_undo_is_declined(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt downgrade`` answered with ``n`` leaves the last change in place.

    The question about undoing is asked before anything is touched, so a "no"
    ends the command as aborted with the applied release still installed.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "update", NEW, "--yes"])

    exit_code, output, errors = _invoke(["spt", "downgrade"], answer="n\n")

    assert exit_code == 1
    assert "Undo the last SPT change?" in _text(output)
    assert "Aborted" in _text(errors)
    assert _subjects(spt_game_dir) == [f"spt: {NEW}", "chore: clean SPT 4.1.0"]
    assert (spt_game_dir / "winhttp.dll").read_text(encoding="utf-8") == (
        f"doorstop of {NEW}"
    )


def test_update_reaches_a_release_of_the_source_that_replaced_the_archived_one(
    fake_game_dir_runtime: Path,
    spt_release_sources: tuple[SptReleaseSource, ...],
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` and ``spt update`` reach a release only the current repository publishes.

    Nothing names a release source, so the CLI reads both defaults: the
    archived repository stopped at the installed 4.1.2 and the current one
    publishes 4.1.5. The table offers 4.1.5 as newer and the update puts it in
    place, which is the whole point of reading two sources -- a player whose
    SPT came from the archived repository would otherwise never be offered
    anything again.
    """
    current, *archived = spt_release_sources
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", NEWEST)
    write_client_exe(fake_game_dir_runtime)
    clean_repo_at(fake_game_dir_runtime)
    _offer(current, make_spt_release, CONTINUED)
    for source in archived:
        _offer(source, make_spt_release, NEWEST)
    monkeypatch.chdir(fake_game_dir_runtime)

    listed_code, listed_output, _ = _invoke(["spt", "list"])
    exit_code, output, _ = _invoke(["spt", "update", CONTINUED, "--yes"])

    assert (listed_code, exit_code) == (0, 0)
    assert _rows(listed_output) == [
        ["Version", "EFT build", "Released", "State", "Download"],
        [CONTINUED, BUILD, "2025-01-02 03:04", "newer", "yes"],
        [NEWEST, BUILD, "2025-01-02 03:04", "installed", "yes"],
    ]
    assert f"SPT is now {CONTINUED}" in _text(output)
    assert _subjects(fake_game_dir_runtime)[0] == f"spt: {CONTINUED}"
