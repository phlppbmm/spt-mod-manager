"""Collecting the content IDs a mod's install commit adds."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from sptmod.core.errors import GitError
from sptmod.core.game_dir import GameLayout
from sptmod.core.models import Json
from sptmod.core.profile.nodes import ID_FIELD
from sptmod.core.repo import Repo

DB_DIR_NAME: Final = "db"
TRADERS_SEGMENT: Final = "traders"
ITEMS_SEGMENT: Final = "items"
QUESTS_SEGMENT: Final = "quests"
HIDEOUT_SEGMENT: Final = "hideout"
ACHIEVEMENT_SEGMENTS: Final = frozenset({"achievement", "achievements"})
TRADER_BASE_FILE: Final = "base.json"
ACHIEVEMENTS_FILE: Final = "achievements.json"
RECIPE_LIST_KEYS: Final = ("recipes", "scavRecipes", "cultistRecipes")

_PLAIN_ID_FIELD: Final = "id"
_TEMPLATE_ID_PATTERN: Final = re.compile(r"^[0-9a-fA-F]{24}$")


@dataclass(frozen=True)
class ModIds:
    """The content IDs a mod adds, grouped by what they identify.

    Attributes:
        items: Item template IDs, 24-character hex strings.
        traders: Trader IDs, folder names below ``db/traders/`` and the ``_id``
            of their ``base.json``.
        quests: Quest IDs, the ``_id`` of quest JSON entries.
        recipes: Hideout production recipe IDs, the ``_id`` of entries in db
            files below a ``hideout`` path segment.
        achievements: Achievement IDs, the ``id`` of entries in achievement db
            files.
        warnings: One line per db file that could not be read or parsed, each
            naming the file and the reason. Collection continues after every
            such file, so warnings never mean an incomplete abort.
    """

    items: set[str] = field(default_factory=set[str])
    traders: set[str] = field(default_factory=set[str])
    quests: set[str] = field(default_factory=set[str])
    recipes: set[str] = field(default_factory=set[str])
    achievements: set[str] = field(default_factory=set[str])
    warnings: list[str] = field(default_factory=list[str])



def collect_mod_ids(repo: Repo, install_commit: str, layout: GameLayout) -> ModIds:
    """Collect the content IDs the mod's install commit adds.

    Reads every ``<runtime>/user/mods/*/db/**/*.json`` the commit touches from the
    commit itself (``repo.show_file_content``), never from the working tree, so
    the result does not depend on the current state of the game directory.

    db layouts differ between mods, so parsing is heuristic and tolerant:

    ``items``
        Every 24-character hex key of a JSON object whose value is an object,
        plus 24-character hex ``_id`` values in files below an ``items`` path
        segment.
    ``traders``
        The directory name below ``db/traders/`` a file lives in, plus the
        ``_id`` of a ``base.json`` there.
    ``quests``
        The ``_id`` of every entry in a file below a ``quests`` path segment.
    ``recipes``
        In a file below a ``hideout`` path segment: the ``_id`` of every entry
        and, for an object document, the ``_id`` of every entry of its
        ``recipes``, ``scavRecipes`` and ``cultistRecipes`` lists.
    ``achievements``
        The ``id`` — not ``_id`` — of every entry of a file named
        ``achievements.json`` or living below an ``achievement`` or
        ``achievements`` path segment.

    A file that is no valid JSON, is not UTF-8 text or that git cannot show
    adds a warning to the result and is skipped; the remaining files are still
    read.

    Raises:
        GitError: the commit does not exist or git failed.
    """
    ids = ModIds()
    for mod_file in repo.show_files(install_commit):
        db_path = _db_relative(mod_file.path, layout)
        if mod_file.change == "deleted" or db_path is None:
            continue
        try:
            content = repo.show_file_content(install_commit, mod_file.path)
            # Mod authors on Windows often save JSON with a UTF-8 BOM.
            data = json.loads(content.removeprefix("\ufeff"))
        except (GitError, json.JSONDecodeError) as error:
            ids.warnings.append(f"{mod_file.path.as_posix()}: {error}")
            continue
        _collect_from_file(db_path, data, ids)
    return ids


def _db_relative(path: Path, layout: GameLayout) -> Path | None:
    """Return the ``db/``-relative part of a mod db JSON file, or ``None``."""
    if path.suffix.lower() != ".json" or not path.is_relative_to(layout.mods_dir):
        return None
    parts = path.relative_to(layout.mods_dir).parts
    if len(parts) < 3 or parts[1] != DB_DIR_NAME:
        return None
    return Path(*parts[2:])


def _collect_from_file(db_path: Path, data: Json, ids: ModIds) -> None:
    """Extract and collect content IDs from a parsed db JSON file.

    Updates every set of ``ids`` based on the file path segments and content
    structure, following the rules documented on :func:`collect_mod_ids`.
    """
    segments = {part.lower() for part in db_path.parts[:-1]}
    if isinstance(data, dict):
        ids.items.update(
            key
            for key, value in data.items()
            if isinstance(value, dict) and _TEMPLATE_ID_PATTERN.match(key)
        )
    if db_path.parts[0].lower() == TRADERS_SEGMENT and len(db_path.parts) > 1:
        ids.traders.add(db_path.parts[1])
        if db_path.name.lower() == TRADER_BASE_FILE:
            ids.traders.update(_entry_ids(data))
    if ITEMS_SEGMENT in segments:
        ids.items.update(
            entry_id
            for entry_id in _entry_ids(data)
            if _TEMPLATE_ID_PATTERN.match(entry_id)
        )
    if QUESTS_SEGMENT in segments:
        ids.quests.update(_entry_ids(data))
    if HIDEOUT_SEGMENT in segments:
        ids.recipes.update(_entry_ids(data))
        if isinstance(data, dict):
            for key in RECIPE_LIST_KEYS:
                ids.recipes.update(_entry_ids(data.get(key)))
    if db_path.name.lower() == ACHIEVEMENTS_FILE or segments & ACHIEVEMENT_SEGMENTS:
        ids.achievements.update(_entry_ids(data, key=_PLAIN_ID_FIELD))


def _entry_ids(data: Json, key: str = ID_FIELD) -> set[str]:
    """Return the ``key`` values of a JSON object or of a list of objects."""
    entries = data if isinstance(data, list) else [data]
    found: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_id = entry.get(key)
            if isinstance(entry_id, str) and entry_id:
                found.add(entry_id)
    return found


def all_ids(ids: ModIds) -> set[str]:
    """Return every content ID of ``ids``, whatever it identifies."""
    return ids.items | ids.traders | ids.quests | ids.recipes | ids.achievements
