"""Tests for the ``first_seen_at`` stamp Catalog.sync attaches to cached mods."""

import re
import time
from datetime import UTC, datetime

import httpx
import respx

from fixture_types import JsonDict
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient
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

_ISO_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _mod(mod_id: int, name: str, *, updated_at: str, downloads: int = 1) -> JsonDict:
    """Build a mod dict for first_seen_at tests with minimal required fields.

    Generates dict with id, name, slug, owner, downloads, updated_at, and
    empty versions; used by test functions to seed the catalog.
    """
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name="Owner"),
        downloads=downloads,
        updated_at=updated_at,
        versions=[],
    )


def _sync_page(
    mods: list[JsonDict],
    respx_router: respx.MockRouter,
    client_factory: ClientFactory,
) -> ForgeClient:
    """Mock GET /mods to return mods list and build a ForgeClient.

    Sets up respx to return mods as a single-page response (current=1,
    last=1); returns a client ready for catalog.sync() calls.
    """
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(mods, current_page=1, last_page=1, total=len(mods), next_url=None),
        )
    )
    return client_factory()


def test_first_sync_stamps_a_new_mod_with_an_iso8601_now_timestamp(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """sync() stamps new mods with first_seen_at=now in ISO-8601 format.

    Syncing Alpha Mod (id=1) sets first_seen_at to a timestamp within
    ±1 second of sync start time; the stamp enables time-series tracking of
    when mods were first cached.
    """
    before = datetime.now(UTC).timestamp()
    client = _sync_page(
        [_mod(1, "Alpha Mod", updated_at="2025-06-01T00:00:00Z")],
        respx_router,
        client_factory,
    )

    catalog.sync(client, full=True)
    after = datetime.now(UTC).timestamp()

    mod = catalog.get_mod(1)
    assert mod is not None
    assert mod.first_seen_at is not None
    assert _ISO_STAMP.fullmatch(mod.first_seen_at)
    stamped = datetime.strptime(mod.first_seen_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    # Second-granularity comparison; allow one second of slack on either side.
    assert before - 1 <= stamped.timestamp() <= after + 1


def test_unchanged_mod_on_a_later_sync_keeps_the_stamp_and_is_not_reported_updated(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """sync() preserves first_seen_at for unchanged mods and reports zero updates.

    After first sync sets Alpha Mod's first_seen_at, second sync with
    identical mod data reports new_mods=0, updated_mods=0, and preserves
    the original stamp unchanged.
    """
    mods = [_mod(1, "Alpha Mod", updated_at="2025-06-01T00:00:00Z")]
    client = _sync_page(mods, respx_router, client_factory)
    catalog.sync(client, full=True)
    first_seen = catalog.get_mod(1)
    assert first_seen is not None
    original_stamp = first_seen.first_seen_at

    client = _sync_page(mods, respx_router, client_factory)
    result = catalog.sync(client, full=True)

    assert result.new_mods == 0
    assert result.updated_mods == 0
    after = catalog.get_mod(1)
    assert after is not None
    assert after.first_seen_at == original_stamp


def test_updated_mod_on_a_later_sync_keeps_its_original_first_seen_stamp(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """sync() preserves first_seen_at even when mod metadata changes.

    Alpha Mod (id=1) synced first with name="Alpha Mod", then later with
    name="Alpha Mod Renamed" and downloads=999. Sync reports updated_mods=1
    but first_seen_at remains unchanged; the stamp is immutable once set.
    """
    client = _sync_page(
        [_mod(1, "Alpha Mod", updated_at="2025-06-01T00:00:00Z")],
        respx_router,
        client_factory,
    )
    catalog.sync(client, full=True)
    original = catalog.get_mod(1)
    assert original is not None
    original_stamp = original.first_seen_at

    changed = _mod(1, "Alpha Mod Renamed", updated_at="2025-06-02T00:00:00Z", downloads=999)
    client = _sync_page([changed], respx_router, client_factory)
    result = catalog.sync(client, full=True)

    assert result.updated_mods == 1
    updated = catalog.get_mod(1)
    assert updated is not None
    assert updated.name == "Alpha Mod Renamed"
    assert updated.first_seen_at == original_stamp


def test_mod_appearing_only_in_a_later_delta_sync_gets_a_later_stamp_and_is_counted_new(
    catalog: Catalog, client_factory: ClientFactory, respx_router: respx.MockRouter
) -> None:
    """Delta sync assigns new mods a fresh first_seen_at later than prior syncs.

    Full sync of Alpha Mod sets first sync watermark and its first_seen_at.
    After sleeping 1.1s, delta sync finds Beta Mod (updated_at=2025-06-05)
    and assigns it a new first_seen_at > alpha_stamp. Result: new_mods=1.
    """
    client = _sync_page(
        [_mod(1, "Alpha Mod", updated_at="2025-06-01T00:00:00Z")],
        respx_router,
        client_factory,
    )
    catalog.sync(client, full=True)  # sets the watermark to 2025-06-01
    alpha = catalog.get_mod(1)
    assert alpha is not None
    alpha_stamp = alpha.first_seen_at
    assert alpha_stamp is not None

    time.sleep(1.1)  # the stamp format has second granularity

    delta_client = _sync_page(
        [_mod(2, "Beta Mod", updated_at="2025-06-05T00:00:00Z")],
        respx_router,
        client_factory,
    )
    result = catalog.sync(delta_client, full=False)

    assert result.new_mods == 1
    beta = catalog.get_mod(2)
    assert beta is not None
    assert beta.first_seen_at is not None
    assert beta.first_seen_at > alpha_stamp
