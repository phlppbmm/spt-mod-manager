"""ForgeClient construction validation and context-manager lifecycle."""

import httpx
import pytest
import respx

from forge_json import page_envelope
from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.forge.client import ForgeClient

FORGE_URL_ENV_VAR = "SPTMOD_FORGE_URL"


@pytest.mark.parametrize("rate_per_minute", [0, -1, -100])
def test_non_positive_rate_per_minute_raises_value_error(rate_per_minute: int) -> None:
    """ForgeClient construction rejects rate_per_minute <= 0 (0, -1, -100).

    The rate_per_minute parameter must be strictly positive; zero or negative
    values trigger ValueError before any HTTP client is initialized.
    """
    with pytest.raises(ValueError):
        ForgeClient(rate_per_minute=rate_per_minute)


def test_context_manager_closes_client_on_exit(respx_router: respx.MockRouter) -> None:
    """ForgeClient closes its underlying httpx.Client on context exit."""
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope([])))

    with ForgeClient() as client:
        client.mods()  # succeeds while the client is open

    # A request after close() must not succeed. Both the raw httpx
    # RuntimeError and a wrapped ForgeUnavailableError are acceptable
    # "closed" signals.
    with pytest.raises((RuntimeError, ForgeUnavailableError)):
        client.mods()


# --------------------------------------------------------------------------
# SPTMOD_FORGE_URL: overrides the base URL when no explicit one is given.
# --------------------------------------------------------------------------


def test_env_var_sets_the_base_url_when_no_explicit_base_url_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ForgeClient uses SPTMOD_FORGE_URL environment variable as base URL when not explicitly provided."""
    custom_url = "https://custom.forge.invalid/api/v1"
    monkeypatch.setenv(FORGE_URL_ENV_VAR, custom_url)

    with respx.mock(base_url=custom_url, assert_all_called=False) as router:
        route = router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope([])))
        with ForgeClient() as client:
            client.mods()

        assert route.called


def test_default_base_url_is_used_when_env_var_is_unset(
    monkeypatch: pytest.MonkeyPatch, respx_router: respx.MockRouter
) -> None:
    """ForgeClient falls back to default base URL when SPTMOD_FORGE_URL is unset."""
    monkeypatch.delenv(FORGE_URL_ENV_VAR, raising=False)
    route = respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope([])))

    with ForgeClient() as client:
        client.mods()

    assert route.called


def test_explicit_base_url_wins_over_the_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly passed base_url parameter takes precedence over SPTMOD_FORGE_URL."""
    monkeypatch.setenv(FORGE_URL_ENV_VAR, "https://env.forge.invalid/api/v1")
    explicit_url = "https://explicit.forge.invalid/api/v1"

    with respx.mock(base_url=explicit_url, assert_all_called=False) as router:
        route = router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope([])))
        with ForgeClient(base_url=explicit_url) as client:
            client.mods()

        assert route.called
