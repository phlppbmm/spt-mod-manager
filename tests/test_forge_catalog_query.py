"""Tests for Catalog.get_mod, find_mod, search, get_versions and resolve_version."""

import httpx
import pytest
import respx

from fixture_types import ClientFactory
from forge_json import mod_json_full, owner_json, page_envelope
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError


def _seed_search_mods(client_factory: ClientFactory, respx_router: respx.MockRouter, catalog: Catalog) -> None:
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
    page = page_envelope([alpha, beta, gamma], current_page=1, last_page=1, total=3, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)


def test_get_mod_returns_the_cached_mod(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """get_mod(2) returns cached Beta Mod (id, name, author) intact.

    After syncing three mods, get_mod(2) returns mod with id=2, name="Beta
    Mod", author="Bob"; this verifies cache round-trips metadata and id
    lookup works.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    mod = catalog.get_mod(2)

    assert mod is not None
    assert mod.id == 2
    assert mod.name == "Beta Mod"
    assert mod.author == "Bob"


def test_get_mod_returns_none_for_unknown_id(catalog: Catalog) -> None:
    """get_mod(99999) returns None for unknown id, not raising or defaulting.

    Empty cache lookups return None to distinguish "not found" from "error".
    """
    assert catalog.get_mod(99999) is None


def test_find_mod_matches_name_case_insensitively(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """find_mod("aLpHa MoD") matches Alpha Mod (id=1) despite case mismatch.

    Case-insensitive name lookup enables user input to match cached names.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    mod = catalog.find_mod("aLpHa MoD")

    assert mod is not None
    assert mod.id == 1


def test_find_mod_returns_none_when_no_name_matches(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """find_mod("Does Not Exist") returns None, not raising."""
    _seed_search_mods(client_factory, respx_router, catalog)

    assert catalog.find_mod("Does Not Exist") is None


@pytest.mark.parametrize(
    "slug,expected_id",
    [
        pytest.param("beta-mod", 2, id="exact-slug"),
        pytest.param("ALPHA-MOD", 1, id="slug-in-the-wrong-case"),
        pytest.param("GaMmA-MoD", 3, id="slug-in-mixed-case"),
    ],
)
def test_find_by_slug_matches_the_address_name_case_insensitively(
    catalog: Catalog,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    slug: str,
    expected_id: int,
) -> None:
    """find_by_slug("beta-mod") returns the cached mod with that slug.

    The slug is the stable name a mod carries in its Forge address, which is
    how a mod is identified where its display name would not do. If the body
    returned None, no lookup by address would find anything.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    mod = catalog.find_by_slug(slug)

    assert mod is not None
    assert mod.id == expected_id


@pytest.mark.parametrize(
    "slug",
    [
        pytest.param("does-not-exist", id="unknown-slug"),
        pytest.param("Alpha Mod", id="the-display-name-is-no-slug"),
        pytest.param("", id="empty-slug"),
    ],
)
def test_find_by_slug_returns_none_when_no_slug_matches(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter, slug: str
) -> None:
    """find_by_slug returns None for a slug no cached mod carries, not raising.

    A display name is deliberately not a slug: matching it would let a lookup
    by address hit a mod that only happens to be called that.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    assert catalog.find_by_slug(slug) is None


@pytest.mark.parametrize(
    ("query", "compatible_with", "expected"),
    [
        pytest.param("ALPHA", None, [(1, "Alpha Mod")], id="name-case-insensitive"),
        pytest.param("bob", None, [(2, "Beta Mod")], id="author-case-insensitive"),
        pytest.param("", None, [(1, "Alpha Mod"), (2, "Beta Mod"), (3, "Gamma Mod")], id="empty-query-all"),
        pytest.param("no-such-mod-anywhere", None, [], id="no-match"),
        pytest.param("mod", "3.8.5", [(1, "Alpha Mod"), (2, "Beta Mod")], id="spt-compat-filter"),
    ],
)
def test_search_filters_the_cached_mods(
    catalog: Catalog,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    query: str,
    compatible_with: str | None,
    expected: list[tuple[int, str]],
) -> None:
    """search matches name and author case-insensitively and honours the SPT filter.

    Against the three cached mods (Alpha/Alice and Beta/Bob for SPT 3.8, Gamma/Carol
    for 4.0): "ALPHA" finds the mod by name regardless of case; "bob" finds Beta by
    its author; the empty query returns every mod ordered by name rather than an
    error; a query nothing matches returns an empty list instead of None; and
    compatible_with="3.8.5" drops Gamma. Asserted so no user can pick an
    incompatible mod out of a search result.
    """
    _seed_search_mods(client_factory, respx_router, catalog)

    results = catalog.search(query, compatible_with=compatible_with)

    assert [(mod.id, mod.name) for mod in results] == expected


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
    """get_versions(1) returns [20, 10, 5] ordering by published_at DESC then id DESC.

    With three versions of same mod, two share published_at=2025-01-01 (ids 10
    and 20) but tiebreak by id DESC (20 before 10), and id=5 with older
    published_at=2024-12-01 ranks last.
    """
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
    """get_versions(1, compatible_with="3.8.5") returns only id=201 (1.0.0).

    With three versions cached (1.0.0 for 3.8, 1.1.0 for 3.9, 2.0.0 for 4.0),
    the SPT compatibility filter narrows the result before returning, preventing
    incompatible versions from appearing in the list.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    versions = catalog.get_versions(1, compatible_with="3.8.5")

    assert [v.id for v in versions] == [201]


def test_resolve_version_picks_newest_by_default(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """resolve_version(1) returns 2.0.0, the semantically newest version.

    With three versions cached (1.0.0, 1.1.0, 2.0.0), unconstrained resolution
    picks 2.0.0 by semantic version comparison, not by published_at timestamp
    or any other ordering.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    version = catalog.resolve_version(1)

    assert version.version == "2.0.0"


def test_resolve_version_picks_newest_compatible_with_spt_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """resolve_version(1, spt_version="3.8.5") returns 1.0.0 (SPT 3.8 only).

    With versions for SPT 3.8 (1.0.0), 3.9 (1.1.0), and 4.0 (2.0.0), the
    compatibility filter applies first, then picks the newest among compatible
    versions.
    """
    _seed_versions_for_resolve(client_factory, respx_router, catalog)

    version = catalog.resolve_version(1, spt_version="3.8.5")

    assert version.version == "1.0.0"


def test_resolve_version_honours_explicit_wanted_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """resolve_version(1, wanted="1.1.0") returns id=202 (1.1.0), not 2.0.0.

    With three versions cached (1.0.0, 1.1.0, 2.0.0), explicit wanted version
    pinning overrides the default newest-version logic.
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


@pytest.mark.parametrize(
    ("mod_id_or_name", "wanted", "spt_version", "seeded"),
    [
        pytest.param(99999, None, None, False, id="unknown-mod"),
        pytest.param(1, "9.9.9", None, True, id="unknown-wanted-version"),
        pytest.param(1, None, "5.0.0", True, id="no-spt-compatible-version"),
    ],
)
def test_resolve_version_raises_when_nothing_fits(
    catalog: Catalog,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    mod_id_or_name: int | str,
    wanted: str | None,
    spt_version: str | None,
    seeded: bool,
) -> None:
    """resolve_version raises NoCompatibleVersionError instead of guessing a version.

    "unknown-mod" looks up an id no cache holds; "unknown-wanted-version" asks for
    9.9.9 while only the seeded versions are cached; "no-spt-compatible-version" asks
    for SPT 5.0.0 while the cached versions cover 3.8, 3.9 and 4.0. Each must raise
    rather than return None or fall back to a different version, because a silent
    fallback installs a mod the user did not choose.
    """
    if seeded:
        _seed_versions_for_resolve(client_factory, respx_router, catalog)

    with pytest.raises(NoCompatibleVersionError):
        catalog.resolve_version(mod_id_or_name, wanted=wanted, spt_version=spt_version)


def _sync_single_mod_with_versions(
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    catalog: Catalog,
    mod_id: int,
    name: str,
    versions: list[dict[str, object]],
) -> None:
    """Mock one mod into the catalog with given id, name, and versions.

    Sets up respx_router to return a single-page response containing the
    mod with specified parameters, then performs a full sync to populate
    the catalog.
    """
    mod = mod_json_full(id=mod_id, name=name, owner=owner_json(name="Alice"), versions=versions)
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
    """search("örakel") and search("MÜLLER") both find mod with name "Örakel" by "Müller".

    Unicode characters in mod names and authors participate in case-insensitive
    search via casefold, enabling accented-character matching across case variants.
    """
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
    """find_mod("ÖRAKEL") locates mod with name "Örakel" via casefold.

    Unicode characters participate in case-insensitive name matching, enabling
    accented-character lookups with case variants.
    """
    mod = mod_json_full(id=1, name="Örakel", owner=owner_json(name="Müller"), versions=[])
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
    literal = mod_json_full(id=1, name="a_b", owner=owner_json(name="Alice"), versions=[])
    decoy = mod_json_full(id=2, name="aXb", owner=owner_json(name="Bob"), versions=[])
    page = page_envelope([literal, decoy], current_page=1, last_page=1, total=2, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    catalog.sync(client, full=True)

    results = catalog.search("a_b")

    assert [m.id for m in results] == [1]


def test_resolve_version_error_names_the_spt_version(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """NoCompatibleVersionError for 'Boss Notifier' with SPT 4.1.3 names both in message.

    When resolve_version("Boss Notifier", spt_version="4.1.3") finds no
    compatible version, the error message is "No version of 'Boss Notifier'
    supports SPT 4.1.3." including both the mod and SPT version for clarity.
    """
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


@pytest.mark.parametrize(
    ("mod_id", "seeded"),
    [
        pytest.param(99999, False, id="unknown-mod"),
        pytest.param(1, True, id="synced-but-not-stored"),
    ],
)
def test_get_description_returns_none_when_none_was_stored(
    catalog: Catalog,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    mod_id: int,
    seeded: bool,
) -> None:
    """get_description returns None rather than raising when no description is stored.

    "unknown-mod" queries an empty cache; "synced-but-not-stored" queries a mod a sync
    put into the cache before any store_description call, since sync carries metadata
    but not descriptions. Both return None so a description panel can render a miss
    without an exception.
    """
    if seeded:
        _seed_search_mods(client_factory, respx_router, catalog)

    assert catalog.get_description(mod_id) is None


@pytest.mark.parametrize("stamp", ["2025-06-01T00:00:00Z", None], ids=["stamped", "unstamped"])
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
    """store_description(99999, "text") succeeds but leaves nothing in cache.

    Storing description for nonexistent mod silently succeeds without exception
    or side effect, preventing failures when a mod disappears between sync and
    description store.
    """
    catalog.store_description(99999, "Plain text.", "2025-06-01T00:00:00Z")

    assert catalog.get_description(99999) is None


def test_store_description_survives_a_later_sync_of_the_same_mod(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Stored description for mod id=1 persists when the mod is re-synced renamed.

    After store_description(1, "Plain text.", timestamp) and a full sync that
    updates mod 1's name to "Alpha Mod Renamed", get_description(1) still
    returns the originally stored text and timestamp.
    """
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
            json=page_envelope([updated], current_page=1, last_page=1, total=1, next_url=None),
        )
    )
    catalog.sync(client_factory(), full=True)

    stored = catalog.get_description(1)
    assert stored is not None
    assert stored.text == "Plain text."
    assert stored.updated_at == "2025-06-01T00:00:00Z"
