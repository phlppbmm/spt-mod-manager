"""Behaviour of the SPT release list.

Covers ``list_spt_releases``: the series filter and the version arithmetic it
rests on, the order of the listed releases, the installed and newer flags, the
page, date, download and size of every release, an unreadable installed
version, the client build, a handed release client, an unreachable source and
the releases of both default sources reaching an installation that came from
the archived one.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import clean_repo_at
from fixture_types import MakeSptRelease
from fixtures_spt import (
    BUILD,
    INSTALLED,
    NEW,
    NEWEST,
    OLDER,
    OTHER_SERIES,
    SptReleaseSource,
    offer,
    unversioned_game_dir,
    write_client_exe,
)
from pe_builder import write_server_exe
from sptmod.core import services
from sptmod.core.errors import NotAGameDirError, SptReleaseUnavailableError
from sptmod.core.sptrelease.client import SptReleaseClient

# Every release the arithmetic table offers, so each installed version picks
# its own series out of the same published set.
PUBLISHED = (OLDER, INSTALLED, NEW, NEWEST, OTHER_SERIES)

# What the 4.1 series looks like from an installation of 4.1.0.
FROM_4_1_0 = [(NEWEST, False, True), (NEW, False, True), (INSTALLED, True, False)]


# --- the installed version the list is read against ----------------------


@pytest.mark.parametrize(
    ("installed_version", "expected_latest", "expected"),
    [
        pytest.param("4.1.0", NEWEST, FROM_4_1_0, id="three-components"),
        pytest.param("v4.1.0", NEWEST, FROM_4_1_0, id="v-prefix"),
        pytest.param("4.1", NEWEST, FROM_4_1_0, id="two-components"),
        pytest.param("4.1.0.5", NEWEST, FROM_4_1_0, id="fourth-component-ignored"),
        pytest.param("4.1.x", NEWEST, FROM_4_1_0, id="non-numeric-component"),
        pytest.param("4", OLDER, [(OLDER, False, True)], id="one-component"),
        pytest.param(
            "4.1.2",
            NEWEST,
            [(NEWEST, True, False), (NEW, False, False), (INSTALLED, False, False)],
            id="newest-installed",
        ),
        pytest.param("3.1.2", "", [], id="other-major-series"),
    ],
)
def test_list_reads_the_published_releases_against_the_installed_version(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    installed_version: str,
    expected_latest: str,
    expected: list[tuple[str, bool, bool]],
) -> None:
    """list_spt_releases() lists the installed series only, newest version first.

    The installation reports ``installed_version``; the same five releases are
    published every time. A version is written as a ``v`` prefix, with fewer
    or more than three components or with a non-numeric one just as readably
    as a plain three-component one, because the first three numeric components
    decide everything: the major and minor pair picks the series -- releases of
    another minor or another major line need the official installer and are
    left out -- and the third component says which release is the installed one
    and which ones are above it.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", installed_version)
    write_client_exe(fake_game_dir_runtime)
    clean_repo_at(fake_game_dir_runtime)
    for version in PUBLISHED:
        offer(spt_release_source, make_spt_release, version)

    listed = services.list_spt_releases(fake_game_dir_runtime)

    assert listed.current_version == installed_version
    assert listed.client_build == BUILD
    assert listed.latest == expected_latest
    assert [
        (entry.version, entry.installed, entry.newer) for entry in listed.releases
    ] == expected


# --- list_spt_releases ---------------------------------------------------


def test_list_reports_the_page_and_the_size_of_every_release(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reports the release page, its date and its download.

    A release whose archive was withdrawn stays in the list, marked as not
    downloadable and without a size, because it can still be installed with
    the official installer.
    """
    archive = make_spt_release(version=NEW)
    spt_release_source.offer(
        archive, version=NEW, published_at="2025-03-04T05:06:07Z"
    )
    spt_release_source.announce(NEWEST, published_at="2025-06-07T08:09:10Z")

    listed = services.list_spt_releases(spt_game_dir)

    assert [
        (
            entry.version,
            entry.client_build,
            entry.published_at,
            entry.url,
            entry.downloadable,
            entry.content_length,
        )
        for entry in listed.releases
    ] == [
        (
            NEWEST,
            BUILD,
            datetime(2025, 6, 7, 8, 9, 10, tzinfo=UTC),
            "https://spt-releases.invalid/tag/4.1.2",
            False,
            None,
        ),
        (
            NEW,
            BUILD,
            datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC),
            "https://spt-releases.invalid/tag/4.1.1",
            True,
            archive.stat().st_size,
        ),
    ]


def test_list_shows_every_release_when_the_installed_version_is_unknown(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() drops the series filter when it knows no version.

    Without a readable version nothing can be compared, so every published
    release is offered and none of them is the installed one.
    """
    path = unversioned_game_dir(fake_game_dir_runtime)
    for version in (OLDER, NEW, OTHER_SERIES):
        offer(spt_release_source, make_spt_release, version)

    listed = services.list_spt_releases(path)

    assert listed.current_version == ""
    assert listed.latest == OTHER_SERIES
    assert [
        (entry.version, entry.installed, entry.newer) for entry in listed.releases
    ] == [(OTHER_SERIES, False, True), (NEW, False, True), (OLDER, False, True)]


def test_list_reports_no_client_build_when_the_game_client_is_missing(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() leaves the client build empty when it cannot be read.

    The game directory holds no ``EscapeFromTarkov.exe``, so the build stays
    unknown while the release list itself is still delivered.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", INSTALLED)
    clean_repo_at(fake_game_dir_runtime)
    offer(spt_release_source, make_spt_release, NEW)

    listed = services.list_spt_releases(fake_game_dir_runtime)

    assert listed.client_build == ""
    assert [entry.version for entry in listed.releases] == [NEW]


def test_list_is_empty_when_no_release_of_the_installed_series_is_published(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reports no release and no latest for a series without one."""
    offer(spt_release_source, make_spt_release, OTHER_SERIES)

    listed = services.list_spt_releases(spt_game_dir)

    assert not listed.releases
    assert listed.latest == ""
    assert listed.current_version == INSTALLED


def test_list_uses_a_release_client_it_is_handed(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reads through the client passed to it.

    Callers that already hold a client -- the server process serving several
    requests -- hand it in instead of having one built per call.
    """
    offer(spt_release_source, make_spt_release, NEW)

    with SptReleaseClient() as client:
        listed = services.list_spt_releases(spt_game_dir, client=client)

    assert [entry.version for entry in listed.releases] == [NEW]


def test_list_reports_an_unreachable_release_source(
    spt_game_dir: Path, spt_release_source: SptReleaseSource
) -> None:
    """list_spt_releases() raises SptReleaseUnavailableError when the source fails."""
    spt_release_source.unavailable(503)

    with pytest.raises(SptReleaseUnavailableError, match="not available right now"):
        services.list_spt_releases(spt_game_dir)


def test_list_rejects_a_directory_that_is_no_game_directory(tmp_path: Path) -> None:
    """list_spt_releases() raises NotAGameDirError before it reads any release."""
    with pytest.raises(NotAGameDirError, match="not an SPT game directory"):
        services.list_spt_releases(tmp_path)


# --- the release sources the client reads by itself ----------------------

# What the current release repository publishes beyond NEWEST, the last
# release the archived repository ever saw.
CONTINUED = "4.1.5"
CONTINUED_EARLIER = "4.1.3"


def test_list_spans_the_current_and_the_archived_release_source(
    fake_game_dir_runtime: Path,
    spt_release_sources: tuple[SptReleaseSource, ...],
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() offers releases of both default sources to an installation from the old one.

    Nothing names a release source, so the client reads its defaults: the
    current repository publishes 4.1.5 and 4.1.3, the archived one stopped at
    4.1.2, and the installation is that 4.1.2. All five releases of the series
    are listed newest first with 4.1.2 marked installed and the two later ones
    newer, because a player whose SPT came from the archived repository must
    be offered the releases that appeared after the move.
    """
    current, *archived = spt_release_sources
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", NEWEST)
    write_client_exe(fake_game_dir_runtime)
    clean_repo_at(fake_game_dir_runtime)
    for version in (CONTINUED, CONTINUED_EARLIER):
        offer(current, make_spt_release, version)
    for source in archived:
        for version in (NEWEST, NEW, INSTALLED):
            offer(source, make_spt_release, version)

    listed = services.list_spt_releases(fake_game_dir_runtime)

    assert listed.current_version == NEWEST
    assert listed.latest == CONTINUED
    assert [
        (entry.version, entry.installed, entry.newer) for entry in listed.releases
    ] == [
        (CONTINUED, False, True),
        (CONTINUED_EARLIER, False, True),
        (NEWEST, True, False),
        (NEW, False, False),
        (INSTALLED, False, False),
    ]
