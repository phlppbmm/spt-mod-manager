"""Tests for forge/resolve.py: install-plan resolution over the dependency tree."""

from typing import Any

import httpx
import pytest
import respx

from sptmod.core.errors import UnresolvableDependencyError
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from sptmod.core.forge.resolve import plan_install
from sptmod.core.models import PlanEntry
from test_forge_catalog_fixtures import (
    catalog,
    client_factory,
    db_path,
    fake_clock,
    make_mod_info,
    mod_json_full,
    owner_json,
    page_envelope,
    recording_sleep,
    respx_router,
    success_envelope,
    version_json,
)

__all__ = [
    "catalog",
    "client_factory",
    "db_path",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]

SPT_VERSION = "3.8.5"
SPT_CONSTRAINT = "^3.8.0"


def _seed_target(
    client_factory,
    respx_router: respx.MockRouter,
    catalog: Catalog,
    *,
    mod_id: int = 1,
    name: str = "Target Mod",
    guid: str | None = "com.example.target",
    version: str = "2.0.0",
    version_id: int = 900,
    link: str = "https://dl.example/target-2.0.0.zip",
    content_length: int = 1000,
) -> None:
    """Sync one mod into the catalog and fill in its version's link/content_length.

    Mirrors ``test_fetch_versions_fills_link_and_content_length``: a plain
    ``sync`` only caches the version *summary* (no link/content_length), so a
    ``fetch_versions`` call is needed for those fields to be present.
    """
    mod = mod_json_full(
        id=mod_id,
        name=name,
        guid=guid,
        owner=owner_json(name="Alice"),
        versions=[
            {
                "id": version_id,
                "version": version,
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)

    full_versions_page = page_envelope(
        [
            version_json(
                id=version_id,
                version=version,
                link=link,
                content_length=content_length,
                spt_version_constraint=SPT_CONSTRAINT,
            )
        ],
        current_page=1,
        last_page=1,
        total=1,
        next_url=None,
    )
    respx_router.get(f"/mod/{mod_id}/versions").mock(
        return_value=httpx.Response(200, json=full_versions_page)
    )
    catalog.fetch_versions(client, mod_id)


def _dep_node(
    *,
    id: int,
    name: str,
    guid: str | None = None,
    slug: str | None = None,
    version: str = "1.0.0",
    version_id: int = 500,
    link: str = "https://dl.example/dep.zip",
    content_length: int = 500,
    conflict: bool = False,
    latest_compatible_version: dict[str, Any] | None | Any = "__default__",
    dependencies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the raw JSON of one ``/mods/dependencies`` tree node."""
    if latest_compatible_version == "__default__":
        latest_compatible_version = {
            "id": version_id,
            "version": version,
            "link": link,
            "content_length": content_length,
            "fika_compatibility": "unknown",
        }
    return {
        "id": id,
        "guid": guid,
        "name": name,
        "slug": slug or name.lower().replace(" ", "-"),
        "latest_compatible_version": latest_compatible_version,
        "conflict": conflict,
        "dependencies": dependencies or [],
    }


def _mock_dependencies_response(
    respx_router: respx.MockRouter, nodes: list[dict[str, Any]]
) -> None:
    """Echo the queried ``mods`` pair back as the dict key of the response.

    Per docs/FORGE_API.md (confirmed by
    ``test_dependencies_omits_unresolvable_pairs_and_keeps_empty_list_for_no_deps``
    in ``test_forge_client_parsing.py``), the per-pair value is the list of
    *direct* dependency nodes itself (``[]`` for no dependencies) -- there is
    no extra node wrapping the queried mod. ``ForgeClient.dependencies`` keys
    its result dict by the queried ``identifier:version`` pair verbatim;
    echoing the request's own ``mods`` param back avoids hard-coding which
    identifier (numeric ID vs. GUID) ``plan_install`` chooses to query with.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json=success_envelope({pair: nodes}))

    respx_router.get("/mods/dependencies").mock(side_effect=_handler)


def _mock_empty_dependencies_response(respx_router: respx.MockRouter) -> None:
    """Mock a response that omits every queried pair, including the target's."""
    respx_router.get("/mods/dependencies").mock(
        return_value=httpx.Response(200, json=success_envelope({}))
    )


def test_plan_install_orders_dependency_before_the_requested_mod(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(id=2, name="Dep Mod", guid="com.example.dep")
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()

    plan = plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert plan.spt_version == SPT_VERSION
    assert plan.entries == [
        PlanEntry(
            mod_id=2,
            name="Dep Mod",
            guid="com.example.dep",
            version="1.0.0",
            version_id=500,
            link="https://dl.example/dep.zip",
            content_length=500,
            is_dependency=True,
        ),
        PlanEntry(
            mod_id=1,
            name="Target Mod",
            guid="com.example.target",
            version="2.0.0",
            version_id=900,
            link="https://dl.example/target-2.0.0.zip",
            content_length=1000,
            is_dependency=False,
        ),
    ]


def test_plan_install_skips_dependency_already_satisfied_by_forge_id(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(id=2, name="Dep Mod", guid="com.example.dep", version="1.0.0")
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()
    installed = [make_mod_info(forge_id="2", name="Some Other Name", version="1.0.0")]

    plan = plan_install(1, "2.0.0", catalog, client, installed, SPT_VERSION)

    assert [entry.mod_id for entry in plan.entries] == [1]


def test_plan_install_skips_dependency_already_satisfied_by_name_without_forge_id(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(id=2, name="Dep Mod", guid=None, version="1.0.0")
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()
    installed = [make_mod_info(forge_id="", name="dep mod", version="1.0.0")]

    plan = plan_install(1, "2.0.0", catalog, client, installed, SPT_VERSION)

    assert [entry.mod_id for entry in plan.entries] == [1]


def test_plan_install_includes_outdated_installed_dependency(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(id=2, name="Dep Mod", guid="com.example.dep", version="2.0.0")
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()
    installed = [make_mod_info(forge_id="2", name="Dep Mod", version="1.0.0")]

    plan = plan_install(1, "2.0.0", catalog, client, installed, SPT_VERSION)

    dep_entries = [entry for entry in plan.entries if entry.is_dependency]
    assert [entry.mod_id for entry in dep_entries] == [2]
    assert dep_entries[0].version == "2.0.0"
    assert [entry.mod_id for entry in plan.entries] == [2, 1]


def test_plan_install_orders_a_transitive_dependency_chain(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep_b = _dep_node(id=3, name="Dep B", version="1.0.0")
    dep_a = _dep_node(id=2, name="Dep A", version="1.0.0", dependencies=[dep_b])
    _mock_dependencies_response(respx_router, [dep_a])
    client = client_factory()

    plan = plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert [entry.name for entry in plan.entries] == ["Dep B", "Dep A", "Target Mod"]
    assert [entry.is_dependency for entry in plan.entries] == [True, True, False]


def test_plan_install_diamond_dependency_appears_once_before_its_dependents(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    """A -> {B, C}, both B and C -> D: D must be planned once, before B and C."""
    _seed_target(client_factory, respx_router, catalog)
    dep_d_via_b = _dep_node(id=5, name="Dep D")
    dep_d_via_c = _dep_node(id=5, name="Dep D")
    dep_b = _dep_node(id=3, name="Dep B", dependencies=[dep_d_via_b])
    dep_c = _dep_node(id=4, name="Dep C", dependencies=[dep_d_via_c])
    dep_a = _dep_node(id=2, name="Dep A", dependencies=[dep_b, dep_c])
    _mock_dependencies_response(respx_router, [dep_a])
    client = client_factory()

    plan = plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert [entry.name for entry in plan.entries] == [
        "Dep D",
        "Dep B",
        "Dep C",
        "Dep A",
        "Target Mod",
    ]
    assert [entry.mod_id for entry in plan.entries].count(5) == 1


def test_plan_install_target_pair_omitted_from_response_raises_naming_the_target(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    _mock_empty_dependencies_response(respx_router)
    client = client_factory()

    with pytest.raises(UnresolvableDependencyError) as excinfo:
        plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert excinfo.value.dependency == "Target Mod"


def test_plan_install_installed_dependency_with_unparseable_version_is_not_satisfied(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(id=2, name="Dep Mod", guid="com.example.dep", version="1.0.0")
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()
    installed = [make_mod_info(forge_id="2", name="Dep Mod", version="dev-build")]

    plan = plan_install(1, "2.0.0", catalog, client, installed, SPT_VERSION)

    assert [entry.mod_id for entry in plan.entries if entry.is_dependency] == [2]


def test_plan_install_conflicting_dependency_raises_naming_it(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(id=2, name="Conflicted Dep", conflict=True)
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()

    with pytest.raises(UnresolvableDependencyError) as excinfo:
        plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert excinfo.value.dependency == "Conflicted Dep"


def test_plan_install_null_compatible_version_raises_naming_the_dependency(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    dep = _dep_node(
        id=2, name="No Version Dep", latest_compatible_version=None, conflict=False
    )
    _mock_dependencies_response(respx_router, [dep])
    client = client_factory()

    with pytest.raises(UnresolvableDependencyError) as excinfo:
        plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert excinfo.value.dependency == "No Version Dep"


def test_plan_install_dependency_cycle_raises_naming_the_dependency(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_target(client_factory, respx_router, catalog)
    # "Dep A" (id=2) reappears on its own path -> a cycle.
    dep_a_again = _dep_node(id=2, name="Dep A", dependencies=[])
    dep_a = _dep_node(id=2, name="Dep A", dependencies=[dep_a_again])
    _mock_dependencies_response(respx_router, [dep_a])
    client = client_factory()

    with pytest.raises(UnresolvableDependencyError) as excinfo:
        plan_install(1, "2.0.0", catalog, client, [], SPT_VERSION)

    assert excinfo.value.dependency == "Dep A"


def test_plan_install_unknown_mod_raises_no_compatible_version_error(
    catalog: Catalog, client_factory
) -> None:
    client = client_factory()

    with pytest.raises(NoCompatibleVersionError):
        plan_install(99999, None, catalog, client, [], SPT_VERSION)


def test_plan_install_without_wanted_version_resolves_the_highest_compatible(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    mod = mod_json_full(
        id=1,
        name="Target Mod",
        guid="com.example.target",
        owner=owner_json(name="Alice"),
        versions=[
            {
                "id": 901,
                "version": "1.0.0",
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 902,
                "version": "2.0.0",
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-02-01T00:00:00Z",
            },
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)
    full_versions_page = page_envelope(
        [
            version_json(
                id=901,
                version="1.0.0",
                link="https://dl.example/target-1.0.0.zip",
                content_length=100,
                spt_version_constraint=SPT_CONSTRAINT,
            ),
            version_json(
                id=902,
                version="2.0.0",
                link="https://dl.example/target-2.0.0.zip",
                content_length=200,
                spt_version_constraint=SPT_CONSTRAINT,
            ),
        ],
        current_page=1,
        last_page=1,
        total=2,
        next_url=None,
    )
    respx_router.get("/mod/1/versions").mock(
        return_value=httpx.Response(200, json=full_versions_page)
    )
    catalog.fetch_versions(client, 1)
    _mock_dependencies_response(respx_router, [])

    plan = plan_install(1, None, catalog, client, [], SPT_VERSION)

    assert [entry.version for entry in plan.entries] == ["2.0.0"]
