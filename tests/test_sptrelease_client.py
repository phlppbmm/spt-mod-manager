"""Behaviour of ``SptReleaseClient``: URL resolution, request shape, parsing, and error mapping.

Covers: base URL precedence (argument > env > default, empty env falls back);
single GET with ``per_page=100`` and the GitHub ``Accept`` header; field
parsing for every ``SptRelease`` attribute including tag/name pattern
matching, asset selection, and date fallback; skipping of drafts,
pre-releases, and unparseable entries; newest-first ordering; error mapping
for network failures, error statuses, non-JSON bodies, and a JSON body that
is not a list; and the context-manager close contract.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx

from fixtures_forge import recorded_requests
from github_json import asset_json, release_json, release_without_asset_json
from sptmod.core.errors import SptReleaseUnavailableError
from sptmod.core.sptrelease.client import (
    DEFAULT_RELEASES_URL,
    UNKNOWN_DATE,
    SptRelease,
    SptReleaseClient,
    spt_releases_url,
)

FAKE_RELEASES_URL = "https://sptrelease.invalid/releases"
RELEASE_ENV_VAR = "SPTMOD_SPT_RELEASE_URL"


@pytest.fixture
def fake_router() -> Iterator[respx.MockRouter]:
    """A respx router for the release client; every request is intercepted, none reaches GitHub."""
    with respx.mock(assert_all_called=False) as router:
        yield router


# --------------------------------------------------------------------------
# URL resolution: argument > SPTMOD_SPT_RELEASE_URL > DEFAULT_RELEASES_URL.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        pytest.param(None, DEFAULT_RELEASES_URL, id="env-unset"),
        pytest.param("", DEFAULT_RELEASES_URL, id="env-empty"),
        pytest.param("https://mirror.invalid/releases", "https://mirror.invalid/releases", id="env-set"),
    ],
)
def test_spt_releases_url_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: str
) -> None:
    """spt_releases_url() returns the env var when it names a URL, the default otherwise.

    SPTMOD_SPT_RELEASE_URL is unset, set to the empty string, or set to a mirror
    address; the first two fall back to DEFAULT_RELEASES_URL because an empty
    variable names no host, the third wins so a mirror needs no code change.
    """
    if env_value is None:
        monkeypatch.delenv(RELEASE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(RELEASE_ENV_VAR, env_value)

    assert spt_releases_url() == expected


@pytest.mark.parametrize(
    ("env_value", "base_url", "asked_url"),
    [
        pytest.param(FAKE_RELEASES_URL, None, FAKE_RELEASES_URL, id="env-var-used"),
        pytest.param(None, None, DEFAULT_RELEASES_URL, id="default-used"),
        pytest.param(
            "https://env.invalid/releases", FAKE_RELEASES_URL, FAKE_RELEASES_URL, id="explicit-argument-wins"
        ),
    ],
)
def test_the_client_asks_the_host_the_precedence_rules_pick(
    monkeypatch: pytest.MonkeyPatch,
    fake_router: respx.MockRouter,
    env_value: str | None,
    base_url: str | None,
    asked_url: str,
) -> None:
    """releases() sends its request to the URL the precedence rules select.

    The environment names a URL and no argument is given, neither names one, or
    both do; the route registered on the expected host is the one that records a
    call, so the precedence (argument > env var > default) is asserted through
    the request the client really sends.
    """
    if env_value is None:
        monkeypatch.delenv(RELEASE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(RELEASE_ENV_VAR, env_value)

    route = fake_router.get(asked_url).mock(return_value=httpx.Response(200, json=[]))
    with SptReleaseClient(base_url) as client:
        client.releases()

    assert route.call_count == 1


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


def test_releases_raises_on_network_error(fake_router: respx.MockRouter) -> None:
    """A network failure (e.g. connection refused) is mapped to SptReleaseUnavailableError."""
    fake_router.get(FAKE_RELEASES_URL).mock(side_effect=httpx.ConnectError("boom"))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseUnavailableError) as exc_info:
        client.releases()

    assert "internet connection" in str(exc_info.value)


@pytest.mark.parametrize("status", [403, 500], ids=["forbidden", "server-error"])
def test_releases_raises_on_error_status(fake_router: respx.MockRouter, status: int) -> None:
    """A non-2xx HTTP status (403, 500) is mapped to SptReleaseUnavailableError, naming the status."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(status))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseUnavailableError) as exc_info:
        client.releases()

    assert str(status) in str(exc_info.value)


def test_releases_raises_on_a_non_json_body(fake_router: respx.MockRouter) -> None:
    """A 200 response whose body is not JSON is mapped to SptReleaseUnavailableError."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, content=b"not json at all"))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseUnavailableError) as exc_info:
        client.releases()

    assert "unreadable" in str(exc_info.value)


def test_releases_raises_when_the_body_is_a_json_object_instead_of_a_list(
    fake_router: respx.MockRouter,
) -> None:
    """A 200 response whose JSON body is an object (not a list) is mapped to SptReleaseUnavailableError."""
    fake_router.get(FAKE_RELEASES_URL).mock(return_value=httpx.Response(200, json={"message": "not a list"}))

    with SptReleaseClient(FAKE_RELEASES_URL) as client, pytest.raises(SptReleaseUnavailableError) as exc_info:
        client.releases()

    assert "unexpected shape" in str(exc_info.value)


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
