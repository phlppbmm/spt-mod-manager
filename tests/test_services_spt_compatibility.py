"""How installed mods are judged against an SPT version, and what that refuses.

Covers ``check_spt_compatibility`` -- the three verdicts it sorts the installed
mods into, the Forge ID before name matching, the empty SPT constraint that
counts as no version for the wanted release, and the two ways the local mod
list can fail to answer -- together with the guard ``apply_spt_release`` puts
in front of a version change: which mods refuse it, that the override goes
through anyway, that nothing is downloaded when it refuses, and that the older
refusals of a version change keep their precedence and their wording.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import forge_trailers
from fixture_types import MakeSptRelease, TrailerInstallCommit
from fixtures_forge import seed_catalog
from fixtures_spt import (
    INSTALLED,
    NEW,
    NEWEST,
    OTHER_SERIES,
    SptReleaseSource,
    offer,
    release_subjects,
)
from forge_json import mod_json_with_versions
from sptmod.core import services
from sptmod.core.errors import SptModsIncompatibleError, SptReleaseUnavailableError
from sptmod.core.repo import Repo

# Constraints as the Forge publishes them: one covering the 4.1 series the
# game directory is on, one covering only the series before it.
FOR_NEW_SERIES = "~4.1.0"
FOR_OLD_SERIES = "~4.0.0"

# The baseline the ``spt_game_dir`` fixture commits; every history assertion
# starts from it.
BASELINE = "chore: clean SPT 4.1.0"

# What the report says when the local mod list cannot answer at all.
NEVER_DOWNLOADED_NOTICE = (
    "The mod list has not been downloaded yet, so your installed mods could "
    "not be checked. Download it first to have them checked."
)
UNREADABLE_NOTICE = (
    "The mod list on this computer could not be opened, so your installed "
    "mods could not be checked."
)


def _never_synced(tmp_path: Path) -> Path:
    """A catalog path nobody has ever synced: the file does not exist yet."""
    return tmp_path / "never-synced.db"


def _unopenable(tmp_path: Path) -> Path:
    """A catalog path that cannot be opened: a file sits where its folder goes."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    return blocked / "forge-catalog.db"


# --- check_spt_compatibility ---------------------------------------------


def test_check_sorts_the_installed_mods_into_the_three_verdicts(
    spt_game_dir: Path, install_commit: TrailerInstallCommit, db_path: Path
) -> None:
    """Every installed mod lands in exactly one list of the report.

    Installs four mods -- one linked to a Forge entry that publishes a version
    for the wanted release under another display name, one the list knows by
    name and holds no version for, one the list knows without holding any
    version of it, and one the list has never heard of -- against a downloaded
    mod list. Asserts the compatible, the incompatible and the skipped names,
    that the report is marked as checked and that it carries no notice.
    Reason: the three verdicts have different consequences, so a mod may never
    be counted as fitting or as unfitting when it is merely unknown.
    """
    repo = Repo(spt_game_dir)
    install_commit(repo, "Alpha", **forge_trailers("501"))
    install_commit(repo, "Beta")
    install_commit(repo, "Gamma")
    install_commit(repo, "Delta")
    seed_catalog(
        db_path,
        [
            mod_json_with_versions(
                mod_id=501,
                name="Alpha On The Forge",
                constraints=[FOR_OLD_SERIES, FOR_NEW_SERIES],
            ),
            mod_json_with_versions(
                mod_id=502, name="Beta", constraints=[FOR_OLD_SERIES]
            ),
            mod_json_with_versions(mod_id=503, name="Gamma", constraints=[]),
        ],
    )

    report = services.check_spt_compatibility(spt_game_dir, NEW, db_path=db_path)

    assert report.spt_version == NEW
    assert report.compatible == ["Alpha"]
    assert report.incompatible == ["Beta"]
    assert report.skipped == ["Delta", "Gamma"]
    assert report.checked
    assert report.notice == ""


@pytest.mark.parametrize(
    ("constraints", "compatible", "incompatible", "skipped"),
    [
        pytest.param([FOR_NEW_SERIES], ["Solo"], [], [], id="version-for-the-target"),
        pytest.param([FOR_OLD_SERIES], [], ["Solo"], [], id="only-another-series"),
        pytest.param([""], [], ["Solo"], [], id="no-spt-version-declared"),
        pytest.param(["nonsense range"], [], ["Solo"], [], id="unreadable-constraint"),
        pytest.param([], [], [], ["Solo"], id="no-version-in-the-list"),
    ],
)
def test_check_judges_a_mod_by_the_constraints_its_versions_declare(
    spt_game_dir: Path,
    install_commit: TrailerInstallCommit,
    db_path: Path,
    constraints: list[str],
    compatible: list[str],
    incompatible: list[str],
    skipped: list[str],
) -> None:
    """One mod, one verdict, taken from what its published versions declare.

    Installs a single mod and gives the mod list the constraints of the case,
    then asserts which of the three lists holds it. Reason: a version that
    names no SPT version, or one nobody can read, says nothing about the
    wanted release and may not pass as a fit, while an entry without any
    version at all is not a verdict but a gap in the list.
    """
    install_commit(Repo(spt_game_dir), "Solo")
    seed_catalog(
        db_path,
        [mod_json_with_versions(mod_id=601, name="Solo", constraints=constraints)],
    )

    report = services.check_spt_compatibility(spt_game_dir, NEW, db_path=db_path)

    assert report.compatible == compatible
    assert report.incompatible == incompatible
    assert report.skipped == skipped


def test_check_matches_by_forge_id_before_the_name(
    spt_game_dir: Path, install_commit: TrailerInstallCommit, db_path: Path
) -> None:
    """A mod linked to Forge is judged by that entry, not by its name.

    Installs a mod named ``Twin`` linked to Forge entry 611, while the mod
    list holds both that entry -- renamed there, with a version for the wanted
    release -- and another entry that carries the name ``Twin`` and has no
    version for it. Asserts the mod counts as compatible. Reason: display
    names are not identities on the Forge, so a mod that merely shares a name
    with an unfitting one must not block a version change.
    """
    install_commit(Repo(spt_game_dir), "Twin", **forge_trailers("611"))
    seed_catalog(
        db_path,
        [
            mod_json_with_versions(
                mod_id=611, name="Renamed Twin", constraints=[FOR_NEW_SERIES]
            ),
            mod_json_with_versions(
                mod_id=612, name="Twin", constraints=[FOR_OLD_SERIES]
            ),
        ],
    )

    report = services.check_spt_compatibility(spt_game_dir, NEW, db_path=db_path)

    assert report.compatible == ["Twin"]
    assert not report.incompatible


def test_check_falls_back_to_the_name_when_the_forge_id_is_unknown(
    spt_game_dir: Path, install_commit: TrailerInstallCommit, db_path: Path
) -> None:
    """A Forge ID the mod list does not hold still leaves the name to match on.

    Installs a mod linked to Forge entry 9999, which the list does not hold,
    while it does hold an entry of the same name without a version for the
    wanted release. Asserts the mod is reported as incompatible rather than
    skipped. Reason: a cache that is behind on one entry must not turn a known
    mismatch into an unanswered question.
    """
    install_commit(Repo(spt_game_dir), "Solo", **forge_trailers("9999"))
    seed_catalog(
        db_path,
        [
            mod_json_with_versions(
                mod_id=621, name="Solo", constraints=[FOR_OLD_SERIES]
            )
        ],
    )

    report = services.check_spt_compatibility(spt_game_dir, NEW, db_path=db_path)

    assert report.incompatible == ["Solo"]
    assert not report.skipped


@pytest.mark.parametrize(
    ("make_db_path", "notice"),
    [
        pytest.param(_never_synced, NEVER_DOWNLOADED_NOTICE, id="never-downloaded"),
        pytest.param(_unopenable, UNREADABLE_NOTICE, id="cannot-be-opened"),
    ],
)
def test_check_says_in_plain_words_when_the_mod_list_cannot_answer(
    spt_game_dir: Path,
    install_commit: TrailerInstallCommit,
    tmp_path: Path,
    make_db_path: Callable[[Path], Path],
    notice: str,
) -> None:
    """A mod list that was never downloaded, or cannot be opened, is no verdict.

    Installs a mod and points the check at a mod list that either has never
    been synced or cannot be opened at all. Asserts the report is marked as
    not checked, names the wanted version, carries the notice in plain words
    and leaves all three lists empty. Reason: an empty result would read as a
    clean bill of health, and the installed mod must not be passed off as
    judged -- not as fitting, not as unfitting and not as unknown.
    """
    install_commit(Repo(spt_game_dir), "Solo")

    report = services.check_spt_compatibility(
        spt_game_dir, NEW, db_path=make_db_path(tmp_path)
    )

    assert not report.checked
    assert report.notice == notice
    assert report.spt_version == NEW
    assert (report.compatible, report.incompatible, report.skipped) == ([], [], [])


# --- apply_spt_release: the mod guard ------------------------------------


def test_apply_refuses_the_change_and_names_the_mods_without_a_version(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    db_path: Path,
) -> None:
    """A version change stops while installed mods have no version for it.

    Installs two mods the mod list holds versions of, none of them for the
    wanted release, and asks for that release. Asserts the error names both
    mods and the wanted version without naming any command-line option,
    carries the mods as data, that the archive was never fetched and that the
    game directory is untouched. Reason: the point of the check is to spare
    the player a half-broken installation, the refusal has to cost neither
    bandwidth nor a rollback, and the message is shown by every transport, so
    it may not send a desktop user to a flag only the command line has.
    """
    repo = Repo(spt_game_dir)
    install_commit(repo, "AncientMod")
    install_commit(repo, "OldMod")
    seed_catalog(
        db_path,
        [
            mod_json_with_versions(
                mod_id=701, name="AncientMod", constraints=[FOR_OLD_SERIES]
            ),
            mod_json_with_versions(
                mod_id=702, name="OldMod", constraints=[FOR_OLD_SERIES]
            ),
        ],
    )
    download = offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(SptModsIncompatibleError) as error:
        services.apply_spt_release(spt_game_dir, NEW, db_path=db_path)

    assert str(error.value) == (
        f"these installed mods have no version for SPT {NEW}: OldMod, "
        f"AncientMod. Update or remove them first, or change the SPT version "
        f"anyway."
    )
    assert "--allow-incompatible-mods" not in str(error.value)
    assert error.value.spt_version == NEW
    assert error.value.mods == ["OldMod", "AncientMod"]
    assert download.call_count == 0
    assert release_subjects(spt_game_dir)[-1] == BASELINE
    assert services.list_spt_releases(spt_game_dir).current_version == INSTALLED


def test_apply_changes_the_version_when_the_unfitting_mods_are_allowed(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    db_path: Path,
) -> None:
    """``allow_incompatible_mods`` carries the change past the refusal.

    Installs a mod without a version for the wanted release and applies that
    release with the override set. Asserts the new version is in place, the
    change is committed and the archive was fetched. Reason: the check advises
    the player, it does not overrule them -- someone who knows their mods must
    be able to go ahead.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    seed_catalog(
        db_path,
        [
            mod_json_with_versions(
                mod_id=711, name="OldMod", constraints=[FOR_OLD_SERIES]
            )
        ],
    )
    download = offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(
        spt_game_dir, NEW, db_path=db_path, allow_incompatible_mods=True
    )

    assert (result.previous_version, result.version) == (INSTALLED, NEW)
    assert release_subjects(spt_game_dir)[0] == f"spt: {NEW}"
    assert download.call_count == 1


@pytest.mark.parametrize(
    "constraints",
    [
        pytest.param([], id="known-without-any-version"),
        pytest.param([FOR_NEW_SERIES], id="version-for-the-target"),
    ],
)
def test_apply_goes_through_when_no_mod_is_left_without_a_version(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    db_path: Path,
    constraints: list[str],
) -> None:
    """Only a mod with versions but none for the target refuses a change.

    Installs one mod the downloaded mod list cannot judge and one it judges as
    fitting, and applies the release. Asserts the change happened. Reason: a
    mod nobody has catalogued yet is a gap in the list, not a verdict against
    the player's installation, so it may never stand in the way.
    """
    repo = Repo(spt_game_dir)
    install_commit(repo, "Unlisted")
    install_commit(repo, "Listed")
    seed_catalog(
        db_path,
        [mod_json_with_versions(mod_id=721, name="Listed", constraints=constraints)],
    )
    offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(spt_game_dir, NEW, db_path=db_path)

    assert result.version == NEW
    assert release_subjects(spt_game_dir)[0] == f"spt: {NEW}"


@pytest.mark.parametrize(
    "make_db_path",
    [
        pytest.param(_never_synced, id="never-downloaded"),
        pytest.param(_unopenable, id="cannot-be-opened"),
    ],
)
def test_apply_refuses_nothing_while_the_mod_list_cannot_answer(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    tmp_path: Path,
    make_db_path: Callable[[Path], Path],
) -> None:
    """A mod list that cannot answer never stops a version change.

    Installs a mod and applies a release against a mod list that was never
    downloaded, or that cannot be opened at all. Asserts the change went
    through. Reason: the check is an aid; a missing or broken local cache is
    sptmod's problem, not a reason to keep the player from their update.
    """
    install_commit(Repo(spt_game_dir), "Solo")
    offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(
        spt_game_dir, NEW, db_path=make_db_path(tmp_path)
    )

    assert result.version == NEW
    assert release_subjects(spt_game_dir)[0] == f"spt: {NEW}"


@pytest.mark.parametrize(
    ("wanted", "message"),
    [
        pytest.param(
            "4.1.9", "There is no published SPT 4.1.9.", id="unpublished-version"
        ),
        pytest.param(
            OTHER_SERIES,
            f"SPT {OTHER_SERIES} is not part of the same SPT series as your "
            f"installed SPT {INSTALLED}. Changing between series needs the "
            f"official SPT installer from https://sp-mod.com/installer.",
            id="other-series",
        ),
        pytest.param(
            NEWEST,
            f"SPT {NEWEST} publishes no download. Install it with the official "
            f"SPT installer from https://sp-mod.com/installer.",
            id="withdrawn-download",
        ),
    ],
)
def test_apply_keeps_the_release_refusals_ahead_of_the_mod_check(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: TrailerInstallCommit,
    db_path: Path,
    wanted: str,
    message: str,
) -> None:
    """A release that cannot be had is refused as such, not over the mods.

    Installs a mod without a version for any of the wanted releases and asks
    for a version that is not published, one of another series and one whose
    download was withdrawn. Asserts each is refused as an unavailable release,
    with its own wording. Reason: the mod check answers the question of
    whether a change is a good idea; it must not push aside the answer that
    the change is not possible at all.
    """
    install_commit(Repo(spt_game_dir), "OldMod")
    seed_catalog(
        db_path,
        [
            mod_json_with_versions(
                mod_id=731, name="OldMod", constraints=[FOR_OLD_SERIES]
            )
        ],
    )
    offer(spt_release_source, make_spt_release, OTHER_SERIES)
    spt_release_source.announce(NEWEST)

    with pytest.raises(SptReleaseUnavailableError) as error:
        services.apply_spt_release(spt_game_dir, wanted, db_path=db_path)

    assert str(error.value) == message
