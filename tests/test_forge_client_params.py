"""Request building: query/filter[...]/sort/include serialization on the wire."""

from collections.abc import Callable

import httpx
import pytest
import respx

from fixture_types import ClientFactory, JsonDict
from fixtures_forge import recorded_requests
from forge_json import page_envelope, success_envelope
from sptmod.core.forge.client import ForgeClient

type ModPairs = list[tuple[str, str]]
type BulkCall = Callable[[ForgeClient, ModPairs], None]


def test_mods_serializes_query_filters_sort_include_fields(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """mods() serializes query, filters, sort, include, and fields into wire params.

    With query="terminator", filters={name:"abc", featured:"true"}, sort="-updated_at,name",
    include=["versions", "license"], fields=["id", "name"], page=2, per_page=25,
    the HTTP request carries query=terminator, filter[name]=abc, filter[featured]=true,
    sort=-updated_at,name, include=versions,license, fields=id,name, page=2, per_page=25.
    """
    route = respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope([])))
    client = client_factory()

    client.mods(
        query="terminator",
        filters={"name": "abc", "featured": "true"},
        sort="-updated_at,name",
        include=["versions", "license"],
        fields=["id", "name"],
        page=2,
        per_page=25,
    )

    params = recorded_requests(route)[0].url.params
    assert params["query"] == "terminator"
    assert params["filter[name]"] == "abc"
    assert params["filter[featured]"] == "true"
    assert params["sort"] == "-updated_at,name"
    assert params["include"] == "versions,license"
    assert params["fields"] == "id,name"
    assert params["page"] == "2"
    assert params["per_page"] == "25"


def test_mod_versions_serializes_filters_and_sort(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """mod_versions() encodes filters, sort, and include into request params."""
    route = respx_router.get("/mod/42/versions").mock(return_value=httpx.Response(200, json=page_envelope([])))
    client = client_factory()

    client.mod_versions(
        42,
        filters={"version": "^1.0.0", "fika_compatibility": "compatible"},
        sort="-published_at",
        include=["dependencies"],
    )

    params = recorded_requests(route)[0].url.params
    assert params["filter[version]"] == "^1.0.0"
    assert params["filter[fika_compatibility]"] == "compatible"
    assert params["sort"] == "-published_at"
    assert params["include"] == "dependencies"


_UPDATES_RESPONSE: JsonDict = {
    "spt_version": "3.11.5",
    "updates": [],
    "blocked_updates": [],
    "up_to_date": [],
    "incompatible_with_spt": [],
}


def _call_updates(client: ForgeClient, mods: ModPairs) -> None:
    """Ask the client for updates of ``mods`` against a fixed SPT version."""
    client.updates(mods=mods, spt_version="3.11.5")


def _call_dependencies(client: ForgeClient, mods: ModPairs) -> None:
    """Ask the client for the dependencies of ``mods`` against a fixed SPT version."""
    client.dependencies(mods=mods, spt_version="3.11.5")


_MOD_PAIR_CASES = [
    pytest.param(
        "/mods/updates",
        _UPDATES_RESPONSE,
        _call_updates,
        [("5", "1.2.0"), ("com.example.mod", "2.0.5")],
        "5:1.2.0,com.example.mod:2.0.5",
        id="updates-two-pairs",
    ),
    pytest.param(
        "/mods/dependencies",
        {},
        _call_dependencies,
        [("5", "1.2.0"), ("com.example.mod", "2.0.5")],
        "5:1.2.0,com.example.mod:2.0.5",
        id="dependencies-two-pairs",
    ),
    pytest.param(
        "/mods/updates",
        _UPDATES_RESPONSE,
        _call_updates,
        [("5", "1.2.0")],
        "5:1.2.0",
        id="updates-single-pair",
    ),
]


@pytest.mark.parametrize(("path", "response_data", "call", "mods", "expected_mods"), _MOD_PAIR_CASES)
def test_mod_pairs_are_sent_as_comma_joined_id_colon_version_pairs(
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    path: str,
    response_data: JsonDict,
    call: BulkCall,
    mods: ModPairs,
    expected_mods: str,
) -> None:
    """Both bulk endpoints send their mod list as ``id:version`` pairs joined by commas.

    Calls ``updates`` and ``dependencies`` with a mod list and the SPT version and
    asserts the ``mods`` and ``spt_version`` query parameters on the recorded
    request. Reason: identifiers may be numeric ids or GUIDs, several mods are
    separated by a comma and a single mod must not gain a trailing one, because
    the API rejects a malformed list.
    """
    route = respx_router.get(path).mock(return_value=httpx.Response(200, json=success_envelope(response_data)))
    client = client_factory()

    call(client, mods)

    params = recorded_requests(route)[0].url.params
    assert params["mods"] == expected_mods
    assert params["spt_version"] == "3.11.5"
