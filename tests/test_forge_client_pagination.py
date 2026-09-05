"""Pagination behaviour of ForgeClient: iter_mods walking and per_page clamping."""

import httpx
import pytest
import respx

from sptmod.core.forge.client import DEFAULT_BASE_URL
from test_forge_client_fixtures import (
    ClientFactory,
    client_factory,
    fake_clock,
    mod_json_minimal,
    page_envelope,
    recording_sleep,
    respx_router,
)

__all__ = ["client_factory", "fake_clock", "recording_sleep", "respx_router"]


def test_iter_mods_walks_every_page_exactly_once_in_order(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Follows links.next sequentially; fetches /mods once per page, yields each item once."""
    page1 = page_envelope(
        [
            mod_json_minimal(id=1, name="Mod One"),
            mod_json_minimal(id=2, name="Mod Two"),
        ],
        current_page=1,
        last_page=2,
        total=3,
        next_url=f"{DEFAULT_BASE_URL}/mods?page=2",
    )
    page2 = page_envelope(
        [mod_json_minimal(id=3, name="Mod Three")],
        current_page=2,
        last_page=2,
        total=3,
        next_url=None,
    )
    responses = iter([httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    route = respx_router.get("/mods").mock(side_effect=lambda request: next(responses))
    client = client_factory()

    items = list(client.iter_mods())

    assert [(m.id, m.name) for m in items] == [
        (1, "Mod One"),
        (2, "Mod Two"),
        (3, "Mod Three"),
    ]
    assert route.call_count == 2


def test_iter_mods_stops_at_last_page(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Stops when current_page >= last_page; does not follow links.next if it points backward."""
    single_page = page_envelope(
        [mod_json_minimal(id=1, name="Only Mod")],
        current_page=1,
        last_page=1,
        total=1,
        next_url=None,
    )
    route = respx_router.get("/mods").mock(
        return_value=httpx.Response(200, json=single_page)
    )
    client = client_factory()

    items = list(client.iter_mods())

    assert [m.id for m in items] == [1]
    assert route.call_count == 1


def test_iter_mods_stops_when_next_url_repeats_an_already_fetched_url(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """A page whose links.next points back at a URL already fetched must not loop.

    page1 -> next = URL_A. Fetching URL_A yields page2, whose next again is
    URL_A (already fetched). The iterator must stop after page2's items
    instead of re-fetching URL_A forever.
    """
    repeated_next_url = f"{DEFAULT_BASE_URL}/mods?page=2"
    page1 = page_envelope(
        [mod_json_minimal(id=1, name="Mod One")],
        current_page=1,
        last_page=2,
        total=2,
        next_url=repeated_next_url,
    )
    page2 = page_envelope(
        [mod_json_minimal(id=2, name="Mod Two")],
        current_page=2,
        last_page=2,
        total=2,
        next_url=repeated_next_url,
    )
    responses = [httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    call_count = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] > 3:
            # Bail out fast instead of relying only on the pytest-timeout
            # guard if a broken implementation loops forever.
            raise AssertionError(
                "iter_mods kept re-fetching a next url it had already fetched"
            )
        return responses[min(call_count["n"], len(responses)) - 1]

    route = respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    items = list(client.iter_mods())

    assert [m.id for m in items] == [1, 2]
    assert route.call_count == 2


def test_iter_mods_stops_when_a_page_short_of_last_page_has_no_next_url(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """meta says there is more to fetch (current_page < last_page) but the
    envelope's links.next is null: an inconsistent API response the client
    cannot chase further, so iteration stops after this page's items.
    """
    page1 = page_envelope(
        [mod_json_minimal(id=1, name="Mod One")],
        current_page=1,
        last_page=2,
        total=2,
        next_url=None,
    )
    route = respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page1))
    client = client_factory()

    items = list(client.iter_mods())

    assert [m.id for m in items] == [1]
    assert route.call_count == 1


def test_iter_mods_stops_when_next_url_points_back_at_the_page_just_fetched(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """links.next pointing back at a URL already fetched (here, page 1 itself)
    must not be re-fetched, even while meta still claims more pages remain.
    """
    page1 = page_envelope(
        [mod_json_minimal(id=1, name="Mod One")],
        current_page=1,
        last_page=2,
        total=2,
        next_url="/mods",  # exactly the URL iter_mods used for this same page
    )
    route = respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page1))
    client = client_factory()

    items = list(client.iter_mods())

    assert [m.id for m in items] == [1]
    assert route.call_count == 1


def test_iter_mods_stops_when_last_page_reached_despite_non_null_next(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """meta says current_page >= last_page: stop even if links.next is set."""
    bogus_next_url = f"{DEFAULT_BASE_URL}/mods?page=99"
    only_page = page_envelope(
        [mod_json_minimal(id=1, name="Mod One")],
        current_page=2,
        last_page=2,
        total=1,
        next_url=bogus_next_url,
    )
    call_count = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] > 3:
            raise AssertionError("iter_mods kept fetching past meta.last_page")
        return httpx.Response(200, json=only_page)

    route = respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    items = list(client.iter_mods())

    assert [m.id for m in items] == [1]
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("requested_per_page", "expected_sent"),
    [(999, "50"), (50, "50"), (10, "10"), (1, "1")],
)
def test_mods_per_page_is_clamped_to_50(
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    requested_per_page: int,
    expected_sent: str,
) -> None:
    """mods() clamps per_page to MAX_PER_PAGE (50) before sending the request."""
    route = respx_router.get("/mods").mock(
        return_value=httpx.Response(200, json=page_envelope([]))
    )
    client = client_factory()

    client.mods(per_page=requested_per_page)

    assert route.calls[0].request.url.params["per_page"] == expected_sent
