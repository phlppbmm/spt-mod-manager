"""Tests for Catalog.get_mod, find_mod, search, get_versions and resolve_version."""

import httpx
import pytest
import respx

from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from test_forge_catalog_fixtures import (
    ClientFactory,
    catalog,
    client_factory,
    db_path,
    fake_clock,
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


def _seed_search_mods(
    client_factory: ClientFactory, respx_router: respx.MockRouter, catalog: Catalog
) -> None:
    """Sync three mods: two compatible with 3.8.x, one only with 4.x."""
    alpha = mod_json_full(
        id=1,
        name="Alpha Mod",
        slug="alpha-mod",
        owner=owner_json(name="Alice"),
        updated_at="2025-06-01T00:00:00Z",
        versions=[
            {
                "id": 901,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    beta = mod_json_full(
        id=2,
        name="Beta Mod",
        slug="beta-mod",
        owner=owner_json(name="Bob"),
        updated_at="2025-06-02T00:00:00Z",
        versions=[
            {
                "id": 902,
                "version": "2.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    gamma = mod_json_full(
        id=3,
        name="Gamma Mod",
        slug="gamma-mod",
        owner=owner_json(name="Carol"),
        updated_at="2025-06-03T00:00:00Z",
        versions=[
            {
                "id": 903,
                "version": "1.0.0",
                "spt_version_constraint": "^4.0.0",
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope(
        [alpha, beta, gamma], current_page=1, last_page=1, total=3, next_url=None
    )
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)


def test_get_mod_returns_the_cached_mod(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Seeding three mods into the catalog, get_mod(2) returns Beta Mod
    with all cached fields (id, name, author) intact; this verifies the cache
    round-trips mod metadata correctly and the numeric-id lookup path works.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    mod = catalog.get_mod(2)

    assert mod is not None
    assert mod.id == 2
    assert mod.name == "Beta Mod"
    assert mod.author == "Bob"


def test_get_mod_returns_none_for_unknown_id(catalog: Catalog) -> None:
    """An empty cache returns None for any lookup; this guards against callers
    accidentally treating an absent mod lookup as an error instead of a miss.
    """
    assert catalog.get_mod(99999) is None


def test_find_mod_matches_name_case_insensitively(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """After seeding three mods, find_mod("aLpHa MoD") returns the Alpha Mod
    record despite differing case; this guards against case-sensitive string
    comparison breaking mod lookups from user input.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    mod = catalog.find_mod("aLpHa MoD")

    assert mod is not None
    assert mod.id == 1


def test_find_mod_returns_none_when_no_name_matches(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """After seeding three mods, find_mod() for a name not in the cache
    returns None; this ensures failed lookups do not raise an exception.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    assert catalog.find_mod("Does Not Exist") is None


def test_search_matches_name_case_insensitively(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Search with all-caps "ALPHA" returns only the mod containing that
    substring (Alpha Mod, id=1), confirming case-insensitive substring matching
    in mod names.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    results = catalog.search("ALPHA")

    assert [m.id for m in results] == [1]


def test_search_matches_author_case_insensitively(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Search with lowercase "bob" returns the Beta Mod (id=2) whose author
    is "Bob"; this verifies search queries also apply to mod author fields
    with case-insensitive matching.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    results = catalog.search("bob")

    assert [m.id for m in results] == [2]


def test_search_with_empty_query_returns_every_mod_ordered_by_name(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """An empty search query returns all three seeded mods in alphabetical order
    (Alpha, Beta, Gamma); this guards against the common "empty = error" bug
    and verifies that no filter is the same as matching everything.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    results = catalog.search("")

    assert [m.name for m in results] == ["Alpha Mod", "Beta Mod", "Gamma Mod"]


def test_search_with_no_match_returns_empty_list(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """A search query matching nothing in a populated cache returns an empty
    list rather than None or raising; this guards against None-handling bugs
    when displaying search results.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    assert catalog.search("no-such-mod-anywhere") == []


def test_search_compatible_with_excludes_mods_without_a_compatible_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """When searching for "mod" with compatible_with="3.8.5", Gamma Mod (which
    only has a 4.0-compatible version) is excluded from results; this ensures
    the SPT version filter is applied to search results, not just direct lookups.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    results = catalog.search("mod", compatible_with="3.8.5")

    assert [m.id for m in results] == [1, 2]


def _seed_versions_for_resolve(
    client_factory: ClientFactory, respx_router: respx.MockRouter, catalog: Catalog
) -> None:
    """Sync one mod ("Alpha Mod", id=1) with three versions of varying compatibility."""
    mod = mod_json_full(
        id=1,
        name="Alpha Mod",
        slug="alpha-mod",
        owner=owner_json(name="Alice"),
        updated_at="2025-06-01T00:00:00Z",
        versions=[
            {
                "id": 201,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 202,
                "version": "1.1.0",
                "spt_version_constraint": "^3.9.0",
                "downloads": 1,
                "published_at": "2025-02-01T00:00:00Z",
            },
            {
                "id": 203,
                "version": "2.0.0",
                "spt_version_constraint": "^4.0.0",
                "downloads": 1,
                "published_at": "2025-03-01T00:00:00Z",
            },
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)


def test_get_versions_orders_by_published_at_desc_then_id_desc(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Catalog.get_versions returns versions ordered by published_at DESC, tiebroken by id DESC."""
    mod = mod_json_full(
        id=1,
        name="Alpha Mod",
        owner=owner_json(name="Alice"),
        versions=[
            {
                "id": 10,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 20,
                "version": "1.0.1",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": 5,
                "version": "0.9.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 1,
                "published_at": "2024-12-01T00:00:00Z",
            },
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)

    versions = catalog.get_versions(1)

    # Same published_at for id=10 and id=20: tiebreak by id DESC (20 before 10).
    assert [v.id for v in versions] == [20, 10, 5]


def test_get_versions_compatible_with_filters_out_incompatible_versions(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """When Alpha Mod has three versions (1.0.0 for 3.8, 1.1.0 for 3.9, 2.0.0
    for 4.0), get_versions(1, compatible_with="3.8.5") returns only 1.0.0;
    this ensures the version list is filtered by SPT compatibility, not returned raw.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    versions = catalog.get_versions(1, compatible_with="3.8.5")

    assert [v.id for v in versions] == [201]


def test_resolve_version_picks_newest_by_default(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """With three versions cached (1.0.0, 1.1.0, 2.0.0), resolve_version(1)
    with no constraints returns 2.0.0; this ensures the default is the
    semantically newest version, not the newest published or any other sort.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    version = catalog.resolve_version(1)

    assert version.version == "2.0.0"


def test_resolve_version_picks_newest_compatible_with_spt_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """With three versions cached for different SPT ranges, resolve_version(1,
    spt_version="3.8.5") returns 1.0.0, the newest version compatible with 3.8;
    this verifies the compatibility filter narrows before picking the newest.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    version = catalog.resolve_version(1, spt_version="3.8.5")

    assert version.version == "1.0.0"


def test_resolve_version_honours_explicit_wanted_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """With three versions cached (1.0.0, 1.1.0, 2.0.0), resolve_version(1,
    wanted="1.1.0") returns exactly version id=202 (1.1.0), not the newest;
    this ensures wanted version pinning overrides default newest-version logic.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    version = catalog.resolve_version(1, wanted="1.1.0")

    assert version.id == 202


def test_resolve_version_by_name_case_insensitive(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """resolve_version("alpha mod") with lowercase name finds the Alpha Mod mod
    and returns version 2.0.0; this ensures the name-based lookup path works
    and applies the same newest-version logic as numeric-id lookup.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    version = catalog.resolve_version("alpha mod")

    assert version.version == "2.0.0"


def test_resolve_version_raises_for_unknown_mod(catalog: Catalog) -> None:
    """An empty cache looking up a nonexistent mod id raises
    NoCompatibleVersionError, not KeyError or returning None; this prevents
    callers from handling lookup failure as an exception-free miss.
    """
    with pytest.raises(NoCompatibleVersionError):
        catalog.resolve_version(99999)


def test_resolve_version_raises_for_unknown_wanted_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """When three versions are cached but wanted="9.9.9" is not among them,
    resolve_version raises NoCompatibleVersionError; this prevents callers from
    accidentally getting a different version as a fallback.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    with pytest.raises(NoCompatibleVersionError):
        catalog.resolve_version(1, wanted="9.9.9")


def test_resolve_version_raises_when_no_version_is_compatible(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """With versions for SPT 3.8, 3.9, and 4.0, requesting SPT 5.0.0 raises
    NoCompatibleVersionError; this ensures version mismatches fail rather than
    returning the newest version regardless of compatibility.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    with pytest.raises(NoCompatibleVersionError):
        catalog.resolve_version(1, spt_version="5.0.0")


def _sync_single_mod_with_versions(
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    catalog: Catalog,
    mod_id: int,
    name: str,
    versions: list[dict[str, object]],
) -> None:
    """Sync a single mod with specified versions into the catalog."""
    mod = mod_json_full(
        id=mod_id, name=name, owner=owner_json(name="Alice"), versions=versions
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)


def _version_row(version_id: int, version: str, published_at: str) -> dict[str, object]:
    """Build a version dict row with given id, version string, and published_at timestamp."""
    return {
        "id": version_id,
        "version": version,
        "spt_version_constraint": "^3.8.0",
        "downloads": 1,
        "published_at": published_at,
    }


@pytest.mark.parametrize(
    ("versions", "expected_id"),
    [
        # 2.0.0 was published *before* the 1.9.1 hotfix; get_versions() is
        # explicitly published_at-ordered (not semantic), but resolve_version's
        # default pick must be the semantically newest version, 2.0.0.
        (
            [
                _version_row(401, "2.0.0", "2025-01-01T00:00:00Z"),
                _version_row(402, "1.9.1", "2025-02-01T00:00:00Z"),
            ],
            401,
        ),
        # "nightly-build" cannot be compared semantically and is published
        # after 2.0.0; the parseable highest version must still win.
        (
            [
                _version_row(501, "2.0.0", "2025-01-01T00:00:00Z"),
                _version_row(502, "nightly-build", "2025-03-01T00:00:00Z"),
            ],
            501,
        ),
        # Nothing is parseable, so the newest published_at wins.
        (
            [
                _version_row(601, "nightly-a", "2025-01-01T00:00:00Z"),
                _version_row(602, "nightly-b", "2025-02-01T00:00:00Z"),
            ],
            602,
        ),
    ],
    ids=[
        "semantic-order-beats-published-at",
        "unparseable-ignored-when-parseable-exists",
        "published-at-fallback-when-all-unparseable",
    ],
)
def test_resolve_version_default_pick(
    catalog: Catalog,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    versions: list[dict[str, object]],
    expected_id: int,
) -> None:
    """Catalog.resolve_version prefers semantically-parseable versions over publication order."""
    _sync_single_mod_with_versions(
        client_factory,
        respx_router,
        catalog,
        mod_id=1,
        name="Alpha Mod",
        versions=versions,
    )

    version = catalog.resolve_version(1)

    assert version.id == expected_id


def test_search_matches_unicode_name_and_author_via_casefold(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Catalog.search handles Unicode characters in mod names and authors correctly."""
    mod = mod_json_full(
        id=1,
        name="Örakel",
        owner=owner_json(name="Müller"),
        versions=[],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)

    by_name = catalog.search("örakel")
    by_author = catalog.search("MÜLLER")

    assert [m.id for m in by_name] == [1]
    assert [m.id for m in by_author] == [1]


def test_find_mod_matches_unicode_name_via_casefold(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Catalog.find_mod handles Unicode characters in mod names correctly."""
    mod = mod_json_full(
        id=1, name="Örakel", owner=owner_json(name="Müller"), versions=[]
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)

    mod_found = catalog.find_mod("ÖRAKEL")

    assert mod_found is not None
    assert mod_found.id == 1


def test_search_treats_percent_and_underscore_as_literal_characters(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Catalog.search treats SQL wildcard characters as literals, not patterns."""
    # SQL LIKE treats "_" as "match any single character"; an unescaped
    # search must not let "a_b" accidentally match an unrelated "aXb" mod.
    literal = mod_json_full(
        id=1, name="a_b", owner=owner_json(name="Alice"), versions=[]
    )
    decoy = mod_json_full(id=2, name="aXb", owner=owner_json(name="Bob"), versions=[])
    page = page_envelope(
        [literal, decoy], current_page=1, last_page=1, total=2, next_url=None
    )
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)

    results = catalog.search("a_b")

    assert [m.id for m in results] == [1]


def test_resolve_version_error_names_the_spt_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """NoCompatibleVersionError message includes the requested SPT version for clarity."""
    # Real data: every cached version of Boss Notifier targets SPT 3.x or
    # carries no constraint at all, so SPT 4.1.3 has nothing to install.
    _sync_single_mod_with_versions(
        client_factory,
        respx_router,
        catalog,
        mod_id=1247,
        name="Boss Notifier",
        versions=[
            {
                "id": 586,
                "version": "1.6.0",
                "spt_version_constraint": "~3.11.0",
                "downloads": 1,
                "published_at": "2025-05-16T15:21:41.000000Z",
            },
            {
                "id": 1858,
                "version": "1.5.4",
                "spt_version_constraint": "",
                "downloads": 1,
                "published_at": "2024-12-27T19:55:03.000000Z",
            },
            {
                "id": 4963,
                "version": "1.3.4",
                "spt_version_constraint": "3.8.0",
                "downloads": 1,
                "published_at": "2024-04-01T21:36:47.000000Z",
            },
        ],
    )

    with pytest.raises(NoCompatibleVersionError) as error:
        catalog.resolve_version("Boss Notifier", spt_version="4.1.3")

    assert str(error.value) == "No version of 'Boss Notifier' supports SPT 4.1.3."


def test_get_description_returns_none_for_an_unknown_mod(catalog: Catalog) -> None:
    """An empty cache returns None for get_description(99999) rather than
    raising; this guards against description lookups for nonexistent mods
    becoming unhandled exceptions.
    """
    assert catalog.get_description(99999) is None


def test_get_description_returns_none_before_a_description_is_stored(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """After seeding mods, get_description(1) returns None without raising,
    indicating that descriptions are not synced from the Forge by default
    and must be fetched separately.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    assert catalog.get_description(1) is None


@pytest.mark.parametrize(
    "stamp", ["2025-06-01T00:00:00Z", None], ids=["stamped", "unstamped"]
)
def test_store_description_round_trips_text_and_stamp(
    catalog: Catalog,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    stamp: str | None,
) -> None:
    """Catalog.store_description persists text and timestamp, retrievable via get_description."""
    _seed_search_mods(client_factory, respx_router, catalog)

    catalog.store_description(1, "Plain text.", stamp)

    stored = catalog.get_description(1)
    assert stored is not None
    assert stored.text == "Plain text."
    assert stored.updated_at == stamp


def test_store_description_for_an_unknown_mod_stores_nothing(
    catalog: Catalog,
) -> None:
    """Calling store_description on a nonexistent mod id silently succeeds
    (no exception) but leaves nothing behind; this prevents description updates
    from failing when a mod was deleted between the last sync and the store call.
    """
    catalog.store_description(99999, "Plain text.", "2025-06-01T00:00:00Z")

    assert catalog.get_description(99999) is None


def test_store_description_survives_a_later_sync_of_the_same_mod(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Stored descriptions are preserved when the mod's catalog entry is updated."""
    _seed_search_mods(client_factory, respx_router, catalog)
    catalog.store_description(1, "Plain text.", "2025-06-01T00:00:00Z")
    updated = mod_json_full(
        id=1,
        name="Alpha Mod Renamed",
        slug="alpha-mod",
        owner=owner_json(name="Alice"),
        updated_at="2025-07-01T00:00:00Z",
        versions=[],
    )
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                [updated], current_page=1, last_page=1, total=1, next_url=None
            ),
        )
    )
    catalog.sync(client_factory(), full=True)

    stored = catalog.get_description(1)
    assert stored is not None
    assert stored.text == "Plain text."
    assert stored.updated_at == "2025-06-01T00:00:00Z"
