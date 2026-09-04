"""Collecting a mod's content IDs and finding their residue in profiles."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from sptmod.core.errors import GitError
from sptmod.core.game_dir import GameLayout
from sptmod.core.models import (
    ACTIVE_STATES,
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    CommitInfo,
    ProfileResidue,
    ResidueCategory,
)
from sptmod.core.repo import Repo

DB_DIR_NAME: Final = "db"
TRADERS_SEGMENT: Final = "traders"
ITEMS_SEGMENT: Final = "items"
QUESTS_SEGMENT: Final = "quests"
TRADER_BASE_FILE: Final = "base.json"

_ID_FIELD: Final = "_id"
_TEMPLATE_ID_PATTERN: Final = re.compile(r"^[0-9a-fA-F]{24}$")


@dataclass(frozen=True)
class ModIds:
    """The content IDs a mod adds, grouped by what they identify.

    Attributes:
        items: Item template IDs, 24-character hex strings.
        traders: Trader IDs, folder names below ``db/traders/`` and the ``_id``
            of their ``base.json``.
        quests: Quest IDs, the ``_id`` of quest JSON entries.
        warnings: One line per db file that could not be read or parsed, each
            naming the file and the reason. Collection continues after every
            such file, so warnings never mean an incomplete abort.
    """

    items: set[str] = field(default_factory=set)
    traders: set[str] = field(default_factory=set)
    quests: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckResult:
    """What a residue check over all mods and all profiles found.

    Attributes:
        residues: The residues per mod name, in the order the mods were
            checked. Mods without residues are absent.
        warnings: The collection warnings of every checked mod, each prefixed
            with the mod name.
    """

    residues: dict[str, list[ProfileResidue]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


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


def find_residues(profile_path: Path, ids: ModIds) -> list[ProfileResidue]:
    """Return the residues of ``ids`` in the profile file ``profile_path``.

    Searches ``characters.pmc.Inventory.items[]._tpl`` for item IDs,
    the keys of ``characters.pmc.TradersInfo`` for trader IDs and
    ``characters.pmc.Quests[].qid`` for quest IDs. Every ID that none of these
    matched is then looked for in the raw profile text; a hit there is reported
    in the ``'string'`` category, because the fallback cannot say what the ID
    is used for.

    Returns at most one residue per category, each carrying the distinct IDs
    found and how often they occur, ordered ``item``, ``trader``, ``quest``,
    ``string``. Categories without a hit are absent, so an empty list means the
    profile is free of the mod's content. A profile that is missing, unreadable
    or no valid JSON yields an empty list.

    The profile file is only read, never written.
    """
    parsed = _parse_profile(profile_path)
    if parsed is None:
        return []
    return _residues_in(parsed, ids)


def check_all(repo: Repo, layout: GameLayout) -> CheckResult:
    """Check every active mod against every profile without changing anything.

    Collects the IDs of each active mod from its install commit and searches
    them in every profile returned by :func:`profile_paths`. Neither the
    repository nor the profiles are modified.

    Raises:
        GitError: git failed.
    """
    profiles = [
        parsed
        for path in profile_paths(layout)
        if (parsed := _parse_profile(path)) is not None
    ]
    result = CheckResult()
    for name, commit in _active_mod_commits(repo).items():
        ids = collect_mod_ids(repo, commit, layout)
        result.warnings.extend(f"{name}: {warning}" for warning in ids.warnings)
        residues = [
            residue for profile in profiles for residue in _residues_in(profile, ids)
        ]
        if residues:
            result.residues[name] = residues
    return result


def profile_paths(layout: GameLayout) -> list[Path]:
    """Return the profile files of the installation, sorted by name.

    These are the ``*.json`` files directly in the profiles directory of
    ``layout``; the ``backups/`` directory below it is skipped. Returns an
    empty list if the profiles directory does not exist.
    """
    profiles_dir = layout.root / layout.profiles_dir
    backups_dir = layout.root / layout.profile_backups_dir
    if not profiles_dir.is_dir():
        return []
    return sorted(
        path
        for path in profiles_dir.glob("*.json")
        if path.is_file() and path.parent != backups_dir
    )


@dataclass(frozen=True)
class _ParsedProfile:
    """A profile file read and parsed once, ready to match several mods against."""

    name: str
    text: str
    pmc: dict[str, object]


def _parse_profile(profile_path: Path) -> _ParsedProfile | None:
    """Parse a profile JSON file into a _ParsedProfile if valid.

    Returns None if the file does not exist, cannot be read, is not UTF-8
    encoded, or contains invalid JSON.
    """
    try:
        text = profile_path.read_text(encoding="utf-8-sig")
        profile = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _ParsedProfile(name=profile_path.stem, text=text, pmc=_pmc(profile))


def _residues_in(profile: _ParsedProfile, ids: ModIds) -> list[ProfileResidue]:
    """Find which IDs from ``ids`` appear in ``profile``.

    Searches structured fields first (inventory items, traders, quests), then
    text-searches the profile for any IDs that did not match structurally.
    Untyped text hits are reported in the 'string' category.
    """
    residues: list[ProfileResidue] = []
    matched: set[str] = set()
    structural: tuple[tuple[ResidueCategory, dict[str, int]], ...] = (
        ("item", _inventory_hits(profile.pmc, ids.items)),
        ("trader", _trader_hits(profile.pmc, ids.traders)),
        ("quest", _quest_hits(profile.pmc, ids.quests)),
    )
    for category, hits in structural:
        matched.update(hits)
        residue = _residue(profile.name, category, hits)
        if residue is not None:
            residues.append(residue)

    # The fallback scans the whole profile text once per unmatched ID; mods
    # add tens of IDs and profiles are a few megabytes, so this stays cheap.
    remaining = (ids.items | ids.traders | ids.quests) - matched
    fallback = {
        found: profile.text.count(found) for found in remaining if found in profile.text
    }
    string_residue = _residue(profile.name, "string", fallback)
    if string_residue is not None:
        residues.append(string_residue)
    return residues


def _db_relative(path: Path, layout: GameLayout) -> Path | None:
    """Return the ``db/``-relative part of a mod db JSON file, or ``None``."""
    if path.suffix.lower() != ".json" or not path.is_relative_to(layout.mods_dir):
        return None
    parts = path.relative_to(layout.mods_dir).parts
    if len(parts) < 3 or parts[1] != DB_DIR_NAME:
        return None
    return Path(*parts[2:])


def _collect_from_file(db_path: Path, data: object, ids: ModIds) -> None:
    """Extract and collect content IDs from a parsed db JSON file.

    Updates ids.items, ids.traders, and ids.quests based on the file path
    segments and content structure. Categorizes 24-hex strings as template
    IDs, extracts _id fields from entries, and identifies trader IDs.
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


def _entry_ids(data: object) -> set[str]:
    """Return the ``_id`` values of a JSON object or of a list of objects."""
    entries = data if isinstance(data, list) else [data]
    found: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_id = entry.get(_ID_FIELD)
            if isinstance(entry_id, str) and entry_id:
                found.add(entry_id)
    return found


def _pmc(profile: object) -> dict[str, object]:
    """Extract the pmc character dict from a profile, or return empty dict."""
    if not isinstance(profile, dict):
        return {}
    characters = profile.get("characters")
    if not isinstance(characters, dict):
        return {}
    pmc = characters.get("pmc")
    return pmc if isinstance(pmc, dict) else {}


def _inventory_hits(pmc: dict[str, object], wanted: set[str]) -> dict[str, int]:
    """Return item template IDs from pmc.Inventory.items[]._tpl in wanted.

    Returns a dict mapping each ID to its count of occurrences.
    """
    inventory = pmc.get("Inventory")
    if not isinstance(inventory, dict):
        return {}
    items = inventory.get("items")
    if not isinstance(items, list):
        return {}
    hits: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        template = item.get("_tpl")
        if isinstance(template, str) and template in wanted:
            hits[template] = hits.get(template, 0) + 1
    return hits


def _trader_hits(pmc: dict[str, object], wanted: set[str]) -> dict[str, int]:
    """Return trader IDs from pmc.TradersInfo keys that are in wanted.

    Each trader key is counted as occurring once.
    """
    traders = pmc.get("TradersInfo")
    if not isinstance(traders, dict):
        return {}
    return {key: 1 for key in traders if key in wanted}


def _quest_hits(pmc: dict[str, object], wanted: set[str]) -> dict[str, int]:
    """Return quest IDs from pmc.Quests[].qid that are in wanted.

    Returns a dict mapping each ID to its count of occurrences.
    """
    quests = pmc.get("Quests")
    if not isinstance(quests, list):
        return {}
    hits: dict[str, int] = {}
    for quest in quests:
        if not isinstance(quest, dict):
            continue
        quest_id = quest.get("qid")
        if isinstance(quest_id, str) and quest_id in wanted:
            hits[quest_id] = hits.get(quest_id, 0) + 1
    return hits


def _residue(
    profile: str, category: ResidueCategory, hits: dict[str, int]
) -> ProfileResidue | None:
    """Create a ProfileResidue from category hits, or None if hits empty."""
    if not hits:
        return None
    return ProfileResidue(
        profile=profile,
        category=category,
        ids=sorted(hits),
        count=sum(hits.values()),
    )


def _active_mod_commits(repo: Repo) -> dict[str, str]:
    """Return the newest commit hash per active mod, newest mod first."""
    heads: dict[str, CommitInfo] = {}
    for commit in repo.log():
        name = commit.trailers.get(TRAILER_MOD, "")
        if name:
            heads.setdefault(name, commit)
    return {
        name: head.hash
        for name, head in heads.items()
        if head.trailers.get(TRAILER_MOD_STATE, "") in ACTIVE_STATES
    }
