"""ForgeClient construction validation and context-manager lifecycle."""

import httpx
import pytest
import respx

from sptmod.core.forge.client import ForgeClient
from test_forge_client_fixtures import page_envelope, respx_router

__all__ = ["respx_router"]


@pytest.mark.parametrize("rate_per_minute", [0, -1, -100])
def test_non_positive_rate_per_minute_raises_value_error(rate_per_minute: int) -> None:
    with pytest.raises(ValueError):
        ForgeClient(rate_per_minute=rate_per_minute)


def test_context_manager_closes_client_on_exit(respx_router: respx.MockRouter) -> None:
    respx_router.get("/mods").mock(
        return_value=httpx.Response(200, json=page_envelope([]))
    )

    with ForgeClient() as client:
        client.mods()  # succeeds while the client is open

    with pytest.raises(Exception):  # noqa: B017 - exact exception type is an
        # implementation detail (raw httpx RuntimeError vs. a wrapped
        # ForgeUnavailableError are both acceptable "closed" signals); what
        # matters is that a request after close() no longer succeeds.
        client.mods()
