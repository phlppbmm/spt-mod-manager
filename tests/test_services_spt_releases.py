"""Behaviour of the SPT release list.

Covers ``list_spt_releases``: the series filter and the version arithmetic it
rests on, the order of the listed releases, the installed and newer flags, the
page, date, download and size of every release, an unreadable installed
version, the client build, a handed release client, the releases of both
default sources reaching an installation that came from the archived one, and
how the list degrades: the stored listing that spares the sources a request,
the refresh that replaces it, a source failure that leaves the installed
facts and the stored listing standing, and a stored listing that is damaged,
missing or impossible to write.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx

from conftest import clean_repo_at
from fixture_types import MakeSptRelease
from fixtures_spt import (
    BUILD,
    INSTALLED,
    NEW,
    NEWEST,
    OLDER,
    OTHER_SERIES,
    SPT_RELEASE_URL_ENV,
    SptReleaseSource,
    offer,
    unversioned_game_dir,
    write_client_exe,
)
from pe_builder import write_server_exe
from sptmod.core import services
from sptmod.core.errors import NotAGameDirError
from sptmod.core.sptrelease.client import DEFAULT_RELEASES_URLS, SptReleaseClient
from sptmod.core.workspace import spt_release_cache_dir

# A release source of its own, named through SPTMOD_SPT_RELEASE_URL, so what
# it publishes can be told apart from what the default sources publish.
MIRROR_URL = "https://spt-mirror.invalid/releases"

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


def test_list_keeps_the_installed_facts_when_the_release_source_fails(
    spt_game_dir: Path, spt_release_source: SptReleaseSource
) -> None:
    """list_spt_releases() still reports the installed version and build when the source fails.

    Nothing was ever downloaded and the source answers with an error, so the
    list stays empty and says why; the installed version and the Escape from
    Tarkov build come from the game directory and are reported anyway,
    because a failing network says nothing about what is installed here.
    """
    spt_release_source.unavailable(503)

    listed = services.list_spt_releases(spt_game_dir)

    assert listed.current_version == INSTALLED
    assert listed.client_build == BUILD
    assert not listed.releases
    assert listed.latest == ""
    assert listed.source_ok is False
    assert "not available right now" in listed.notice


def test_list_says_when_the_source_refuses_over_its_hourly_limit(
    spt_game_dir: Path, spt_release_source: SptReleaseSource
) -> None:
    """list_spt_releases() passes the limit message on instead of a bare error status.

    The source answers 403 with the request budget marked as spent; the
    notice names the hourly limit for anonymous requests, so a player reads
    that this passes by itself rather than suspecting their installation.
    """
    spt_release_source.unavailable(
        403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1767225600"}
    )

    listed = services.list_spt_releases(spt_game_dir)

    assert listed.source_ok is False
    assert "hourly limit for anonymous requests" in listed.notice
    assert listed.current_version == INSTALLED


def test_list_reads_the_source_once_and_serves_the_stored_listing_afterwards(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() asks the release source only for the first listing.

    Two calls in a row return the same releases while the source was read
    once, because the sources allow sixty anonymous requests an hour and
    every view of the app would otherwise spend some of them.
    """
    offer(spt_release_source, make_spt_release, NEW)

    first = services.list_spt_releases(spt_game_dir)
    second = services.list_spt_releases(spt_game_dir)

    assert [entry.version for entry in first.releases] == [NEW]
    assert [entry.version for entry in second.releases] == [NEW]
    assert second.source_ok is True
    assert second.notice == ""
    assert spt_release_source.list_calls == 1


def test_list_reads_the_source_again_when_a_fresh_listing_is_asked_for(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases(refresh=True) fetches again and replaces the stored listing.

    A release is published after the first listing was stored: an ordinary
    call keeps showing the stored one, the asked-for refresh brings the new
    release in, and the call after it is served from the refreshed copy --
    which is how the explicit refresh of the app reaches the player.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.list_spt_releases(spt_game_dir)
    offer(spt_release_source, make_spt_release, NEWEST)

    stored = services.list_spt_releases(spt_game_dir)
    refreshed = services.list_spt_releases(spt_game_dir, refresh=True)
    after = services.list_spt_releases(spt_game_dir)

    assert [entry.version for entry in stored.releases] == [NEW]
    assert [entry.version for entry in refreshed.releases] == [NEWEST, NEW]
    assert [entry.version for entry in after.releases] == [NEWEST, NEW]
    assert spt_release_source.list_calls == 2


def test_list_serves_the_stored_listing_when_a_refresh_fails(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """A refresh the source refuses still returns the stored listing plus a notice.

    The listing was downloaded before the source started failing; the
    releases stay on show, marked as not current and dated in the notice,
    because a list from earlier beats an empty panel once the request budget
    of the hour is spent.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.list_spt_releases(spt_game_dir)
    spt_release_source.unavailable(503)

    listed = services.list_spt_releases(spt_game_dir, refresh=True)

    assert [entry.version for entry in listed.releases] == [NEW]
    assert listed.latest == NEW
    assert listed.current_version == INSTALLED
    assert listed.source_ok is False
    assert "not available right now" in listed.notice
    assert "downloaded on" in listed.notice


def test_the_listing_of_a_named_source_is_kept_apart_from_the_default_ones(
    spt_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listing stored for one set of sources is never served for another.

    The default sources publish 4.1.1 and the mirror named by
    SPTMOD_SPT_RELEASE_URL publishes 4.1.2; each is read from its own source
    and reading the defaults again afterwards still answers with their own
    releases, so pointing at a mirror neither reads nor overwrites what the
    official sources said.
    """
    with respx.mock(assert_all_called=False) as router:
        defaults = [SptReleaseSource(router, url) for url in DEFAULT_RELEASES_URLS]
        mirror = SptReleaseSource(router, MIRROR_URL)
        defaults[0].announce(NEW)
        mirror.announce(NEWEST)
        monkeypatch.delenv(SPT_RELEASE_URL_ENV, raising=False)
        from_defaults = services.list_spt_releases(spt_game_dir)
        monkeypatch.setenv(SPT_RELEASE_URL_ENV, MIRROR_URL)
        from_mirror = services.list_spt_releases(spt_game_dir)
        monkeypatch.delenv(SPT_RELEASE_URL_ENV)
        defaults_again = services.list_spt_releases(spt_game_dir)

    assert [entry.version for entry in from_defaults.releases] == [NEW]
    assert [entry.version for entry in from_mirror.releases] == [NEWEST]
    assert [entry.version for entry in defaults_again.releases] == [NEW]
    assert mirror.list_calls == 1


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not json at all", id="no-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param(
            '{"fetched_at": "2026-01-01T00:00:00+00:00", "releases": "4.1.1"}',
            id="releases-not-a-list",
        ),
        pytest.param(
            '{"fetched_at": "2026-01-01T00:00:00+00:00", "releases": '
            '["4.1.1", {"version": ""}, {"version": "4.1.1", "published_at": 7}]}',
            id="unusable-entries",
        ),
        pytest.param(
            '{"fetched_at": "whenever", "releases": '
            '[{"version": "4.1.1", "published_at": "2026-01-01T00:00:00+00:00"}]}',
            id="unreadable-date-of-download",
        ),
        pytest.param("", id="half-written"),
    ],
)
def test_a_damaged_stored_listing_counts_as_none(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    content: str,
) -> None:
    """A stored listing that cannot be read is replaced by a fresh one instead of failing.

    The stored copy is damaged in every way a file can be -- no JSON at all,
    the wrong shape, unusable entries, an unreadable date of download, and
    nothing written yet -- and the next call reads the source again and shows
    what it says, because the copy is disposable and must never stand in the
    way of the list.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.list_spt_releases(spt_game_dir)
    for entry in spt_release_cache_dir().glob("*.json"):
        entry.write_text(content, encoding="utf-8")
    offer(spt_release_source, make_spt_release, NEWEST)

    listed = services.list_spt_releases(spt_game_dir)

    assert [entry.version for entry in listed.releases] == [NEWEST, NEW]
    assert listed.source_ok is True
    assert spt_release_source.list_calls == 2


def test_a_listing_that_cannot_be_stored_does_not_fail_the_call(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cache directory that cannot be created leaves the release list working.

    A file sits where the stored listings belong, so neither writing nor
    reading one can work; both calls answer from the source instead of
    failing, because the stored copy is a saving and never a requirement.
    """
    cache_home = tmp_path / "cache"
    (cache_home / "sptmod").mkdir(parents=True)
    (cache_home / "sptmod" / "spt-releases").write_text("not a directory")
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    offer(spt_release_source, make_spt_release, NEW)

    first = services.list_spt_releases(spt_game_dir)
    second = services.list_spt_releases(spt_game_dir)

    assert [entry.version for entry in first.releases] == [NEW]
    assert [entry.version for entry in second.releases] == [NEW]
    assert second.source_ok is True
    assert spt_release_source.list_calls == 2


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
