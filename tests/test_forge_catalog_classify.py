"""Tests for Catalog.classify: joining the cache with installed mods."""

import httpx
import respx

from sptmod.core.forge.catalog import Catalog
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
)

__all__ = [
    "catalog",
    "client_factory",
    "db_path",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]


def _seed_classify_mods(
    client_factory, respx_router: respx.MockRouter, catalog: Catalog
) -> None:
    """Sync four mods used across the classify scenarios.

    - Alpha Mod (id=10): two 3.8-compatible versions, 1.0.0 and 1.1.0 (newer).
    - Beta Mod (id=11): a single 3.8-compatible version, 2.0.0.
    - Gamma Mod (id=12): only a 4.0-compatible version, 1.0.0 (incompatible w/ 3.8.x).
    - Delta Mod (id=13): a single 3.8-compatible version, 1.0.0; never installed.
    - Epsilon Mod (id=14): a 3.8-compatible 1.0.0 and a newer, only 4.0-compat 2.0.0.
    """
    alpha = mod_json_full(
        id=10,
        name="Alpha Mod",
        owner=owner_json(name="Alice"),
        versions=[
            {
                "id": 301,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 302,
                "version": "1.1.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-02-01T00:00:00Z",
            },
        ],
    )
    beta = mod_json_full(
        id=11,
        name="Beta Mod",
        owner=owner_json(name="Bob"),
        versions=[
            {
                "id": 303,
                "version": "2.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            }
        ],
    )
    gamma = mod_json_full(
        id=12,
        name="Gamma Mod",
        owner=owner_json(name="Carol"),
        versions=[
            {
                "id": 304,
                "version": "1.0.0",
                "spt_version_constraint": "^4.0.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            }
        ],
    )
    delta = mod_json_full(
        id=13,
        name="Delta Mod",
        owner=owner_json(name="Dave"),
        versions=[
            {
                "id": 305,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            }
        ],
    )
    epsilon = mod_json_full(
        id=14,
        name="Epsilon Mod",
        owner=owner_json(name="Erin"),
        versions=[
            {
                "id": 306,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 307,
                "version": "2.0.0",
                "spt_version_constraint": "^4.0.0",
                "downloads": 1,
                "published_at": "2025-02-01T00:00:00Z",
            },
        ],
    )
    page = page_envelope(
        [alpha, beta, gamma, delta, epsilon],
        current_page=1,
        last_page=1,
        total=5,
        next_url=None,
    )
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)


def test_classify_matches_installed_mod_by_forge_id_and_detects_update(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_classify_mods(client_factory, respx_router, catalog)
    installed = [
        # Name deliberately does not match "Alpha Mod": the match must come
        # from forge_id, not the name.
        make_mod_info(forge_id="10", name="Totally Different Name", version="1.0.0")
    ]

    entries = catalog.classify(installed)

    alpha_entry = next(e for e in entries if e.mod.id == 10)
    assert alpha_entry.state == "update_available"
    assert alpha_entry.installed_version == "1.0.0"
    assert alpha_entry.latest_version is not None
    assert alpha_entry.latest_version.version == "1.1.0"


def test_classify_matches_installed_mod_by_name_case_insensitively(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_classify_mods(client_factory, respx_router, catalog)
    installed = [
        # forge_id deliberately does not match any cached mod id.
        make_mod_info(forge_id="999", name="beta mod", version="2.0.0")
    ]

    entries = catalog.classify(installed)

    beta_entry = next(e for e in entries if e.mod.id == 11)
    assert beta_entry.state == "installed"
    assert beta_entry.installed_version == "2.0.0"


def test_classify_marks_never_installed_mods_as_available(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_classify_mods(client_factory, respx_router, catalog)

    entries = catalog.classify([])

    delta_entry = next(e for e in entries if e.mod.id == 13)
    assert delta_entry.state == "available"
    assert delta_entry.installed_version is None
    assert delta_entry.latest_version is not None
    assert delta_entry.latest_version.version == "1.0.0"


def test_classify_orders_entries_by_mod_name(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_classify_mods(client_factory, respx_router, catalog)

    entries = catalog.classify([])

    assert [e.mod.name for e in entries] == [
        "Alpha Mod",
        "Beta Mod",
        "Delta Mod",
        "Epsilon Mod",
        "Gamma Mod",
    ]


def test_classify_compatible_with_drops_mods_without_a_compatible_version(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_classify_mods(client_factory, respx_router, catalog)

    entries = catalog.classify([], compatible_with="3.8.5")

    assert 12 not in [e.mod.id for e in entries]  # Gamma Mod: 4.0-only


def test_classify_compatible_with_restricts_update_check_to_compatible_versions(
    catalog: Catalog, client_factory, respx_router: respx.MockRouter
) -> None:
    _seed_classify_mods(client_factory, respx_router, catalog)
    # Epsilon Mod's newest version overall (2.0.0) is only 4.0-compatible;
    # the installed 1.0.0 already is the newest 3.8-compatible version.
    installed = [make_mod_info(forge_id="14", name="Epsilon Mod", version="1.0.0")]

    without_filter = catalog.classify(installed)
    with_filter = catalog.classify(installed, compatible_with="3.8.5")

    epsilon_unfiltered = next(e for e in without_filter if e.mod.id == 14)
    epsilon_filtered = next(e for e in with_filter if e.mod.id == 14)
    assert epsilon_unfiltered.state == "update_available"
    assert epsilon_filtered.state == "installed"
