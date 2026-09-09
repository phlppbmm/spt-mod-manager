"""Pagination behaviour of ForgeClient: iter_mods walking and per_page clamping."""

import httpx
import pytest
import respx

from fixture_types import ClientFactory
from fixtures_forge import recorded_requests
from forge_json import mod_json_minimal, page_envelope
from sptmod.core.forge.client import DEFAULT_BASE_URL


def test_iter_mods_walks_every_page_exactly_once_in_order(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """iter_mods() fetches all pages sequentially via links.next without repeating.

    Two pages are set up: page 1 has mods id=1,2 (current_page=1, last_page=2,
    next_url=/mods?page=2) and page 2 has mod id=3 (current_page=2, last_page=2,
    next_url=None). iter_mods() makes exactly 2 HTTP calls and yields all 3 items
    in order: (1, "Mod One"), (2, "Mod Two"), (3, "Mod Three").
    """
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

    def next_response(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    route = respx_router.get("/mods").mock(side_effect=next_response)
    client = client_factory()

    items = list(client.iter_mods())

    assert [(m.id, m.name) for m in items] == [
        (1, "Mod One"),
        (2, "Mod Two"),
        (3, "Mod Three"),
    ]
    assert route.call_count == 2


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

    def side_effect(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] > 3:
            # Bail out fast instead of relying only on the pytest-timeout
            # guard if a broken implementation loops forever.
            raise AssertionError("iter_mods kept re-fetching a next url it had already fetched")
        return responses[min(call_count["n"], len(responses)) - 1]

    route = respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    items = list(client.iter_mods())

    assert [m.id for m in items] == [1, 2]
    assert route.call_count == 2


_SINGLE_FETCH_PAGES = [
    pytest.param(
        page_envelope(
            [mod_json_minimal(id=1, name="Mod One")],
            current_page=1,
            last_page=1,
            total=1,
            next_url=None,
        ),
        id="last-page-reached",
    ),
    pytest.param(
        page_envelope(
            [mod_json_minimal(id=1, name="Mod One")],
            current_page=1,
            last_page=2,
            total=2,
            next_url=None,
        ),
        id="more-pages-but-no-next-url",
    ),
    pytest.param(
        page_envelope(
            [mod_json_minimal(id=1, name="Mod One")],
            current_page=1,
            last_page=2,
            total=2,
            # exactly the URL iter_mods used to fetch this very page
            next_url="/mods",
        ),
        id="next-url-repeats-current-page",
    ),
    pytest.param(
        page_envelope(
            [mod_json_minimal(id=1, name="Mod One")],
            current_page=2,
            last_page=2,
            total=1,
            next_url=f"{DEFAULT_BASE_URL}/mods?page=99",
        ),
        id="next-url-set-past-last-page",
    ),
]


@pytest.mark.parametrize("page", _SINGLE_FETCH_PAGES)
def test_iter_mods_stops_after_a_page_that_leads_nowhere_new(
    client_factory: ClientFactory, respx_router: respx.MockRouter, page: dict[str, object]
) -> None:
    """A page that offers no unfetched successor ends the walk after one request.

    Serves one envelope whose pagination hints all amount to "nothing new to
    fetch" - the last page reached, a missing next link, a next link back at the
    page just fetched, or a next link although the meta counter is already at the
    last page - and asserts the single item is yielded after exactly one request.
    Reason: the walk must end on any of these, and a broken implementation would
    either loop forever on the repeated link or fetch past the last page, so the
    mock fails the test on the fourth request instead of hanging.
    """
    call_count = {"n": 0}

    def side_effect(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] > 3:
            raise AssertionError("iter_mods kept fetching pages that lead nowhere new")
        return httpx.Response(200, json=page)

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
    route = respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope([])))
    client = client_factory()

    client.mods(per_page=requested_per_page)

    request = recorded_requests(route)[0]
    assert request.url.params["per_page"] == expected_sent
