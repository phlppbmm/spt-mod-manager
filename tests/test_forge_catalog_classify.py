"""Tests for Catalog.classify: joining the cache with installed mods."""

import httpx
import respx

from fixture_types import ClientFactory
from forge_json import make_mod_info, mod_json_full, owner_json, page_envelope
from sptmod.core.forge.catalog import Catalog


def _seed_classify_mods(client_factory: ClientFactory, respx_router: respx.MockRouter, catalog: Catalog) -> None:
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
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """classify() matches by forge_id, then detects available updates.

    Installed mod with forge_id="10" but different name ("Totally Different")
    matches the cached Alpha Mod (id=10, versions 1.0.0 and 1.1.0) by id, not
    name. Result: state="update_available", installed_version="1.0.0",
    latest_version="1.1.0".
    """
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
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """classify() falls back to name matching when forge_id missing.

    Installed mod with forge_id="999" (no match) and name="beta mod" matches
    cached Beta Mod (id=11) by name case-insensitively. Result:
    state="installed", installed_version="2.0.0".
    """
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
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """classify([]) on empty installed list marks all catalog mods "available".

    Delta Mod (id=13) is in the catalog but not installed, so it appears with
    state="available", installed_version=None; this distinguishes "available to
    install" from "update_available" (already installed).
    """
    _seed_classify_mods(client_factory, respx_router, catalog)

    entries = catalog.classify([])

    delta_entry = next(e for e in entries if e.mod.id == 13)
    assert delta_entry.state == "available"
    assert delta_entry.installed_version is None
    assert delta_entry.latest_version is not None
    assert delta_entry.latest_version.version == "1.0.0"


def test_classify_orders_entries_by_mod_name(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """classify() returns all entries sorted by name alphabetically.

    Five seeded mods are returned in order (Alpha, Beta, Delta, Epsilon,
    Gamma), not database iteration order; this ensures UI displays are
    consistent regardless of how the catalog fetches rows.
    """
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
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """classify(compatible_with="3.8.5") excludes mods with no 3.8-compatible version.

    Gamma Mod (id=12) has only a 4.0-compatible version, so it is excluded
    from the result when compatible_with="3.8.5".
    """
    _seed_classify_mods(client_factory, respx_router, catalog)

    entries = catalog.classify([], compatible_with="3.8.5")

    assert 12 not in [e.mod.id for e in entries]  # Gamma Mod: 4.0-only


def test_classify_compatible_with_restricts_update_check_to_compatible_versions(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """classify(compatible_with) filters update candidates to fitting versions.

    Epsilon Mod installed as 1.0.0 (3.8-compatible) has newer version 2.0.0
    (4.0-only). Without filter, state="update_available"; with
    compatible_with="3.8.5", state="installed" (1.0.0 is the newest 3.8 fit).
    """
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
