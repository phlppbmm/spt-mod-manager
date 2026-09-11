"""Behaviour of ``SptReleaseClient``: source selection, merging, request shape, parsing, error mapping.

Covers: which sources are read (argument > env var > the default sources, an
empty env var falling back to them); the merge of the default sources into one
newest-first list with the first source winning a version two sources publish;
single GET with ``per_page=100`` and the GitHub ``Accept`` header; field
parsing for every ``SptRelease`` attribute including tag/name pattern
matching, asset selection, and date fallback; skipping of drafts,
pre-releases, and unparseable entries; newest-first ordering; error mapping
for network failures, error statuses, non-JSON bodies, and a JSON body that
is not a list, for a failure of any single source; the refusal over the hourly limit for
anonymous requests, told apart from every other refusal; and the
context-manager close contract.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from fixture_types import JsonDict
from fixtures_forge import recorded_requests
from github_json import asset_json, release_json, release_without_asset_json
from sptmod.core.errors import SptReleaseRateLimitedError, SptReleaseUnavailableError
from sptmod.core.sptrelease.client import (
    DEFAULT_RELEASES_URLS,
    RATE_LIMIT_TIME_FORMAT,
    UNKNOWN_DATE,
    SptRelease,
    SptReleaseClient,
)

FAKE_RELEASES_URL = "https://sptrelease.invalid/releases"
RELEASE_ENV_VAR = "SPTMOD_SPT_RELEASE_URL"
BUILD = "40743"
ARCHIVE_SIZE = 123456789

# The sources read when nothing names one: SPT's current release repository
# first, every source it succeeded after it.
CURRENT_SOURCE, *ARCHIVED_SOURCES = DEFAULT_RELEASES_URLS


def released(
    version: str,
    published_at: str,
    *,
    source: str = "current",
    build: str = BUILD,
    size: int = ARCHIVE_SIZE,
) -> JsonDict:
    """Build a release entry for ``version`` whose page and archive name the source publishing it.

    Every field a source can differ in -- the page, the build, the date, the
    download link and its size -- carries ``source``, so a merged release can
    be traced back to the repository it came from.
    """
    name = f"SPT-{version}-{build}.7z"
    return release_json(
        tag_name=version,
        name=f"SPT {version} ({build})",
        published_at=published_at,
        html_url=f"https://{source}.invalid/tag/{version}",
        assets=[
            asset_json(
                name=name,
                browser_download_url=f"https://{source}.invalid/{version}/{name}",
                size=size,
            )
        ],
    )


# What the current release repository carries: the versions the archived one
# never saw.
CONTINUED_RELEASES = [
    released("4.1.5", "2026-03-01T00:00:00Z"),
    released("4.1.4", "2026-02-01T00:00:00Z"),
    released("4.1.3", "2026-01-01T00:00:00Z"),
]

# What the archived repository stopped at.
ARCHIVED_RELEASES = [
    released("4.1.2", "2025-06-01T00:00:00Z", source="archived"),
    released("4.1.1", "2025-03-01T00:00:00Z", source="archived"),
    released("4.0.9", "2024-12-01T00:00:00Z", source="archived"),
]


@pytest.fixture
def fake_router() -> Iterator[respx.MockRouter]:
    """A respx router for the release client; every request is intercepted, none reaches GitHub."""
    with respx.mock(assert_all_called=False) as router:
        yield router


def mount(router: respx.MockRouter, url: str, releases: list[JsonDict]) -> respx.Route:
    """Answer a GET of ``url`` with ``releases`` and return the route, so its calls can be counted."""
    return router.get(url).mock(return_value=httpx.Response(200, json=releases))


def failure_mock(mode: str) -> dict[str, Any]:
    """Return the respx keywords letting a source fail in ``mode``.

    Built per call, so two parametrized cases never share one response object.
    """
    modes: dict[str, dict[str, Any]] = {
        "unreachable": {"side_effect": httpx.ConnectError("boom")},
        "forbidden": {"return_value": httpx.Response(403)},
        "server-error": {"return_value": httpx.Response(500)},
        "unreadable-body": {"return_value": httpx.Response(200, content=b"not json at all")},
        "not-a-list": {"return_value": httpx.Response(200, json={"message": "not a list"})},
    }
    return modes[mode]


# --------------------------------------------------------------------------
# Source selection and the merge of the default sources.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("env_value", [pytest.param(None, id="env-unset"), pytest.param("", id="env-empty")])
def test_releases_merges_every_default_source_into_one_newest_first_list(
    monkeypatch: pytest.MonkeyPatch, fake_router: respx.MockRouter, env_value: str | None
) -> None:
    """Without a named source, every default source is read and their releases arrive in one list.

    SPTMOD_SPT_RELEASE_URL is unset or empty -- an empty variable names no
    host -- while the current repository publishes 4.1.5, 4.1.4 and 4.1.3 and
    the archived one 4.1.2 and older; the returned list spans both
    repositories, newest first, because a player whose SPT came from the
    archived source must still be offered what the current one publishes.
    """
    if env_value is None:
        monkeypatch.delenv(RELEASE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(RELEASE_ENV_VAR, env_value)
    mount(fake_router, CURRENT_SOURCE, CONTINUED_RELEASES)
    for url in ARCHIVED_SOURCES:
        mount(fake_router, url, ARCHIVED_RELEASES)

    with SptReleaseClient() as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["4.1.5", "4.1.4", "4.1.3", "4.1.2", "4.1.1", "4.0.9"]


def test_a_version_two_sources_publish_is_kept_once_with_the_entry_of_the_first(
    monkeypatch: pytest.MonkeyPatch, fake_router: respx.MockRouter
) -> None:
    """A version both default sources publish appears once, carrying the first source's data.

    Both repositories publish 4.1.2, with a different page, build, date and
    download; the merged list holds it once and every field comes from the
    source read first, because the archived copy of a version the current
    source republished is the outdated one.
    """
    monkeypatch.delenv(RELEASE_ENV_VAR, raising=False)
    mount(
        fake_router,
        CURRENT_SOURCE,
        [released("4.1.3", "2026-01-01T00:00:00Z"), released("4.1.2", "2025-12-01T00:00:00Z", build="40760")],
    )
    for url in ARCHIVED_SOURCES:
        mount(fake_router, url, [released("4.1.2", "2025-06-01T00:00:00Z", source="archived", build="40001", size=99)])

    with SptReleaseClient() as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["4.1.3", "4.1.2"]
    assert releases[1] == SptRelease(
        version="4.1.2",
        client_build="40760",
        published_at=datetime(2025, 12, 1, tzinfo=UTC),
        url="https://current.invalid/tag/4.1.2",
        download_url="https://current.invalid/4.1.2/SPT-4.1.2-40760.7z",
        archive_name="SPT-4.1.2-40760.7z",
        content_length=ARCHIVE_SIZE,
    )


def test_a_version_one_source_publishes_twice_is_kept_once_with_its_first_entry(
    fake_router: respx.MockRouter,
) -> None:
    """A version listed twice by one source is kept once, with the entry that came first.

    The source lists 4.1.2 twice with different pages and builds; the result
    holds the first of them, because a source's own order is the order it
    means.
    """
    mount(
        fake_router,
        FAKE_RELEASES_URL,
        [
            released("4.1.2", "2025-12-01T00:00:00Z", build="40760"),
            released("4.1.2", "2025-06-01T00:00:00Z", source="stale", build="40001"),
        ],
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert [(release.version, release.client_build, release.url) for release in releases] == [
        ("4.1.2", "40760", "https://current.invalid/tag/4.1.2")
    ]


@pytest.mark.parametrize(
    ("env_value", "base_url"),
    [
        pytest.param(FAKE_RELEASES_URL, None, id="env-var-used"),
        pytest.param("https://env.invalid/releases", FAKE_RELEASES_URL, id="explicit-argument-wins"),
    ],
)
def test_a_named_source_replaces_the_default_sources(
    monkeypatch: pytest.MonkeyPatch,
    fake_router: respx.MockRouter,
    env_value: str,
    base_url: str | None,
) -> None:
    """A source named by the environment or by the argument is read alone; no default is asked.

    The environment names the mirror and no argument is given, or both name a
    source; the mirror publishes 7.0.0 while the defaults publish the usual
    versions, so the result naming only 7.0.0 and every default route staying
    uncalled show that pointing at a mirror really replaces both defaults --
    and that the argument outranks the variable.
    """
    monkeypatch.setenv(RELEASE_ENV_VAR, env_value)
    defaults = {url: mount(fake_router, url, CONTINUED_RELEASES) for url in DEFAULT_RELEASES_URLS}
    named = mount(fake_router, FAKE_RELEASES_URL, [released("7.0.0", "2026-06-01T00:00:00Z", source="mirror")])

    with SptReleaseClient(base_url) as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["7.0.0"]
    assert named.call_count == 1
    assert {url: route.call_count for url, route in defaults.items()} == dict.fromkeys(DEFAULT_RELEASES_URLS, 0)


# --------------------------------------------------------------------------
# Request shape: one GET, per_page=100, GitHub Accept header.
# --------------------------------------------------------------------------


def test_releases_sends_one_get_with_per_page_100_and_accept_header(
    fake_router: respx.MockRouter,
) -> None:
    """releases() issues exactly one GET with per_page=100 and the GitHub Accept header."""
    route = fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        client.releases()

    assert route.call_count == 1
    request = recorded_requests(route)[0]
    assert request.url.params["per_page"] == "100"
    assert request.headers["Accept"] == "application/vnd.github+json"


# --------------------------------------------------------------------------
# Parsing: every field, tag/name patterns, assets, and dates.
# --------------------------------------------------------------------------


def test_releases_parses_a_well_formed_release_with_an_asset(fake_router: respx.MockRouter) -> None:
    """A well-formed release with a .7z asset is parsed into every SptRelease field."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                release_json(
                    tag_name="4.1.2",
                    name="SPT 4.1.2 (40743)",
                    html_url="https://github.com/sp-tarkov/build/releases/tag/4.1.2",
                    published_at="2025-01-02T03:04:05Z",
                    assets=[
                        asset_json(
                            name="SPT-4.1.2-40743.7z",
                            browser_download_url="https://cdn.example/SPT-4.1.2-40743.7z",
                            size=987654321,
                        )
                    ],
                )
            ],
        )
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert releases == [
        SptRelease(
            version="4.1.2",
            client_build="40743",
            published_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
            url="https://github.com/sp-tarkov/build/releases/tag/4.1.2",
            download_url="https://cdn.example/SPT-4.1.2-40743.7z",
            archive_name="SPT-4.1.2-40743.7z",
            content_length=987654321,
        )
    ]


def test_releases_strips_the_v_prefix_from_the_tag(fake_router: respx.MockRouter) -> None:
    """A tag_name of 'v4.1.2' yields version '4.1.2' without the prefix."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[release_json(tag_name="v4.1.2")]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["4.1.2"]


def test_releases_leaves_client_build_empty_when_the_name_has_no_build_group(
    fake_router: respx.MockRouter,
) -> None:
    """A name without a '(build)' group yields an empty client_build."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[release_json(name="SPT 4.1.2")]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert [release.client_build for release in releases] == [""]


def test_releases_without_an_asset_leaves_download_fields_empty(fake_router: respx.MockRouter) -> None:
    """A release with no assets is kept, with empty download_url/archive_name and None content_length."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[release_without_asset_json()]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert len(releases) == 1
    assert releases[0].download_url == ""
    assert releases[0].archive_name == ""
    assert releases[0].content_length is None


def test_releases_skips_non_archive_assets_and_picks_the_first_7z_case_insensitively(
    fake_router: respx.MockRouter,
) -> None:
    """A non-.7z asset before an upper-case '.7Z' asset is skipped; the .7Z asset is selected."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                release_json(
                    assets=[
                        asset_json(name="README.txt", browser_download_url="https://cdn.example/README.txt"),
                        asset_json(
                            name="SPT-4.1.2-40743.7Z",
                            browser_download_url="https://cdn.example/SPT-4.1.2-40743.7Z",
                            size=42,
                        ),
                    ]
                )
            ],
        )
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert releases[0].archive_name == "SPT-4.1.2-40743.7Z"
    assert releases[0].download_url == "https://cdn.example/SPT-4.1.2-40743.7Z"
    assert releases[0].content_length == 42


def test_releases_ignores_an_asset_size_that_is_not_a_plain_int(fake_router: respx.MockRouter) -> None:
    """An asset whose 'size' is not a plain int (here: a bool) yields content_length None."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[release_json(assets=[asset_json(size=True)])])
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert releases[0].content_length is None


@pytest.mark.parametrize(
    ("published_at", "expected"),
    [
        pytest.param("2025-01-02T03:04:05Z", datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC), id="zulu-suffix"),
        pytest.param("2025-01-02T03:04:05", datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC), id="naive-gets-utc"),
        pytest.param("not-a-date", UNKNOWN_DATE, id="unparseable-falls-back"),
    ],
)
def test_releases_parses_published_at(
    fake_router: respx.MockRouter, published_at: str, expected: datetime
) -> None:
    """published_at is parsed with a UTC default for naive timestamps and UNKNOWN_DATE on failure."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[release_json(published_at=published_at)])
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert releases[0].published_at == expected


def test_releases_orders_newest_first(fake_router: respx.MockRouter) -> None:
    """releases() sorts by published_at, newest first, regardless of source order."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                release_json(tag_name="1.0.0", name="SPT 1.0.0", published_at="2024-01-01T00:00:00Z"),
                release_json(tag_name="3.0.0", name="SPT 3.0.0", published_at="2026-01-01T00:00:00Z"),
                release_json(tag_name="2.0.0", name="SPT 2.0.0", published_at="2025-01-01T00:00:00Z"),
            ],
        )
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["3.0.0", "2.0.0", "1.0.0"]


def test_releases_with_an_unparseable_date_sorts_last(fake_router: respx.MockRouter) -> None:
    """A release with an unparseable published_at (UNKNOWN_DATE) sorts after every dated release."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                release_json(tag_name="1.0.0", name="SPT 1.0.0", published_at="garbage"),
                release_json(tag_name="2.0.0", name="SPT 2.0.0", published_at="2025-01-01T00:00:00Z"),
            ],
        )
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["2.0.0", "1.0.0"]


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        pytest.param("draft", {"draft": True}, id="draft"),
        pytest.param("prerelease", {"prerelease": True}, id="prerelease"),
        pytest.param("unparseable-tag", {"tag_name": "not-a-version"}, id="unparseable-tag"),
        pytest.param("non-string-tag", {"tag_name": 412}, id="non-string-tag"),
        pytest.param("unparseable-name", {"name": "Weekly Build"}, id="unparseable-name"),
    ],
)
def test_releases_skips_entries_that_carry_no_usable_version(
    fake_router: respx.MockRouter, label: str, overrides: dict[str, object]
) -> None:
    """Drafts, pre-releases, and entries whose tag or name cannot be parsed are left out."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[release_json(**overrides)]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert releases == [], label


def test_releases_skips_a_non_object_entry_in_the_payload(fake_router: respx.MockRouter) -> None:
    """A payload entry that is not a JSON object (e.g. a bare string) is skipped without error."""
    fake_router.get(FAKE_RELEASES_URL).mock(
        return_value=httpx.Response(200, json=["not an object", release_json(tag_name="5.0.0", name="SPT 5.0.0")])
    )

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        releases = client.releases()

    assert [release.version for release in releases] == ["5.0.0"]


def test_releases_with_an_empty_list_returns_an_empty_list(fake_router: respx.MockRouter) -> None:
    """An empty releases list from the source yields an empty result, no error."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        assert client.releases() == []


# --------------------------------------------------------------------------
# Error mapping.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failing", [pytest.param(0, id="first-source-fails"), pytest.param(-1, id="last-source-fails")]
)
@pytest.mark.parametrize(
    ("mode", "fragment"),
    [
        pytest.param("unreachable", "internet connection", id="unreachable"),
        pytest.param("forbidden", "403", id="forbidden"),
        pytest.param("server-error", "500", id="server-error"),
        pytest.param("unreadable-body", "unreadable", id="unreadable-body"),
        pytest.param("not-a-list", "unexpected shape", id="not-a-list"),
    ],
)
def test_a_single_failing_source_lets_the_whole_call_fail(
    monkeypatch: pytest.MonkeyPatch,
    fake_router: respx.MockRouter,
    failing: int,
    mode: str,
    fragment: str,
) -> None:
    """Any source that cannot be read raises SptReleaseUnavailableError, whichever source it is.

    One default source is unreachable, answers 403 or 500, sends a body that
    is not JSON or a JSON object instead of a list, while every other source
    answers with a full release list; the call raises and names the reason,
    because a half-read list would silently hide the newest releases from the
    player instead of telling them the source is down.
    """
    monkeypatch.delenv(RELEASE_ENV_VAR, raising=False)
    failing_url = DEFAULT_RELEASES_URLS[failing]
    for url in DEFAULT_RELEASES_URLS:
        if url == failing_url:
            fake_router.get(url).mock(**failure_mock(mode))
        else:
            mount(fake_router, url, CONTINUED_RELEASES)

    with SptReleaseClient() as client, pytest.raises(SptReleaseUnavailableError) as exc_info:
        client.releases()

    assert fragment in str(exc_info.value)


# --------------------------------------------------------------------------
# The hourly limit for anonymous requests.
# --------------------------------------------------------------------------

# The moment the request budget of the hour is refilled, half an hour from
# now, and one that went by an hour ago; both are relative to the run, because
# whether a reset lies ahead is exactly what decides the wording.
RESET_EPOCH = int(datetime.now(tz=UTC).timestamp()) + 1800
PASSED_EPOCH = int(datetime.now(tz=UTC).timestamp()) - 3600


def limited(status_code: int, headers: dict[str, str]) -> dict[str, Any]:
    """Return the respx keywords for a refusal carrying ``headers``."""
    return {"return_value": httpx.Response(status_code, headers=headers)}


def local_time(epoch: int) -> str:
    """Return how the moment ``epoch`` names reads in the local time zone."""
    return datetime.fromtimestamp(epoch, tz=UTC).astimezone().strftime(RATE_LIMIT_TIME_FORMAT)


@pytest.mark.parametrize(
    ("status_code", "headers"),
    [
        pytest.param(
            403,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(RESET_EPOCH)},
            id="forbidden-with-exhausted-budget",
        ),
        pytest.param(
            429,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(RESET_EPOCH)},
            id="too-many-requests-with-exhausted-budget",
        ),
    ],
)
def test_an_exhausted_hourly_limit_is_reported_as_one_and_names_the_time_it_lifts(
    fake_router: respx.MockRouter, status_code: int, headers: dict[str, str]
) -> None:
    """A refusal marked as the spent request budget raises SptReleaseRateLimitedError naming the time.

    The source answers 403 or 429 with ``X-RateLimit-Remaining: 0`` and the
    unix time the window ends; the message says that the hourly limit for
    anonymous requests is spent, that the installation is fine and from when
    it works again, because that is a wait, not a broken setup, and a player
    who reads it can tell the two apart.
    """
    fake_router.get(FAKE_RELEASES_URL).mock(**limited(status_code, headers))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseRateLimitedError) as exc_info:
        client.releases()

    message = str(exc_info.value)
    assert "hourly limit for anonymous requests" in message
    assert "Your installation is fine" in message
    assert f"works again from about {local_time(RESET_EPOCH)}" in message


def test_a_retry_after_header_alone_marks_the_refusal_as_the_hourly_limit(
    fake_router: respx.MockRouter,
) -> None:
    """A 403 carrying only ``Retry-After`` raises SptReleaseRateLimitedError naming that moment.

    GitHub answers secondary limits with a number of seconds instead of the
    budget headers; the wait is the same one, so it is reported the same way,
    with the time counted from now.
    """
    fake_router.get(FAKE_RELEASES_URL).mock(**limited(403, {"Retry-After": "600"}))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseRateLimitedError) as exc_info:
        client.releases()

    lifts = (datetime.now().astimezone() + timedelta(seconds=600)).strftime(RATE_LIMIT_TIME_FORMAT)
    assert f"works again from about {lifts}" in str(exc_info.value)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "soon"},
            id="unreadable-reset",
        ),
        pytest.param({"X-RateLimit-Remaining": "0"}, id="no-reset"),
        pytest.param(
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(PASSED_EPOCH)},
            id="reset-already-gone-by",
        ),
        pytest.param({"Retry-After": "in a while"}, id="unreadable-retry-after"),
    ],
)
def test_an_exhausted_hourly_limit_without_a_readable_moment_says_to_try_again_shortly(
    fake_router: respx.MockRouter, headers: dict[str, str]
) -> None:
    """A limit refusal whose moment cannot be read or has gone by reports the limit without a time.

    The reset is unreadable, missing, already in the past -- a clock running
    off or an answer out of a cache -- or no number of seconds at all; the
    message keeps saying that this is the hourly limit and asks for a retry in
    a few minutes, because neither a riddle nor a time that has been and gone
    tells a player anything.
    """
    fake_router.get(FAKE_RELEASES_URL).mock(**limited(403, headers))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseRateLimitedError) as exc_info:
        client.releases()

    message = str(exc_info.value)
    assert "hourly limit for anonymous requests" in message
    assert "Try again in a few minutes." in message


def test_a_refusal_without_any_limit_marking_stays_a_plain_unavailable_source(
    fake_router: respx.MockRouter,
) -> None:
    """A 403 carrying neither budget nor retry header keeps the wording of an unreadable source.

    Nothing says the budget is spent -- requests the source refuses for any
    other reason must not be dressed up as a wait that will pass on its own.
    """
    fake_router.get(FAKE_RELEASES_URL).mock(**limited(403, {"X-RateLimit-Remaining": "7"}))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseUnavailableError) as exc_info:
        client.releases()

    assert not isinstance(exc_info.value, SptReleaseRateLimitedError)
    assert "not available right now (HTTP 403)" in str(exc_info.value)


# --------------------------------------------------------------------------
# Context manager / close().
# --------------------------------------------------------------------------


def test_context_manager_closes_client_on_exit(fake_router: respx.MockRouter) -> None:
    """SptReleaseClient closes its underlying httpx.Client on context exit."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[]))

    with SptReleaseClient(FAKE_RELEASES_URL) as client:
        client.releases()  # succeeds while the client is open

    with pytest.raises((RuntimeError, SptReleaseUnavailableError)):
        client.releases()


def test_close_can_be_called_directly(fake_router: respx.MockRouter) -> None:
    """close() shuts down the client outside of the context-manager protocol too."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json=[]))

    client = SptReleaseClient(FAKE_RELEASES_URL)
    client.releases()
    client.close()

    with pytest.raises((RuntimeError, SptReleaseUnavailableError)):
        client.releases()
