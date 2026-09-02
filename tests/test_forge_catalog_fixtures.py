"""Shared fixtures and builders for the forge/catalog.py test suite.

Not a standalone test module (no ``test_*`` functions): it is imported by
the other ``test_forge_catalog_*.py`` files, mirroring the pattern used by
``test_forge_client_fixtures.py`` for the client test suite. Re-exports the
respx/HTTP builders from ``test_forge_client_fixtures`` so catalog tests can
build mocked Forge responses without duplicating them.
"""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sptmod.core.forge.catalog import Catalog
from sptmod.core.models import ModInfo
from test_forge_client_fixtures import (
    client_factory,
    error_envelope,
    fake_clock,
    mod_json_full,
    mod_json_minimal,
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
    "error_envelope",
    "fake_clock",
    "make_mod_info",
    "mod_json_full",
    "mod_json_minimal",
    "owner_json",
    "page_envelope",
    "recording_sleep",
    "respx_router",
    "success_envelope",
    "version_json",
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path of the catalog SQLite file for one test, inside ``tmp_path``.

    Never the real platformdirs config dir: catalog tests must not touch the
    developer's actual config directory.
    """
    return tmp_path / "forge-catalog.db"


@pytest.fixture
def catalog(db_path: Path) -> Iterator[Catalog]:
    """A ``Catalog`` opened at the test's isolated ``db_path``."""
    with Catalog(db_path) as cat:
        yield cat


def make_mod_info(
    *,
    forge_id: str = "",
    name: str = "Some Mod",
    version: str = "1.0.0",
    **overrides: Any,
) -> ModInfo:
    """Build a ``ModInfo`` fixture with sensible defaults for classify() tests.

    ``forge_id`` and ``name`` are the two fields classify() matches on;
    everything else defaults to plausible but otherwise irrelevant values.
    """
    fields: dict[str, Any] = {
        "name": name,
        "version": version,
        "mod_type": "server",
        "author": "Someone",
        "spt_range": "",
        "state": "installed",
        "commit": "abc123",
        "date": datetime(2025, 1, 1),
        "forge_id": forge_id,
        "forge_version_id": "",
        "guid": "",
        "source": "forge",
    }
    fields.update(overrides)
    return ModInfo(**fields)
