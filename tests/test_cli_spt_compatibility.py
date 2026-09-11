"""``sptmod spt update`` and ``spt downgrade`` faced with unfitting mods.

Covers what both directions of a version change do when the local mod list
holds no version of an installed mod for the wanted SPT release: the verdict
printed above the confirmation, the refusal with its exit code, the mods it
names and the override it offers, the change that goes through once that
override is given, the confirmation that is still asked for, the release the
verdict is reported for when a short version was typed, and the notice
printed instead of a verdict when the mod list cannot answer at all.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fixture_types import MakeSptRelease, TrailerInstallCommit
from fixtures_forge import seed_catalog
from fixtures_spt import INSTALLED, NEW, NEWEST, SptReleaseSource, offer
from forge_json import mod_json_with_versions
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.repo import Repo

runner = CliRunner()

# ``cli.EXIT_CODES`` maps a change refused over the installed mods to this
# code; asserted only here.
MODS_INCOMPATIBLE = 22

# Constraints as the Forge publishes them, one per SPT series.
FOR_NEW_SERIES = "~4.1.0"
FOR_OLD_SERIES = "~4.0.0"
# Covers 4.1.2 and up, so a mod carrying it fits the newest release of the
# series but not the one before it.
ONLY_NEWEST = "~4.1.2"
# Covers 4.1.1 and up, so a mod carrying it fits the new release but not the
# installed one.
ONLY_NEW = "~4.1.1"

# The SPT series without its last component; naming it means the release the
# release list publishes under it.
SERIES = "4.1"
# A version of the installed series the release source does not publish.
UNPUBLISHED = "4.1.5"


def _text(output: str) -> str:
    """Return ``output`` with every run of whitespace turned into one space.

    The console wraps its lines at the terminal width, which puts line breaks
    into the middle of the sentences a test looks for.
    """
    return " ".join(output.split())


def _invoke(args: Sequence[str], answer: str = "") -> tuple[int, str, str]:
    """Run ``sptmod`` with ``args``, answering a confirmation with ``answer``."""
    result = runner.invoke(app, list(args), input=answer)
    return result.exit_code, result.stdout, result.stderr


def _subjects(path: Path) -> list[str]:
    """Return the commit subjects of the game repository at ``path``, newest first."""
    return [commit.subject for commit in services.history(path)]


@pytest.mark.parametrize(
    ("command", "start_at", "constraint"),
    [
        pytest.param("update", INSTALLED, FOR_OLD_SERIES, id="update"),
        pytest.param("downgrade", NEWEST, ONLY_NEWEST, id="downgrade"),
    ],
)
def test_a_version_change_stops_at_a_mod_without_a_version_for_it(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    start_at: str,
    constraint: str,
) -> None:
    """Both directions refuse with exit 22, naming the mod and the override.

    Installs a mod the downloaded mod list holds no version of for release
    4.1.1, and asks for that release once from below and once from above.
    Asserts the exit code, the message naming the mod and
    ``--allow-incompatible-mods``, the version that stayed in place and that
    the archive was never fetched. Reason: a player must learn which of their
    mods will not work *before* the download runs and the installation
    changes, and must be told how to go ahead regardless -- and going back is
    as capable of breaking mods as going forward.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    download = offer(spt_release_source, make_spt_release, NEW)
    offer(spt_release_source, make_spt_release, NEWEST)
    monkeypatch.chdir(spt_game_dir)
    if start_at != INSTALLED:
        _invoke(["spt", "update", start_at, "--yes"])
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=801, name="OldMod", constraints=[constraint])],
    )

    exit_code, _, errors = _invoke(["spt", command, NEW, "--yes"])

    assert exit_code == MODS_INCOMPATIBLE
    assert _text(errors) == (
        f"error: these installed mods have no version for SPT {NEW}: "
        f"OldMod. Update or remove them first, or change the SPT version "
        f"anyway. Repeat the command with --allow-incompatible-mods to do that."
    )
    assert services.list_spt_releases(spt_game_dir).current_version == start_at
    assert download.call_count == 0


def test_the_override_carries_the_change_past_the_unfitting_mods(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--allow-incompatible-mods`` changes the version anyway.

    Repeats the refused update with the override and asserts the new version
    is reported as in place and committed. Reason: the check advises, it does
    not decide -- a player who knows their mods must have a way through.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=811, name="OldMod", constraints=[FOR_OLD_SERIES])],
    )
    offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes", "--allow-incompatible-mods"])

    assert exit_code == 0
    assert f"SPT is now {NEW}" in _text(output)
    assert _subjects(spt_game_dir)[0] == f"spt: {NEW}"


def test_the_override_does_not_answer_the_confirmation(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowing the unfitting mods still leaves the question to be answered.

    Runs the update with ``--allow-incompatible-mods`` but without ``--yes``
    and answers the question with "no". Asserts the command ends aborted and
    the installed version stayed. Reason: the two options mean different
    things -- one accepts the state of the mods, the other skips the question
    -- and accepting the mods must never be read as a yes to the change.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=821, name="OldMod", constraints=[FOR_OLD_SERIES])],
    )
    download = offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, errors = _invoke(["spt", "update", NEW, "--allow-incompatible-mods"], answer="n\n")

    assert exit_code == 1
    assert f"change SPT from {INSTALLED} to {NEW}?" in _text(output)
    assert "Aborted" in _text(errors)
    assert download.call_count == 0
    assert services.list_spt_releases(spt_game_dir).current_version == INSTALLED


@pytest.mark.parametrize(
    "unreadable",
    [
        pytest.param(False, id="never-downloaded"),
        pytest.param(True, id="cannot-be-opened"),
    ],
)
def test_the_change_says_when_the_installed_mods_could_not_be_checked(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unreadable: bool,
) -> None:
    """A mod list that cannot answer is said so before the question is asked.

    Leaves the mod list undownloaded, or puts a file where its folder belongs
    so it cannot be opened, and starts an update. Asserts the printed notice
    names the reason -- and the download it advises for the first case -- and
    that the question about the change is still asked afterwards. Reason: an
    unchecked installation must not look like a checked and clean one, and the
    player has to be able to act on the reason.
    """
    install_commit(Repo(spt_game_dir), "Solo")
    if unreadable:
        isolated_default_db_path.parent.parent.mkdir(parents=True, exist_ok=True)
        isolated_default_db_path.parent.write_text("not a folder", encoding="utf-8")
    offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW], answer="n\n")

    expected = (
        "The mod list on this computer could not be opened, so your installed mods could not be checked."
        if unreadable
        else "The mod list has not been downloaded yet, so your installed mods "
        "could not be checked. Download it first to have them checked."
    )
    assert exit_code == 1
    assert expected in _text(output)
    assert f"change SPT from {INSTALLED} to {NEW}?" in _text(output)


def test_a_checked_installation_is_changed_without_a_notice(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A downloaded mod list that clears the mods says nothing and changes on.

    Installs a mod the mod list holds a version of for the wanted release and
    updates. Asserts the change happened and that neither notice nor verdict
    was printed. Reason: the notices exist for the cases where nothing could
    be judged and the verdict for the mods that stand against the change; a
    clean check must not add noise to a plain update.
    """
    install_commit(Repo(spt_game_dir), "GoodMod")
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=831, name="GoodMod", constraints=[FOR_NEW_SERIES])],
    )
    offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes"])

    assert exit_code == 0
    assert "could not be checked" not in _text(output)
    assert "have no version for" not in _text(output)
    assert f"SPT is now {NEW}" in _text(output)


def test_the_unfitting_mods_are_named_before_the_question_is_asked(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict stands above the confirmation, and "no" still stops there.

    Installs a mod the downloaded mod list holds no version of for the wanted
    release and starts an update without ``--yes``, answering the question
    with "no". Asserts the mod is named in the output above the question,
    that the command ends aborted, that nothing was downloaded and that the
    installed version stayed. Reason: a player is asked to decide, so the
    decision has to be made with the list of mods that will break already on
    screen -- a verdict shown afterwards, or not at all, decides nothing.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=841, name="OldMod", constraints=[FOR_OLD_SERIES])],
    )
    download = offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, errors = _invoke(["spt", "update", NEW], answer="n\n")

    text = _text(output)
    verdict = f"these installed mods have no version for SPT {NEW}: OldMod"
    question = f"change SPT from {INSTALLED} to {NEW}?"
    assert verdict in text
    assert question in text
    assert text.index(verdict) < text.index(question)
    assert exit_code == 1
    assert "Aborted" in _text(errors)
    assert download.call_count == 0
    assert services.list_spt_releases(spt_game_dir).current_version == INSTALLED


@pytest.mark.parametrize(
    ("installed", "expected", "absent"),
    [
        pytest.param(
            ["OldMod"],
            [f"these installed mods have no version for SPT {NEW}: OldMod"],
            ["could not be checked against"],
            id="only-unfitting",
        ),
        pytest.param(
            ["Unlisted"],
            [f"these installed mods could not be checked against SPT {NEW}: Unlisted"],
            ["have no version for"],
            id="only-unjudgeable",
        ),
        pytest.param(
            ["OldMod", "Unlisted"],
            [
                f"these installed mods have no version for SPT {NEW}: OldMod",
                f"these installed mods could not be checked against SPT {NEW}: Unlisted",
            ],
            [],
            id="unfitting-and-unjudgeable",
        ),
    ],
)
def test_the_verdict_names_the_unfitting_and_the_unjudgeable_mods(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed: list[str],
    expected: list[str],
    absent: list[str],
) -> None:
    """Both halves of the verdict are printed, the unfitting ones first.

    Installs, against a mod list that holds one entry without a version for
    the wanted release, a mod that entry judges, a mod it has never heard of,
    or both at once, and lets the change go through with the override.
    Asserts the expected sentences appear in that order and that the sentence
    of the other half is absent where it does not apply. Reason: the two
    groups mean different things -- one names mods that will break, the other
    mods nobody can vouch for -- so neither may be dropped, neither may be
    invented, and the one that costs the player something comes first.
    """
    repo = Repo(spt_game_dir)
    for name in installed:
        install_commit(repo, name)
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=851, name="OldMod", constraints=[FOR_OLD_SERIES])],
    )
    offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, _ = _invoke(["spt", "update", NEW, "--yes", "--allow-incompatible-mods"])

    text = _text(output)
    found = [text.find(sentence) for sentence in expected]
    assert -1 not in found
    assert found == sorted(found)
    assert [sentence for sentence in absent if sentence in text] == []
    assert exit_code == 0
    assert f"SPT is now {NEW}" in text


def test_the_verdict_is_reported_for_the_release_a_short_version_names(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version typed without its last component is judged as the release it is.

    Moves the game directory to 4.1.1, installs a mod the mod list holds a
    version of for 4.1.1 but none for 4.1.0, and asks to go back to "4.1".
    Asserts the printed verdict and the refusal name the published 4.1.0
    rather than the typed "4.1". Reason: the mods are judged against the
    release that will actually be put in place, so a player typing the series
    must not be told about a version nobody publishes.
    """
    install_commit(Repo(spt_game_dir), "NewOnly")
    offer(spt_release_source, make_spt_release, INSTALLED)
    offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)
    _invoke(["spt", "update", NEW, "--yes"])
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=861, name="NewOnly", constraints=[ONLY_NEW])],
    )

    exit_code, output, errors = _invoke(["spt", "downgrade", SERIES, "--yes"])

    text = _text(output)
    assert f"these installed mods have no version for SPT {INSTALLED}: NewOnly" in text
    assert f"for SPT {SERIES}:" not in text
    assert f"no version for SPT {INSTALLED}: NewOnly" in _text(errors)
    assert exit_code == MODS_INCOMPATIBLE


def test_the_verdict_keeps_a_version_the_release_list_does_not_hold(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    isolated_default_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version nobody publishes is reported under the name that was typed.

    Installs a mod without a version for anything in the 4.1 series and asks
    for a release the source does not list. Asserts the verdict names the
    typed version and that the change is then refused as an unpublished
    release. Reason: with no published release to name, the wanted version is
    the only thing the verdict can honestly speak of, and it must still be
    spoken rather than swallowed.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    seed_catalog(
        isolated_default_db_path,
        [mod_json_with_versions(mod_id=871, name="OldMod", constraints=[FOR_OLD_SERIES])],
    )
    download = offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.chdir(spt_game_dir)

    exit_code, output, errors = _invoke(["spt", "update", UNPUBLISHED, "--yes"])

    assert f"these installed mods have no version for SPT {UNPUBLISHED}: OldMod" in _text(output)
    assert f"There is no published SPT {UNPUBLISHED}." in _text(errors)
    assert exit_code != 0
    assert download.call_count == 0
    assert services.list_spt_releases(spt_game_dir).current_version == INSTALLED
