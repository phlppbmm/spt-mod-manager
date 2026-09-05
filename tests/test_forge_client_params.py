"""Request building: query/filter[...]/sort/include serialization on the wire."""

import httpx
import respx

from fixture_types import ClientFactory
from forge_json import page_envelope, success_envelope


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

    params = route.calls[0].request.url.params
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

    params = route.calls[0].request.url.params
    assert params["filter[version]"] == "^1.0.0"
    assert params["filter[fika_compatibility]"] == "compatible"
    assert params["sort"] == "-published_at"
    assert params["include"] == "dependencies"


def test_updates_serializes_mods_as_colon_pairs_and_spt_version(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """updates() serializes mod list as colon-separated id:version pairs.

    Mods [("5", "1.2.0"), ("com.example.mod", "2.0.5")] and spt_version="3.11.5"
    produce wire params mods="5:1.2.0,com.example.mod:2.0.5" and spt_version="3.11.5";
    identifiers can be numeric IDs (as strings) or GUIDs, and are joined with commas.
    """
    minimal_data = {
        "spt_version": "3.11.5",
        "updates": [],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    route = respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(200, json=success_envelope(minimal_data))
    )
    client = client_factory()

    client.updates(mods=[("5", "1.2.0"), ("com.example.mod", "2.0.5")], spt_version="3.11.5")

    params = route.calls[0].request.url.params
    assert params["mods"] == "5:1.2.0,com.example.mod:2.0.5"
    assert params["spt_version"] == "3.11.5"


def test_dependencies_serializes_mods_as_colon_pairs_and_spt_version(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """dependencies() serializes mod list as colon-separated id:version pairs.

    Mods [("5", "1.2.0"), ("com.example.mod", "2.0.5")] and spt_version="3.11.5"
    produce wire params mods="5:1.2.0,com.example.mod:2.0.5" and spt_version="3.11.5";
    identifiers can be numeric IDs (as strings) or GUIDs, and are joined with commas.
    """
    route = respx_router.get("/mods/dependencies").mock(return_value=httpx.Response(200, json=success_envelope({})))
    client = client_factory()

    client.dependencies(mods=[("5", "1.2.0"), ("com.example.mod", "2.0.5")], spt_version="3.11.5")

    params = route.calls[0].request.url.params
    assert params["mods"] == "5:1.2.0,com.example.mod:2.0.5"
    assert params["spt_version"] == "3.11.5"


def test_updates_serializes_single_pair_without_trailing_comma(
    client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """updates() serializes a single mod pair without a trailing comma.

    A single-element mod list [("5", "1.2.0")] produces mods="5:1.2.0" (no comma),
    not "5:1.2.0," (which would be incorrect and rejected by the API).
    """
    minimal_data = {
        "spt_version": "3.11.5",
        "updates": [],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    route = respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(200, json=success_envelope(minimal_data))
    )
    client = client_factory()

    client.updates(mods=[("5", "1.2.0")], spt_version="3.11.5")

    assert route.calls[0].request.url.params["mods"] == "5:1.2.0"
