"""Tests for ``services.get_mod_details``.

The cached mod row and its versions come from the local catalog; the
description is fetched live from the Forge detail endpoint and reduced from
HTML to plain text. An unreachable Forge degrades to the cached data with an
empty description, unless nothing is cached at all, in which case it raises.
"""

from pathlib import Path

import httpx
import pytest
import respx

from sptmod.core import services
from sptmod.core.errors import ForgeUnavailableError
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient
from test_forge_catalog_fixtures import (
    client_factory,
    db_path,
    fake_clock,
    mod_json_full,
    owner_json,
    page_envelope,
    recording_sleep,
    respx_router,
    success_envelope,
    version_json,
)

__all__ = [
    "client_factory",
    "db_path",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]

# An HTML fragment exercising every branch of _HtmlTextExtractor: a <p> tag
# (newline break), inline markup that is dropped but keeps its text, a
# <script> block whose text must not appear, and a <style> block likewise.
_DESCRIPTION_HTML = (
    "<p>Hello <b>world</b>.</p>"
    "<script>var x = 1;</script>"
    "<style>.a{color:red}</style>"
    "<p>Second paragraph.</p>"
)
_DESCRIPTION_TEXT = "Hello world.\nSecond paragraph."


def _mod_summary_json(mod_id: int, name: str) -> dict[str, object]:
    """A mod as returned by the /mods list endpoint (no version link)."""
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name="Author"),
        versions=[
            {
                "id": mod_id * 100,
                "version": "1.0.0",
                "spt_version_constraint": "^4.0.0",
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )


def _mod_detail_json(mod_id: int, name: str, description: str) -> dict[str, object]:
    """A mod as returned by the /mod/{id} detail endpoint."""
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name="Author"),
        description=description,
    )


def _seed(
    db_path: Path,
    client: ForgeClient,
    respx_router: respx.MockRouter,
    mods: list[dict[str, object]],
) -> None:
    """Populate ``db_path`` through the real sync path, mocking one /mods page."""
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                mods, current_page=1, last_page=1, total=len(mods), next_url=None
            ),
        )
    )
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)


def test_get_mod_details_strips_html_and_fetches_missing_version_links(
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If get_mod_details' body is `pass`, this fails: returns None instead of
    ModDetails with the cached mod, the fetched version link and plain text."""
    client = client_factory()
    _seed(db_path, client, respx_router, [_mod_summary_json(1, "Alpha Mod")])
    respx_router.get("/mod/1").mock(
        return_value=httpx.Response(
            200,
            json=success_envelope(
                _mod_detail_json(1, "Alpha Mod", _DESCRIPTION_HTML)
            ),
        )
    )
    respx_router.get("/mod/1/versions").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                [version_json(id=100, link="https://cdn.example/alpha-1.0.0.zip")],
                current_page=1,
                last_page=1,
                total=1,
                next_url=None,
            ),
        )
    )

    result = services.get_mod_details(1, db_path=db_path, client=client)

    assert result.mod is not None
    assert result.mod.name == "Alpha Mod"
    assert result.description == _DESCRIPTION_TEXT
    assert [version.link for version in result.versions] == [
        "https://cdn.example/alpha-1.0.0.zip"
    ]


@pytest.mark.parametrize("use_client", [True, False], ids=["injected", "owned"])
def test_get_mod_details_skips_refetching_versions_that_already_have_a_link(
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    use_client: bool,
) -> None:
    """If get_mod_details' body is `pass`, this fails: catalog.fetch_versions
    is called an extra time, which would 404 since the route is unmocked
    beyond the one setup call."""
    seed_client = client_factory()
    _seed(db_path, seed_client, respx_router, [_mod_summary_json(2, "Beta Mod")])
    versions_route = respx_router.get("/mod/2/versions").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                [version_json(id=200, link="https://cdn.example/beta-1.0.0.zip")],
                current_page=1,
                last_page=1,
                total=1,
                next_url=None,
            ),
        )
    )
    with Catalog(db_path) as cat:
        cat.fetch_versions(seed_client, 2)
    assert versions_route.call_count == 1

    respx_router.get("/mod/2").mock(
        return_value=httpx.Response(
            200, json=success_envelope(_mod_detail_json(2, "Beta Mod", "<p>Text.</p>"))
        )
    )
    call_client = seed_client if use_client else None

    result = services.get_mod_details(2, db_path=db_path, client=call_client)

    assert result.description == "Text."
    assert [version.link for version in result.versions] == [
        "https://cdn.example/beta-1.0.0.zip"
    ]
    assert versions_route.call_count == 1


def test_get_mod_details_owned_client_fetches_missing_version_links(
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """Same as the injected-client happy path, but exercised through the
    branch that builds its own ForgeClient (``client=None`` is the default).
    If get_mod_details' body is `pass`, this fails: returns None."""
    seed_client = client_factory()
    _seed(db_path, seed_client, respx_router, [_mod_summary_json(3, "Gamma Mod")])
    respx_router.get("/mod/3").mock(
        return_value=httpx.Response(
            200,
            json=success_envelope(_mod_detail_json(3, "Gamma Mod", "<p>Owned.</p>")),
        )
    )
    respx_router.get("/mod/3/versions").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                [version_json(id=300, link="https://cdn.example/gamma-1.0.0.zip")],
                current_page=1,
                last_page=1,
                total=1,
                next_url=None,
            ),
        )
    )

    result = services.get_mod_details(3, db_path=db_path)

    assert result.description == "Owned."
    assert [version.link for version in result.versions] == [
        "https://cdn.example/gamma-1.0.0.zip"
    ]


def test_get_mod_details_degrades_to_cached_data_when_forge_is_unreachable(
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If the except ForgeUnavailableError branch were removed, this fails:
    the ConnectError would propagate instead of degrading to the cache."""
    client = client_factory()
    _seed(db_path, client, respx_router, [_mod_summary_json(4, "Delta Mod")])
    respx_router.get("/mod/4").mock(side_effect=httpx.ConnectError("boom"))

    result = services.get_mod_details(4, db_path=db_path, client=client)

    assert result.mod is not None
    assert result.mod.name == "Delta Mod"
    assert result.description == ""


def test_get_mod_details_reraises_when_nothing_is_cached_and_forge_is_unreachable(
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If `if cached is None: raise` were dropped, this fails: a ModDetails
    with mod=None would be returned instead of ForgeUnavailableError."""
    client = client_factory()
    respx_router.get("/mod/999").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(ForgeUnavailableError):
        services.get_mod_details(999, db_path=db_path, client=client)
