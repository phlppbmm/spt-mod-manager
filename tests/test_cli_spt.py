"""The ``sptmod spt`` commands: ``list``, ``update`` and ``downgrade``.

Covers the release table with its state and download columns, the installed
client build, the confirmation, its ``--yes`` bypass and the refusals a "no"
and a missing answer produce, the refusal of a
version change in the wrong direction, the exit codes of an unpublished
release, a mismatching game build and a missing version change to undo, the
hint printed after a successful change, and both forms of going back.
"""

import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import (
    SptReleaseSource,
    clean_repo_at,
    spt_release_members,
    write_client_exe,
)
from fixture_types import MakeArchive, MakeSptRelease
from sptmod.cli import app
from sptmod.core import services

runner = CliRunner()

INSTALLED = "4.1.0"
NEW = "4.1.1"
NEWEST = "4.1.2"
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


def test_list_reports_an_unreachable_release_source(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt list`` exits 17 and says so when the release list cannot be read."""
    spt_release_source.unavailable(503)
    monkeypatch.chdir(spt_game_dir)

    exit_code, _, errors = _invoke(["spt", "list"])

    assert exit_code == RELEASE_UNAVAILABLE
    assert _text(errors).startswith("error: The SPT release list is not available")


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


def test_update_changes_nothing_when_the_confirmation_is_declined(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` answered with ``n`` leaves the installation alone.

    The command ends as aborted, no commit is written and no file of the
    release reaches the game directory.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, errors = _invoke(["spt", "update", NEW], answer="n\n")

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


def test_update_changes_nothing_when_no_answer_can_be_read(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spt update`` without ``--yes`` and without any input refuses the change.

    A command run without a terminal cannot answer the question; the missing
    answer counts as a refusal, so the command ends as aborted and neither a
    commit nor a file of the release reaches the game directory.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, errors = _invoke(["spt", "update", NEW])

    assert exit_code == 1
    assert f"change SPT from {INSTALLED} to {NEW}?" in _text(output)
    assert "Aborted" in _text(errors)
    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]
    assert not (spt_game_dir / "winhttp.dll").exists()


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
