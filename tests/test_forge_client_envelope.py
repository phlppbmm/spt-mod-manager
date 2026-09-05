"""Envelope unwrapping and error-envelope handling for ForgeClient.

Covers: {"success": true, "data": ...} unwrap for /mods and /mod/{id};
{"success": false, "code", "message"} -> ForgeApiError for 400 and 404;
ping()'s boolean contract.
"""

import httpx
import pytest
import respx

from fixture_types import ClientFactory
from forge_json import error_envelope, mod_json_full, mod_json_minimal, page_envelope
from sptmod.core.errors import ForgeApiError, ForgeUnavailableError
from sptmod.core.forge.client import (
    ForgeCategory,
    ForgeLicense,
    ForgeMod,
    ForgeUser,
    ForgeVersionSummary,
    PageMeta,
    SourceCodeLink,
)


def test_mods_unwraps_success_envelope(client_factory: ClientFactory, respx_router: respx.MockRouter) -> None:
    """mods() extracts items and pagination metadata from a success envelope.

    A 200 response with {"success": true, "data": [...], "meta": {...}, "links": {...}}
    is unwrapped: page.items carries the mod list (id=7, name="Unwrapped Mod" in this
    case), and page.meta contains current_page=1, last_page=1, per_page=50, total=1;
    page.next_url is None for a single-page result.
    """
    respx_router.get("/mods").mock(
        return_value=httpx.Response(200, json=page_envelope([mod_json_minimal(id=7, name="Unwrapped Mod")]))
    )
    client = client_factory()

    page = client.mods()

    assert [m.id for m in page.items] == [7]
    assert [m.name for m in page.items] == ["Unwrapped Mod"]
    assert page.meta == PageMeta(current_page=1, last_page=1, per_page=50, total=1)
    assert page.next_url is None


def test_mod_unwraps_success_envelope(client_factory: ClientFactory, respx_router: respx.MockRouter) -> None:
    """mod() parses nested mod details from an unwrapped success envelope.

    A 200 response to /mod/42 with success=true, data={full mod object} is unwrapped
    to extract the mod, including license, category, versions, description, and
    custom_ai_disclosure when include parameters are requested; all nested objects
    (ForgeUser, ForgeLicense, ForgeCategory, ForgeVersionSummary) are parsed
    correctly (id=42, description="Full description.", etc.).
    """
    respx_router.get("/mod/42").mock(
        return_value=httpx.Response(200, json={"success": True, "data": mod_json_full(id=42)})
    )
    client = client_factory()

    mod = client.mod(42, include=["license", "category", "versions"])

    assert mod == ForgeMod(
        id=42,
        hub_id=100,
        guid="com.example.mod",
        name="Example Mod",
        slug="example-mod",
        teaser="A short teaser.",
        thumbnail="https://cdn.example/thumb.png",
        downloads=1234,
        favourites_count=56,
        owner=ForgeUser(
            id=10,
            name="Alice",
            profile_photo_url="https://cdn.example/alice.png",
            cover_photo_url="https://cdn.example/alice-cover.png",
        ),
        additional_authors=[],
        source_code_links=[SourceCodeLink(url="https://github.com/ex/mod", label="GitHub")],
        detail_url="https://forge.sp-tarkov.com/mods/example-mod",
        fika_compatibility=True,
        featured=False,
        contains_ads=False,
        contains_ai_content=False,
        shows_profile_binding_notice=False,
        published_at="2025-01-01T00:00:00Z",
        created_at="2024-12-01T00:00:00Z",
        updated_at="2025-06-01T00:00:00Z",
        description="Full description.",
        custom_ai_disclosure="Uses AI for translation.",
        license=ForgeLicense(id=1, name="MIT License", link="https://mit.example"),
        category=ForgeCategory(id=5, title="Weapons", slug="weapons"),
        versions=[
            ForgeVersionSummary(
                id=900,
                version="1.0.0",
                spt_version_constraint="^3.8.0",
                downloads=10,
                published_at="2025-01-02T00:00:00Z",
            )
        ],
    )


def test_mods_400_validation_failed_raises_forge_api_error(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """A 400 error envelope raises ForgeApiError with code, message, and status."""
    route = respx_router.get("/mods").mock(
        return_value=httpx.Response(400, json=error_envelope("VALIDATION_FAILED", "per_page must be <= 50"))
    )
    client = client_factory()

    with pytest.raises(ForgeApiError) as exc_info:
        client.mods()

    assert exc_info.value.code == "VALIDATION_FAILED"
    assert exc_info.value.message == "per_page must be <= 50"
    assert exc_info.value.status == 400
    assert route.call_count == 1


def test_mod_404_not_found_raises_forge_api_error(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """A 404 error envelope raises ForgeApiError with code, message, and status."""
    route = respx_router.get("/mod/999").mock(
        return_value=httpx.Response(404, json=error_envelope("NOT_FOUND", "Mod not found"))
    )
    client = client_factory()

    with pytest.raises(ForgeApiError) as exc_info:
        client.mod(999)

    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.message == "Mod not found"
    assert exc_info.value.status == 404
    assert route.call_count == 1


def test_mods_with_a_non_json_body_raises_forge_unavailable(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """mods() raises ForgeUnavailableError when response body is not JSON.

    Even if the HTTP status is 200, if the body cannot be decoded as JSON
    (e.g., raw text "not json at all"), ForgeUnavailableError is raised
    instead of allowing a ValueError to propagate.
    """
    respx_router.get("/mods").mock(return_value=httpx.Response(200, content=b"not json at all"))
    client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        client.mods()


def test_ping_true_when_api_answers(client_factory: ClientFactory, respx_router: respx.MockRouter) -> None:
    """ping() returns True when the /ping endpoint responds with 2xx status.

    A 200 response to /ping (regardless of body content) returns True, signalling
    that the API is reachable. ping() checks response.is_success, not the
    envelope's success field.
    """
    respx_router.get("/ping").mock(return_value=httpx.Response(200, json={"success": True}))
    client = client_factory()

    assert client.ping() is True


def test_ping_false_on_connection_error(client_factory: ClientFactory, respx_router: respx.MockRouter) -> None:
    """ping() returns False when the connection to /ping fails.

    httpx.ConnectError (network unreachable, DNS failure, etc.) is caught and
    returns False instead of propagating, indicating the API is unavailable
    rather than raising an exception.
    """
    respx_router.get("/ping").mock(side_effect=httpx.ConnectError("boom"))
    client = client_factory()

    assert client.ping() is False


def test_ping_false_on_server_error(client_factory: ClientFactory, respx_router: respx.MockRouter) -> None:
    """ping() returns False when the API responds with a 5xx error."""
    respx_router.get("/ping").mock(return_value=httpx.Response(500))
    client = client_factory()

    assert client.ping() is False
