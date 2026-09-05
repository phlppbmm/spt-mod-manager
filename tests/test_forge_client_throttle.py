"""Client-side throttle, 429 backoff/retry, and network/5xx error mapping."""

from collections.abc import Callable

import httpx
import pytest
import respx

from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.forge.client import BACKOFF_BASE_SECONDS
from test_forge_client_fixtures import (
    ClientFactory,
    FakeClock,
    RecordingSleep,
    client_factory,
    fake_clock,
    page_envelope,
    recording_sleep,
    respx_router,
)

__all__ = ["client_factory", "fake_clock", "recording_sleep", "respx_router"]


def test_third_request_within_window_sleeps_a_sane_duration(
    client_factory: ClientFactory,
    fake_clock: FakeClock,
    recording_sleep: RecordingSleep,
    respx_router: respx.MockRouter,
) -> None:
    respx_router.get("/mods").mock(
        return_value=httpx.Response(200, json=page_envelope([]))
    )
    client = client_factory(rate_per_minute=2)

    client.mods()
    client.mods()
    assert recording_sleep.calls == []

    client.mods()

    assert len(recording_sleep.calls) >= 1
    last_sleep = recording_sleep.calls[-1]
    assert 0 < last_sleep <= 60


def test_ping_bypasses_the_throttle(
    client_factory: ClientFactory,
    recording_sleep: RecordingSleep,
    respx_router: respx.MockRouter,
) -> None:
    respx_router.get("/ping").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    client = client_factory(rate_per_minute=1)

    for _ in range(5):
        client.ping()

    assert recording_sleep.calls == []


def test_429_then_retries_with_growing_backoff_then_succeeds(
    client_factory: ClientFactory,
    recording_sleep: RecordingSleep,
    respx_router: respx.MockRouter,
) -> None:
    attempts = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json=page_envelope([]))

    route = respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    page = client.mods()

    assert page.items == []
    assert route.call_count == 3
    assert len(recording_sleep.calls) == 2
    assert recording_sleep.calls[1] > recording_sleep.calls[0]


def test_429_honors_retry_after_header(
    client_factory: ClientFactory,
    recording_sleep: RecordingSleep,
    respx_router: respx.MockRouter,
) -> None:
    attempts = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json=page_envelope([]))

    respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    client.mods()

    assert recording_sleep.calls[0] >= 5.0


def test_429_with_a_malformed_retry_after_falls_back_to_exponential_backoff(
    client_factory: ClientFactory,
    recording_sleep: RecordingSleep,
    respx_router: respx.MockRouter,
) -> None:
    """An HTTP-date (or otherwise non-numeric) Retry-After is not honoured;
    the first retry sleeps the base exponential-backoff duration instead."""
    attempts = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
            )
        return httpx.Response(200, json=page_envelope([]))

    respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory()

    client.mods()

    assert recording_sleep.calls[0] == BACKOFF_BASE_SECONDS


@pytest.mark.parametrize(
    ("side_effect", "client_kwargs"),
    [
        (lambda request: httpx.Response(429), {"max_retries": 3}),
        (lambda request: httpx.Response(500), {}),
        (httpx.ConnectError("boom"), {}),
    ],
    ids=["429", "500", "connection-error"],
)
def test_persistent_failure_exhausts_retries_and_raises_forge_unavailable(
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    side_effect: Callable[[httpx.Request], httpx.Response] | Exception,
    client_kwargs: dict[str, int],
) -> None:
    route = respx_router.get("/mods").mock(side_effect=side_effect)
    client = client_factory(**client_kwargs)

    with pytest.raises(ForgeUnavailableError):
        client.mods()

    assert route.call_count > 1
