"""Mod sets, stored per game directory in the user's config directory.

A mod set is a named list of mods with the state each of them should have. The
sets live outside every game directory, in one JSON document keyed by the
resolved path of the game directory, so switching game folders keeps the sets
of both and nothing lands in the game repository.

Unlike the favorites list a broken store is not silently treated as empty: a
set decides which mods a player runs, so a document that cannot be read is
reported instead of quietly losing sets.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import platformdirs

from sptmod.core.models import Json
from sptmod.core.modmeta import InvalidMetadataError

MODSETS_FILE_NAME: Final = "modsets.json"

STORE_VERSION: Final = 1

_APP_NAME: Final = "sptmod"
_TEMP_PREFIX: Final = ".modsets-"
_VERSION_KEY: Final = "version"
_GAMES_KEY: Final = "games"
_SETS_KEY: Final = "sets"
_BINDINGS_KEY: Final = "bindings"


@dataclass(frozen=True)
class ModSetEntry:
    """One mod of a mod set and the state the set gives it.

    ``forge_id`` is the Forge id of the mod, empty for mods Forge knows
    nothing about; ``name`` is the name the installation knows the mod by.
    """

    forge_id: str
    name: str
    version: str
    enabled: bool


@dataclass(frozen=True)
class ModSet:
    """A named list of mods with the state each of them should have."""

    name: str
    created_at: str
    entries: list[ModSetEntry] = field(default_factory=list[ModSetEntry])


@dataclass(frozen=True)
class ProfileBinding:
    """The mod set one player profile is played with."""

    profile_id: str
    set_name: str


@dataclass(frozen=True)
class GameSets:
    """Everything one game directory knows about mod sets."""

    sets: list[ModSet] = field(default_factory=list[ModSet])
    bindings: list[ProfileBinding] = field(default_factory=list[ProfileBinding])


def default_modsets_path() -> Path:
    """Return the mod set file path in the user's config directory."""
    config_dir = platformdirs.user_config_path(_APP_NAME, appauthor=False)
    return config_dir / MODSETS_FILE_NAME


def store_key(game_dir: Path) -> str:
    """Return the key the mod sets of the game directory ``game_dir`` live under."""
    return str(game_dir.resolve())


def load_sets(store: Path, key: str) -> GameSets:
    """Return the mod sets stored in ``store`` for the game directory ``key``.

    A store that does not exist yet, or knows nothing about ``key``, yields
    empty lists.

    Raises:
        InvalidMetadataError: the store cannot be read or does not hold mod
            sets in the expected shape.
    """
    games = _read_store(store)
    game = games.get(key)
    if game is None:
        return GameSets()
    if not isinstance(game, dict):
        raise InvalidMetadataError(f"{store}: the entry for {key} is no object")
    return GameSets(
        sets=_read_sets(store, game.get(_SETS_KEY)),
        bindings=_read_bindings(store, game.get(_BINDINGS_KEY)),
    )


def save_sets(store: Path, key: str, sets: GameSets) -> None:
    """Store ``sets`` in ``store`` as the mod sets of the game directory ``key``.

    The rest of the document, meaning the mod sets of every other game
    directory, is kept. The file is written next to its target and moved into
    place, so a crash mid-write leaves the previous document intact.

    Raises:
        InvalidMetadataError: the store exists and cannot be read.
        OSError: the directory or the file cannot be written.
    """
    games = dict(_read_store(store))
    games[key] = {
        _SETS_KEY: [_dump_set(mod_set) for mod_set in sets.sets],
        _BINDINGS_KEY: {binding.profile_id: binding.set_name for binding in sets.bindings},
    }
    document: Json = {_VERSION_KEY: STORE_VERSION, _GAMES_KEY: games}
    _write(store, document)


def _read_store(store: Path) -> dict[str, Json]:
    """Return the per-game-directory entries of the document in ``store``.

    Raises:
        InvalidMetadataError: the store cannot be read or is no mod set
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
        raise InvalidMetadataError(f"{store} holds no mod sets")
    games = document.get(_GAMES_KEY, {})
    if not isinstance(games, dict):
        raise InvalidMetadataError(f"{store} holds no mod sets")
    return games


def _read_sets(store: Path, raw: Json) -> list[ModSet]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidMetadataError(f"{store}: the mod sets are no list")
    return [_read_set(store, item) for item in raw]


def _read_set(store: Path, raw: Json) -> ModSet:
    if not isinstance(raw, dict):
        raise InvalidMetadataError(f"{store}: a mod set is no object")
    name = raw.get("name")
    created_at = raw.get("created_at", "")
    if not isinstance(name, str) or not isinstance(created_at, str):
        raise InvalidMetadataError(f"{store}: a mod set has no name")
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        raise InvalidMetadataError(f"{store}: the mods of {name!r} are no list")
    return ModSet(
        name=name,
        created_at=created_at,
        entries=[_read_entry(store, name, item) for item in entries],
    )


def _read_entry(store: Path, set_name: str, raw: Json) -> ModSetEntry:
    if not isinstance(raw, dict):
        raise InvalidMetadataError(f"{store}: a mod of {set_name!r} is no object")
    forge_id = raw.get("forge_id", "")
    name = raw.get("name")
    version = raw.get("version", "")
    enabled = raw.get("enabled", True)
    if (
        not isinstance(forge_id, str)
        or not isinstance(name, str)
        or not isinstance(version, str)
        or not isinstance(enabled, bool)
    ):
        raise InvalidMetadataError(f"{store}: a mod of {set_name!r} is unreadable")
    return ModSetEntry(forge_id=forge_id, name=name, version=version, enabled=enabled)


def _read_bindings(store: Path, raw: Json) -> list[ProfileBinding]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise InvalidMetadataError(f"{store}: the profile bindings are no object")
    bindings: list[ProfileBinding] = []
    for profile_id, set_name in raw.items():
        if not isinstance(set_name, str):
            raise InvalidMetadataError(f"{store}: the mod set of profile {profile_id} is no name")
        bindings.append(ProfileBinding(profile_id=profile_id, set_name=set_name))
    return bindings


def _dump_set(mod_set: ModSet) -> Json:
    return {
        "name": mod_set.name,
        "created_at": mod_set.created_at,
        "entries": [_dump_entry(entry) for entry in mod_set.entries],
    }


def _dump_entry(entry: ModSetEntry) -> Json:
    return {
        "forge_id": entry.forge_id,
        "name": entry.name,
        "version": entry.version,
        "enabled": entry.enabled,
    }


def _write(store: Path, document: Json) -> None:
    """Write ``document`` to ``store`` through a temporary file."""
    store.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=store.parent, prefix=_TEMP_PREFIX)
    with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
        json.dump(document, temp_file, indent=2)
        temp_file.write("\n")
    os.replace(temp_name, store)
