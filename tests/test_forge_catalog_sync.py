"""Tests for Catalog.sync, Catalog.fetch_versions and cache availability
when the Forge API is unreachable.
"""

import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import DEFAULT_BASE_URL
from test_forge_catalog_fixtures import (
    catalog,
    client_factory,
    db_path,
    fake_clock,
    make_mod_info,
    mod_json_full,
    owner_json,
    page_envelope,
    recording_sleep,
    respx_router,
    version_json,
)

__all__ = [
    "catalog",
    "client_factory",
    "db_path",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]


def _mods_route_queue(
    respx_router: respx.MockRouter, responses: list[httpx.Response]
) -> respx.Route:
    """Mock GET /mods to hand out ``responses`` in order, one per request."""

    def _next(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    return respx_router.get("/mods").mock(side_effect=_next)


def _alpha_mod(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Alpha Mod",
        "slug": "alpha-mod",
        "owner": owner_json(id=10, name="Alice"),
        "downloads": 100,
        "detail_url": f"{DEFAULT_BASE_URL}/mods/alpha-mod",
        "published_at": "2025-01-01T00:00:00Z",
        "created_at": "2024-12-01T00:00:00Z",
        "updated_at": "2025-06-08T00:00:00Z",
        "versions": [
            {
                "id": 901,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 10,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    }
    defaults.update(overrides)
    return mod_json_full(**defaults)


def _beta_mod(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": 2,
        "name": "Beta Mod",
        "slug": "beta-mod",
        "owner": owner_json(id=11, name="Bob"),
        "downloads": 200,
        "detail_url": f"{DEFAULT_BASE_URL}/mods/beta-mod",
        "published_at": "2025-01-05T00:00:00Z",
        "created_at": "2024-12-05T00:00:00Z",
        "updated_at": "2025-06-09T00:00:00Z",
        "versions": [
            {
                "id": 902,
                "version": "2.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 20,
                "published_at": "2025-01-06T00:00:00Z",
            }
        ],
    }
    defaults.update(overrides)
    return mod_json_full(**defaults)


def _gamma_mod(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": 3,
        "name": "Gamma Mod",
        "slug": "gamma-mod",
        "owner": owner_json(id=12, name="Carol"),
        "downloads": 300,
        "detail_url": f"{DEFAULT_BASE_URL}/mods/gamma-mod",
        "published_at": "2025-01-10T00:00:00Z",
        "created_at": "2024-12-10T00:00:00Z",
        "updated_at": "2025-06-10T00:00:00Z",
        "versions": [
            {
                "id": 903,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 30,
                "published_at": "2025-01-11T00:00:00Z",
            }
        ],
    }
    defaults.update(overrides)
    return mod_json_full(**defaults)


def _full_sync_pages() -> list[httpx.Response]:
    page1 = page_envelope(
        [_alpha_mod(), _beta_mod()],
        current_page=1,
        last_page=2,
        total=3,
        next_url=f"{DEFAULT_BASE_URL}/mods?page=2",
    )
    page2 = page_envelope(
        [_gamma_mod()],
        current_page=2,
        last_page=2,
        total=3,
        next_url=None,
    )
    return [httpx.Response(200, json=page1), httpx.Response(200, json=page2)]


def test_full_sync_fills_the_cache_from_a_multi_page_response(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()

    result = catalog.sync(client, full=True)

    assert route.call_count == 2
    assert result.new_mods == 3
    assert result.pages == 2
    assert result.full is True

    alpha = catalog.get_mod(1)
    assert alpha is not None
    assert alpha.name == "Alpha Mod"
    assert alpha.author == "Alice"
    assert alpha.slug == "alpha-mod"
    assert alpha.downloads == 100
    assert alpha.published_at == "2025-01-01T00:00:00Z"
    assert alpha.created_at == "2024-12-01T00:00:00Z"
    assert alpha.updated_at == "2025-06-08T00:00:00Z"

    beta = catalog.get_mod(2)
    assert beta is not None
    assert beta.author == "Bob"

    gamma = catalog.get_mod(3)
    assert gamma is not None
    assert gamma.author == "Carol"


def test_full_sync_watermark_is_the_max_updated_at_seen(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()

    catalog.sync(client, full=True)

    assert catalog.last_synced_at() == "2025-06-10T00:00:00Z"


def test_last_synced_at_is_none_before_the_first_sync(catalog: Catalog) -> None:
    assert catalog.last_synced_at() is None


def test_full_sync_caches_embedded_version_summaries_without_link(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()

    catalog.sync(client, full=True)

    versions = catalog.get_versions(1)
    assert [v.id for v in versions] == [901]
    assert versions[0].version == "1.0.0"
    assert versions[0].spt_version_constraint == "^3.8.0"
    assert versions[0].link is None
    assert versions[0].content_length is None


def test_delta_sync_requests_updated_between_from_watermark_minus_overlap_and_sort(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)  # watermark becomes 2025-06-10

    route.side_effect = None
    route.return_value = httpx.Response(
        200, json=page_envelope([], current_page=1, last_page=1, total=0, next_url=None)
    )
    catalog.sync(client, full=False)

    delta_call = route.calls[-1]
    params = delta_call.request.url.params
    assert params["filter[updated_between]"].startswith("2025-06-09,")
    assert params["sort"] == "-updated_at"


def test_delta_sync_updates_only_changed_rows_and_counts_correctly(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    beta_before = catalog.get_mod(2)
    gamma_changed = _gamma_mod(
        name="Gamma Mod Renamed", downloads=999, updated_at="2025-06-20T00:00:00Z"
    )
    delta_page = page_envelope(
        [_beta_mod(), gamma_changed],
        current_page=1,
        last_page=1,
        total=2,
        next_url=None,
    )
    route.side_effect = None
    route.return_value = httpx.Response(200, json=delta_page)

    result = catalog.sync(client, full=False)

    assert result.new_mods == 0
    assert result.updated_mods == 1
    assert result.full is False

    beta_after = catalog.get_mod(2)
    assert beta_after == beta_before

    gamma_after = catalog.get_mod(3)
    assert gamma_after is not None
    assert gamma_after.name == "Gamma Mod Renamed"
    assert gamma_after.downloads == 999
    assert gamma_after.updated_at == "2025-06-20T00:00:00Z"


def test_delta_sync_is_idempotent_when_run_twice_in_a_row(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    gamma_changed = _gamma_mod(
        name="Gamma Mod Renamed", downloads=999, updated_at="2025-06-20T00:00:00Z"
    )
    delta_page = page_envelope(
        [_beta_mod(), gamma_changed],
        current_page=1,
        last_page=1,
        total=2,
        next_url=None,
    )
    route.side_effect = None
    route.return_value = httpx.Response(200, json=delta_page)
    catalog.sync(client, full=False)

    mods_snapshot = [catalog.get_mod(i) for i in (1, 2, 3)]

    second_result = catalog.sync(client, full=False)

    assert second_result.new_mods == 0
    assert second_result.updated_mods == 0
    assert [catalog.get_mod(i) for i in (1, 2, 3)] == mods_snapshot


def test_delta_sync_without_prior_watermark_falls_back_to_full_listing(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()

    catalog.sync(client, full=False)

    for call in route.calls:
        assert "filter[updated_between]" not in call.request.url.params
    assert catalog.get_mod(1) is not None
    assert catalog.get_mod(2) is not None
    assert catalog.get_mod(3) is not None


def test_fetch_versions_fills_link_and_content_length(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)
    assert catalog.get_versions(1)[0].link is None  # sanity: still NULL pre-fetch

    full_versions_page = page_envelope(
        [
            version_json(
                id=901,
                version="1.0.0",
                link="https://cdn.example/alpha-1.0.0.zip",
                content_length=123456,
                spt_version_constraint="^3.8.0",
                published_at="2025-01-02T00:00:00Z",
            )
        ],
        current_page=1,
        last_page=1,
        total=1,
        next_url=None,
    )
    respx_router.get("/mod/1/versions").mock(
        return_value=httpx.Response(200, json=full_versions_page)
    )

    fetched = catalog.fetch_versions(client, 1)

    assert len(fetched) == 1
    assert fetched[0].id == 901
    assert fetched[0].link == "https://cdn.example/alpha-1.0.0.zip"
    assert fetched[0].content_length == 123456

    persisted = catalog.get_versions(1)
    assert len(persisted) == 1  # upserted, not duplicated
    assert persisted[0].id == 901
    assert persisted[0].link == "https://cdn.example/alpha-1.0.0.zip"
    assert persisted[0].content_length == 123456


@pytest.mark.parametrize(
    "fika_compatibility", ["compatible", "incompatible", "unknown"]
)
def test_fetch_versions_stores_and_exposes_fika_compatibility(
    catalog: Catalog,
    client_factory,
    respx_router: respx.MockRouter,
    fika_compatibility: str,
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    full_versions_page = page_envelope(
        [
            version_json(
                id=901,
                version="1.0.0",
                spt_version_constraint="^3.8.0",
                published_at="2025-01-02T00:00:00Z",
                fika_compatibility=fika_compatibility,
            )
        ],
        current_page=1,
        last_page=1,
        total=1,
        next_url=None,
    )
    respx_router.get("/mod/1/versions").mock(
        return_value=httpx.Response(200, json=full_versions_page)
    )

    fetched = catalog.fetch_versions(client, 1)

    assert fetched[0].fika_compatibility == fika_compatibility
    assert catalog.get_versions(1)[0].fika_compatibility == fika_compatibility


def test_sync_after_fetch_versions_does_not_wipe_stored_fika_compatibility(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    full_versions_page = page_envelope(
        [
            version_json(
                id=901,
                version="1.0.0",
                spt_version_constraint="^3.8.0",
                published_at="2025-01-02T00:00:00Z",
                fika_compatibility="incompatible",
            )
        ],
        current_page=1,
        last_page=1,
        total=1,
        next_url=None,
    )
    respx_router.get("/mod/1/versions").mock(
        return_value=httpx.Response(200, json=full_versions_page)
    )
    catalog.fetch_versions(client, 1)

    # A later sync only ever re-sends the /mods `include=versions` summary,
    # which carries no fika_compatibility field at all.
    _mods_route_queue(respx_router, _full_sync_pages())
    catalog.sync(client, full=True)

    assert catalog.get_versions(1)[0].fika_compatibility == "incompatible"


def test_forge_unavailable_during_sync_raises_but_keeps_prior_cache(
    catalog: Catalog,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    good_client = client_factory()
    catalog.sync(good_client, full=True)
    installed = [make_mod_info(forge_id="1", name="Alpha Mod", version="1.0.0")]
    search_before = catalog.search("alpha")
    versions_before = catalog.get_versions(1)
    classify_before = catalog.classify(installed)

    respx_router.get("/mods").mock(side_effect=httpx.ConnectError("boom"))
    failing_client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        catalog.sync(failing_client, full=True)

    assert catalog.search("alpha") == search_before
    assert catalog.get_versions(1) == versions_before
    assert catalog.classify(installed) == classify_before


def test_full_sync_prunes_mods_no_longer_returned_by_the_api(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)
    assert catalog.get_mod(3) is not None  # sanity: Gamma is cached

    # Second full sync: only Alpha and Beta remain server-side, Gamma is gone.
    only_alpha_and_beta = page_envelope(
        [_alpha_mod(), _beta_mod()],
        current_page=1,
        last_page=1,
        total=2,
        next_url=None,
    )
    route.side_effect = None
    route.return_value = httpx.Response(200, json=only_alpha_and_beta)

    catalog.sync(client, full=True)

    assert catalog.get_mod(3) is None
    assert catalog.get_versions(3) == []
    assert catalog.get_mod(1) is not None
    assert catalog.get_mod(2) is not None


def test_full_sync_with_an_empty_page_does_not_prune_the_cache(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    empty_page = page_envelope([], current_page=1, last_page=1, total=0, next_url=None)
    route.side_effect = None
    route.return_value = httpx.Response(200, json=empty_page)

    result = catalog.sync(client, full=True)

    assert result.new_mods == 0
    assert result.updated_mods == 0
    assert catalog.get_mod(1) is not None
    assert catalog.get_mod(2) is not None
    assert catalog.get_mod(3) is not None
    assert catalog.get_versions(1) != []
    assert catalog.get_versions(2) != []
    assert catalog.get_versions(3) != []


def test_delta_sync_does_not_prune_mods_absent_from_the_response(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    # Delta window only reports Alpha as changed; Beta and Gamma are simply
    # outside the queried date range, not deleted server-side.
    delta_page = page_envelope(
        [_alpha_mod(downloads=101)],
        current_page=1,
        last_page=1,
        total=1,
        next_url=None,
    )
    route.side_effect = None
    route.return_value = httpx.Response(200, json=delta_page)

    catalog.sync(client, full=False)

    assert catalog.get_mod(2) is not None
    assert catalog.get_mod(3) is not None
    assert catalog.get_versions(2) != []
    assert catalog.get_versions(3) != []


def test_full_sync_rolls_back_page_one_writes_when_a_later_page_fails(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    page1 = page_envelope(
        [_alpha_mod()],
        current_page=1,
        last_page=2,
        total=2,
        next_url=f"{DEFAULT_BASE_URL}/mods?page=2",
    )
    call_count = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(200, json=page1)
        return httpx.Response(
            500, json={"success": False, "code": "SERVER_ERROR", "message": "boom"}
        )

    respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        catalog.sync(client, full=True)

    assert catalog.get_mod(1) is None
    assert catalog.last_synced_at() is None


def test_delta_sync_falls_back_to_full_listing_when_the_watermark_is_unparseable(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    route = _mods_route_queue(
        respx_router,
        [
            httpx.Response(
                200,
                json=page_envelope(
                    [_alpha_mod(updated_at="not-a-date")],
                    current_page=1,
                    last_page=1,
                    total=1,
                    next_url=None,
                ),
            )
        ],
    )
    client = client_factory()
    catalog.sync(client, full=True)
    assert catalog.last_synced_at() == "not-a-date"

    route.side_effect = None
    route.return_value = httpx.Response(
        200, json=page_envelope([], current_page=1, last_page=1, total=0, next_url=None)
    )
    catalog.sync(client, full=False)

    delta_call = route.calls[-1]
    assert "filter[updated_between]" not in delta_call.request.url.params


def test_full_sync_with_no_updated_at_on_any_mod_leaves_the_watermark_unset(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                [_alpha_mod(updated_at=None)],
                current_page=1,
                last_page=1,
                total=1,
                next_url=None,
            ),
        )
    )
    client = client_factory()

    catalog.sync(client, full=True)

    assert catalog.last_synced_at() is None


class _RecordingProgress:
    """A ``Progress`` callback stand-in recording every call as a tuple."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, step: str, percent: int, message: str) -> None:
        self.calls.append((step, percent, message))


def test_sync_reports_a_page_of_a_zero_last_page_listing_as_complete(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope([], current_page=1, last_page=0, total=0, next_url=None),
        )
    )
    client = client_factory()
    progress = _RecordingProgress()

    catalog.sync(client, full=True, progress=progress)

    assert progress.calls == [("page", 100, "page 1 of 0")]


def test_fetch_versions_rolls_back_on_failure_and_keeps_the_prior_cache(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)
    versions_before = catalog.get_versions(1)

    respx_router.get("/mod/1/versions").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(ForgeUnavailableError):
        catalog.fetch_versions(client, 1)

    assert catalog.get_versions(1) == versions_before


@pytest.mark.parametrize(
    "raw_source_links",
    ["not json", '{"a": 1}'],
    ids=["malformed-json", "valid-json-non-list"],
)
def test_get_mod_tolerates_a_corrupted_source_links_column(
    catalog: Catalog,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    raw_source_links: str,
) -> None:
    """The cached ``source_links`` column is disposable like the rest of the
    catalog cache: corruption on disk degrades to an empty tuple rather than
    raising when read back through the public API.
    """
    _mods_route_queue(respx_router, _full_sync_pages())
    client = client_factory()
    catalog.sync(client, full=True)

    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE mods SET source_links = ? WHERE id = ?", (raw_source_links, 1)
        )
        db.commit()

    mod = catalog.get_mod(1)

    assert mod is not None
    assert mod.source_links == ()
