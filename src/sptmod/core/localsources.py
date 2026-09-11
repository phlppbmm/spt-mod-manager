"""Local mod sources, stored once per user in the config directory.

A local source is a project folder a player builds a mod in. The list of them
belongs to the user, not to one game folder: the same fork is installed into
whichever installation is played, and nothing of it may land in the game
repository.

Like the mod sets and unlike the favorites list, a store that cannot be read is
reported instead of silently reading as empty: forgetting the folders a player
registered is data loss, and no launch depends on this file.
"""

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import platformdirs

from sptmod.core.models import Json
from sptmod.core.modmeta import InvalidMetadataError

LOCAL_SOURCES_FILE_NAME: Final = "localsources.json"

STORE_VERSION: Final = 1

_APP_NAME: Final = "sptmod"
_TEMP_PREFIX: Final = ".localsources-"
_VERSION_KEY: Final = "version"
_SOURCES_KEY: Final = "sources"


@dataclass(frozen=True)
class LocalSource:
    """One registered project folder and when it was registered.

    Nothing else is stored: what a folder offers is read from the archives it
    holds whenever it is asked for, so the store cannot fall out of step with
    the builds on disk.
    """

    folder: Path
    added_at: str


def default_local_sources_path() -> Path:
    """Return the local source file path in the user's config directory."""
    config_dir = platformdirs.user_config_path(_APP_NAME, appauthor=False)
    return config_dir / LOCAL_SOURCES_FILE_NAME


def source_key(folder: Path) -> str:
    """Return the text one project folder is identified by."""
    return str(folder.resolve())


def load_sources(store: Path) -> list[LocalSource]:
    """Return the project folders stored in ``store``.

    A store that does not exist yet yields an empty list.

    Raises:
        InvalidMetadataError: the store cannot be read or does not hold local
            sources in the expected shape.
    """
    document = _read_store(store)
    raw = document.get(_SOURCES_KEY, [])
    if not isinstance(raw, list):
        raise InvalidMetadataError(f"{store} holds no local mod folders")
    return [_read_source(store, item) for item in raw]


def save_sources(store: Path, sources: Sequence[LocalSource]) -> None:
    """Store ``sources`` in ``store`` as the project folders of the user.

    The file is written next to its target and moved into place, so a crash
    mid-write leaves the previous document intact.

    Raises:
        OSError: the directory or the file cannot be written.
    """
    document: Json = {
        _VERSION_KEY: STORE_VERSION,
        _SOURCES_KEY: [_dump_source(source) for source in sources],
    }
    _write(store, document)


def _read_store(store: Path) -> dict[str, Json]:
    """Return the document in ``store``, empty when there is none yet.

    Raises:
        InvalidMetadataError: the store cannot be read or is no local source
            document.
    """
    if not store.exists():
        return {}
    try:
        raw = store.read_text(encoding="utf-8")
    except OSError as error:
        raise InvalidMetadataError(f"{store} cannot be read: {error}") from error
    try:
        document: Json = json.loads(raw)
    except ValueError as error:
        raise InvalidMetadataError(f"{store} is no valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise InvalidMetadataError(f"{store} holds no local mod folders")
    return document


def _read_source(store: Path, raw: Json) -> LocalSource:
    if not isinstance(raw, dict):
        raise InvalidMetadataError(f"{store}: a local mod folder is no object")
    folder = raw.get("folder")
    added_at = raw.get("added_at", "")
    if not isinstance(folder, str) or not isinstance(added_at, str):
        raise InvalidMetadataError(f"{store}: a local mod folder is unreadable")
    return LocalSource(folder=Path(folder), added_at=added_at)


def _dump_source(source: LocalSource) -> Json:
    return {"folder": str(source.folder), "added_at": source.added_at}


def _write(store: Path, document: Json) -> None:
    """Write ``document`` to ``store`` through a temporary file."""
    store.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=store.parent, prefix=_TEMP_PREFIX)
    with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
        json.dump(document, temp_file, indent=2)
        temp_file.write("\n")
    os.replace(temp_name, store)
