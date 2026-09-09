"""Local Forge catalog: SQLite cache with sync, search, resolve and browse.

The package keeps the SQL schema with the dataclasses it mirrors
(:mod:`sptmod.core.forge.catalog.schema`), the pure row and payload conversions
(:mod:`sptmod.core.forge.catalog.rows`) and the database object
(:mod:`sptmod.core.forge.catalog.store`) apart.

``platformdirs`` stays reachable as ``sptmod.core.forge.catalog.platformdirs``:
that is where :func:`default_db_path` resolves the user config directory, and
it is the seam tests redirect to keep the developer's real catalog untouched.
"""

import platformdirs

from sptmod.core.forge.catalog.rows import is_compatible, is_newer
from sptmod.core.forge.catalog.schema import (
    APP_NAME,
    CATALOG_STATES,
    DATA_VERSION,
    DATA_VERSION_KEY,
    DB_FILE_NAME,
    SCHEMA,
    SYNC_OVERLAP_DAYS,
    SYNC_PAGE_STEP,
    WATERMARK_KEY,
    CachedDescription,
    CatalogEntry,
    CatalogMod,
    CatalogSourceLink,
    CatalogState,
    CatalogVersion,
    NoCompatibleVersionError,
    SyncResult,
    default_db_path,
)
from sptmod.core.forge.catalog.store import Catalog

__all__ = [
    "APP_NAME",
    "CATALOG_STATES",
    "DATA_VERSION",
    "DATA_VERSION_KEY",
    "DB_FILE_NAME",
    "SCHEMA",
    "SYNC_OVERLAP_DAYS",
    "SYNC_PAGE_STEP",
    "WATERMARK_KEY",
    "CachedDescription",
    "Catalog",
    "CatalogEntry",
    "CatalogMod",
    "CatalogSourceLink",
    "CatalogState",
    "CatalogVersion",
    "NoCompatibleVersionError",
    "SyncResult",
    "default_db_path",
    "is_compatible",
    "is_newer",
    "platformdirs",
]
