"""Client-side throttle, 429 backoff/retry, and network/5xx error mapping."""

import httpx
import pytest
import respx

from sptmod.core.errors import ForgeUnavailableError
from test_forge_client_fixtures import (
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
    client_factory,
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
    client_factory,
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
    client_factory,
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
    client_factory,
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


def test_persistent_429_exhausts_retries_and_raises_forge_unavailable(
    client_factory, respx_router: respx.MockRouter
) -> None:
    route = respx_router.get("/mods").mock(return_value=httpx.Response(429))
    client = client_factory(max_retries=3)

    with pytest.raises(ForgeUnavailableError):
        client.mods()

    assert route.call_count > 1


def test_connection_error_raises_forge_unavailable(
    client_factory, respx_router: respx.MockRouter
) -> None:
    respx_router.get("/mods").mock(side_effect=httpx.ConnectError("boom"))
    client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        client.mods()


def test_persistent_500_raises_forge_unavailable(
    client_factory, respx_router: respx.MockRouter
) -> None:
    route = respx_router.get("/mods").mock(return_value=httpx.Response(500))
    client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        client.mods()

    assert route.call_count > 1
