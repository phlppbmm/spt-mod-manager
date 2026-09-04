"""Collecting a mod's content IDs and finding their residue in profiles."""

import json
import re
from collections.abc import Callable
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
HIDEOUT_SEGMENT: Final = "hideout"
ACHIEVEMENT_SEGMENTS: Final = frozenset({"achievement", "achievements"})
TRADER_BASE_FILE: Final = "base.json"
ACHIEVEMENTS_FILE: Final = "achievements.json"
RECIPE_LIST_KEYS: Final = ("recipes", "scavRecipes", "cultistRecipes")
BUILD_LIST_KEYS: Final = ("weaponBuilds", "equipmentBuilds", "magazineBuilds")
CHARACTERS: Final = ("pmc", "scav")

_ID_FIELD: Final = "_id"
_PLAIN_ID_FIELD: Final = "id"
_TEMPLATE_FIELD: Final = "_tpl"
_PARENT_FIELD: Final = "parentId"
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

    items: set[str] = field(default_factory=set)
    traders: set[str] = field(default_factory=set)
    quests: set[str] = field(default_factory=set)
    recipes: set[str] = field(default_factory=set)
    achievements: set[str] = field(default_factory=set)
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


def find_residues(profile_path: Path, ids: ModIds) -> list[ProfileResidue]:
    """Return the residues of ``ids`` in the profile file ``profile_path``.

    Searches both characters, ``pmc`` and ``scav``, and the sections both
    share. Every residue names the character it belongs to and the JSON
    pointer of the container it was found in:

    ``item``
        ``Inventory.items`` entries whose ``_tpl`` is one of the mod's item
        templates, plus every item stored inside them: an item counts as a hit
        when its chain of ``parentId`` references leads to such an item. The
        residue reports the matched templates in ``ids`` and how many items
        that includes in ``count``.
    ``encyclopedia``, ``wishlist``
        Keys of ``Encyclopedia`` and ``WishList`` that are one of the mod's
        item templates.
    ``trader``
        Keys of ``TradersInfo`` that are one of the mod's traders.
    ``trader-purchase``
        Keys of the shared ``traderPurchases`` that are one of the mod's
        traders.
    ``quest``
        ``Quests`` entries whose ``qid`` is one of the mod's quests.
    ``repeatable-quest``
        Entries of ``activeQuests`` or ``inactiveQuests`` in
        ``RepeatableQuests`` whose ``traderId`` is one of the mod's traders or
        whose ``_id``, ``templateId`` or ``questStatus.qid`` is one of its
        quests.
    ``task-counter``
        Keys of ``TaskConditionCounters`` that are one of the mod's quests, or
        whose entry has such a quest as its ``sourceId``.
    ``hideout-production``
        Keys of ``Hideout.Production`` that are one of the mod's recipes, or
        whose entry has such a recipe as its ``recipeId``.
    ``hideout-area``
        Slots of ``Hideout.Areas`` holding an item of the mod anywhere below
        them.
    ``unlocked-recipe``
        Entries of ``UnlockedInfo.unlockedProductionRecipe`` that are one of
        the mod's recipes.
    ``insured-item``
        ``InsuredItems`` entries whose ``tid`` is one of the mod's traders or
        whose ``itemId`` is an inventory item of the ``item`` category.
    ``achievement``
        Keys of the pmc's ``Achievements`` that are one of the mod's
        achievements.
    ``insurance``
        Shared ``insurance`` entries from one of the mod's traders, or holding
        one of its items together with the items stored inside it.
    ``dialogue``
        Shared ``dialogues`` of one of the mod's traders, and single messages
        whose ``templateId`` starts with one of the mod's quest IDs or whose
        ``items`` hold one of its item templates.
    ``userbuild``
        Entries of the shared ``userbuilds`` lists that use one of the mod's
        item templates.
    ``string``
        Every ID none of the categories above matched is looked for in the raw
        profile text; a hit there is reported here, because the fallback
        cannot say what the ID is used for.

    Returns at most one residue per character and category, each carrying the
    distinct IDs found and how many entries they account for. Residues are
    ordered by character — ``pmc``, then ``scav``, then the shared sections —
    and within a character in the order of the categories above; ``string`` is
    always last. Categories without a hit are absent, so an empty list means
    the profile is free of the mod's content. A profile that is missing,
    unreadable or no valid JSON yields an empty list.

    A missing container, or one of an unexpected type, is simply no hit.
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
    root: dict[str, object]


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
    root = profile if isinstance(profile, dict) else {}
    return _ParsedProfile(name=profile_path.stem, text=text, root=root)


def _residues_in(profile: _ParsedProfile, ids: ModIds) -> list[ProfileResidue]:
    """Find which IDs from ``ids`` appear in ``profile``.

    Runs every category of ``_CATEGORIES`` over the characters it applies to,
    character-major and category-minor, then text-searches the profile for the
    IDs no category matched. Untyped text hits are reported in the 'string'
    category.
    """
    residues: list[ProfileResidue] = []
    matched: set[str] = set()
    for character in (*CHARACTERS, ""):
        scope = (
            _dict_at(profile.root, "characters", character)
            if character
            else (profile.root)
        )
        for entry in _CATEGORIES:
            if character not in entry.characters:
                continue
            hits = entry.finder(scope, ids)
            matched.update(hits.ids)
            if hits.ids:
                residues.append(
                    ProfileResidue(
                        profile=profile.name,
                        category=entry.category,
                        ids=sorted(hits.ids),
                        count=hits.count,
                        character=character,
                        path=entry.path.format(character=character),
                    )
                )

    # The fallback scans the whole profile text once per unmatched ID; mods
    # add tens of IDs and profiles are a few megabytes, so this stays cheap.
    remaining = _all_ids(ids) - matched
    fallback = {
        found: profile.text.count(found) for found in remaining if found in profile.text
    }
    if fallback:
        residues.append(
            ProfileResidue(
                profile=profile.name,
                category="string",
                ids=sorted(fallback),
                count=sum(fallback.values()),
            )
        )
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


def _entry_ids(data: object, key: str = _ID_FIELD) -> set[str]:
    """Return the ``key`` values of a JSON object or of a list of objects."""
    entries = data if isinstance(data, list) else [data]
    found: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_id = entry.get(key)
            if isinstance(entry_id, str) and entry_id:
                found.add(entry_id)
    return found


def _all_ids(ids: ModIds) -> set[str]:
    """Return every content ID of ``ids``, whatever it identifies."""
    return ids.items | ids.traders | ids.quests | ids.recipes | ids.achievements


@dataclass(frozen=True)
class _Hits:
    """What one category matched in one profile.

    Attributes:
        ids: The distinct content IDs that matched.
        count: How many profile entries those IDs account for.
    """

    ids: frozenset[str] = frozenset()
    count: int = 0


@dataclass(frozen=True)
class _Category:
    """Where content IDs of one kind can sit in a profile.

    Attributes:
        category: The reported category.
        characters: The characters the container belongs to, ``''`` for the
            sections both characters share.
        path: JSON pointer of the container, with ``{character}`` standing for
            the character it is read for.
        finder: Returns the hits in one character, or in the whole profile for
            a shared section.
    """

    category: ResidueCategory
    characters: tuple[str, ...]
    path: str
    finder: Callable[[dict[str, object], ModIds], _Hits]


def _dict_at(scope: object, *keys: str) -> dict[str, object]:
    """Return the object at ``keys`` below ``scope``, empty if it is none."""
    node = scope
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _list_at(scope: object, *keys: str) -> list[object]:
    """Return the list at ``keys`` below ``scope``, empty if it is none."""
    node = scope
    for key in keys:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def _counted(counts: dict[str, int]) -> _Hits:
    """Return the hits of a per-ID occurrence count."""
    return _Hits(frozenset(counts), sum(counts.values()))


def _key_hits(container: dict[str, object], wanted: set[str]) -> _Hits:
    """Return the keys of ``container`` that are in ``wanted``, one entry each."""
    keys = {key for key in container if key in wanted}
    return _Hits(frozenset(keys), len(keys))


def _templates_below(node: object, wanted: set[str]) -> set[str]:
    """Return every ``_tpl`` in ``wanted`` found anywhere below ``node``."""
    found: set[str] = set()
    pending: list[object] = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            template = current.get(_TEMPLATE_FIELD)
            if isinstance(template, str) and template in wanted:
                found.add(template)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found


def _item_closure(items: list[object], wanted: set[str]) -> set[str]:
    """Return the IDs of the items of ``wanted`` and of everything inside them.

    An item belongs to the closure when its ``_tpl`` is in ``wanted`` or when
    its ``parentId`` names an item that already belongs to it. A ``parentId``
    cycle cannot loop the walk, because every item enters the closure once.
    """
    children: dict[str, list[str]] = {}
    closure: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get(_ID_FIELD)
        if not isinstance(item_id, str) or not item_id:
            continue
        parent = item.get(_PARENT_FIELD)
        if isinstance(parent, str):
            children.setdefault(parent, []).append(item_id)
        template = item.get(_TEMPLATE_FIELD)
        if isinstance(template, str) and template in wanted:
            closure.add(item_id)
    pending = list(closure)
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in closure:
                closure.add(child)
                pending.append(child)
    return closure


def _matching_templates(items: list[object], wanted: set[str]) -> set[str]:
    """Return the ``_tpl`` values of ``items`` that are in ``wanted``."""
    return {
        template
        for item in items
        if isinstance(item, dict)
        and isinstance(template := item.get(_TEMPLATE_FIELD), str)
        and template in wanted
    }


def _item_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return inventory items of the mod, counting the items inside them."""
    items = _list_at(scope, "Inventory", "items")
    matched = _matching_templates(items, ids.items)
    return _Hits(frozenset(matched), len(_item_closure(items, ids.items)))


def _encyclopedia_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return encyclopedia entries for the mod's item templates."""
    return _key_hits(_dict_at(scope, "Encyclopedia"), ids.items)


def _wishlist_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return wish list entries for the mod's item templates."""
    return _key_hits(_dict_at(scope, "WishList"), ids.items)


def _trader_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return trader standing entries of the mod's traders."""
    return _key_hits(_dict_at(scope, "TradersInfo"), ids.traders)


def _trader_purchase_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return purchase counters of the mod's traders."""
    return _key_hits(_dict_at(scope, "traderPurchases"), ids.traders)


def _quest_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return quests of the mod the character has accepted or finished."""
    counts: dict[str, int] = {}
    for quest in _list_at(scope, "Quests"):
        if not isinstance(quest, dict):
            continue
        quest_id = quest.get("qid")
        if isinstance(quest_id, str) and quest_id in ids.quests:
            counts[quest_id] = counts.get(quest_id, 0) + 1
    return _counted(counts)


def _repeatable_quest_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return daily and weekly quests belonging to the mod."""
    found: set[str] = set()
    count = 0
    for group in _list_at(scope, "RepeatableQuests"):
        if not isinstance(group, dict):
            continue
        for key in ("activeQuests", "inactiveQuests"):
            for quest in _list_at(group, key):
                matched = _repeatable_quest_ids(quest, ids)
                if matched:
                    found |= matched
                    count += 1
    return _Hits(frozenset(found), count)


def _repeatable_quest_ids(quest: object, ids: ModIds) -> set[str]:
    """Return the IDs that tie one repeatable quest to the mod."""
    if not isinstance(quest, dict):
        return set()
    candidates = [quest.get(_ID_FIELD), quest.get("templateId")]
    status = quest.get("questStatus")
    if isinstance(status, dict):
        candidates.append(status.get("qid"))
    matched = {
        candidate
        for candidate in candidates
        if isinstance(candidate, str) and candidate in ids.quests
    }
    trader = quest.get("traderId")
    if isinstance(trader, str) and trader in ids.traders:
        matched.add(trader)
    return matched


def _task_counter_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return progress counters of the mod's quests."""
    found: set[str] = set()
    count = 0
    for key, value in _dict_at(scope, "TaskConditionCounters").items():
        matched = {key} if key in ids.quests else set()
        if isinstance(value, dict):
            source = value.get("sourceId")
            if isinstance(source, str) and source in ids.quests:
                matched.add(source)
        if matched:
            found |= matched
            count += 1
    return _Hits(frozenset(found), count)


def _hideout_production_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return running hideout productions of the mod's recipes."""
    found: set[str] = set()
    count = 0
    for key, value in _dict_at(scope, "Hideout", "Production").items():
        matched = {key} if key in ids.recipes else set()
        if isinstance(value, dict):
            recipe = value.get("recipeId")
            if isinstance(recipe, str) and recipe in ids.recipes:
                matched.add(recipe)
        if matched:
            found |= matched
            count += 1
    return _Hits(frozenset(found), count)


def _hideout_area_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return hideout area slots holding an item of the mod."""
    found: set[str] = set()
    count = 0
    for area in _list_at(scope, "Hideout", "Areas"):
        for slot in _list_at(area, "slots"):
            matched = _templates_below(slot, ids.items)
            if matched:
                found |= matched
                count += 1
    return _Hits(frozenset(found), count)


def _unlocked_recipe_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return unlocked hideout recipes of the mod."""
    counts: dict[str, int] = {}
    for recipe in _list_at(scope, "UnlockedInfo", "unlockedProductionRecipe"):
        if isinstance(recipe, str) and recipe in ids.recipes:
            counts[recipe] = counts.get(recipe, 0) + 1
    return _counted(counts)


def _insured_item_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return insurance entries for the mod's traders or inventory items."""
    closure = _item_closure(_list_at(scope, "Inventory", "items"), ids.items)
    found: set[str] = set()
    count = 0
    for entry in _list_at(scope, "InsuredItems"):
        if not isinstance(entry, dict):
            continue
        matched: set[str] = set()
        trader = entry.get("tid")
        if isinstance(trader, str) and trader in ids.traders:
            matched.add(trader)
        item_id = entry.get("itemId")
        if isinstance(item_id, str) and item_id in closure:
            matched.add(item_id)
        if matched:
            found |= matched
            count += 1
    return _Hits(frozenset(found), count)


def _achievement_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return achievements of the mod the character has earned."""
    return _key_hits(_dict_at(scope, "Achievements"), ids.achievements)


def _insurance_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return pending insurance returns of the mod's traders or items."""
    found: set[str] = set()
    count = 0
    for entry in _list_at(scope, "insurance"):
        if not isinstance(entry, dict):
            continue
        trader = entry.get("traderId")
        if isinstance(trader, str) and trader in ids.traders:
            found.add(trader)
            count += 1
            continue
        items = _list_at(entry, "items")
        matched = _matching_templates(items, ids.items)
        if matched:
            found |= matched
            count += len(_item_closure(items, ids.items))
    return _Hits(frozenset(found), count)


def _dialogue_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return dialogues of the mod's traders and messages about its content."""
    found: set[str] = set()
    count = 0
    for key, dialogue in _dict_at(scope, "dialogues").items():
        if key in ids.traders:
            found.add(key)
            count += 1
            continue
        for message in _list_at(dialogue, "messages"):
            matched = _message_ids(message, ids)
            if matched:
                found |= matched
                count += 1
    return _Hits(frozenset(found), count)


def _message_ids(message: object, ids: ModIds) -> set[str]:
    """Return the IDs that tie one dialogue message to the mod."""
    if not isinstance(message, dict):
        return set()
    matched = _templates_below(message.get("items"), ids.items)
    template = message.get("templateId")
    if isinstance(template, str):
        matched |= {quest for quest in ids.quests if template.startswith(quest)}
    return matched


def _userbuild_finder(scope: dict[str, object], ids: ModIds) -> _Hits:
    """Return saved builds using one of the mod's item templates."""
    builds = _dict_at(scope, "userbuilds")
    found: set[str] = set()
    count = 0
    for key in BUILD_LIST_KEYS:
        for build in _list_at(builds, key):
            matched = _templates_below(build, ids.items)
            if matched:
                found |= matched
                count += 1
    return _Hits(frozenset(found), count)


_CATEGORIES: Final[tuple[_Category, ...]] = (
    _Category(
        "item", CHARACTERS, "/characters/{character}/Inventory/items", _item_finder
    ),
    _Category(
        "encyclopedia",
        CHARACTERS,
        "/characters/{character}/Encyclopedia",
        _encyclopedia_finder,
    ),
    _Category(
        "wishlist", CHARACTERS, "/characters/{character}/WishList", _wishlist_finder
    ),
    _Category(
        "trader", CHARACTERS, "/characters/{character}/TradersInfo", _trader_finder
    ),
    _Category("trader-purchase", ("",), "/traderPurchases", _trader_purchase_finder),
    _Category("quest", CHARACTERS, "/characters/{character}/Quests", _quest_finder),
    _Category(
        "repeatable-quest",
        ("pmc",),
        "/characters/{character}/RepeatableQuests",
        _repeatable_quest_finder,
    ),
    _Category(
        "task-counter",
        CHARACTERS,
        "/characters/{character}/TaskConditionCounters",
        _task_counter_finder,
    ),
    _Category(
        "hideout-production",
        CHARACTERS,
        "/characters/{character}/Hideout/Production",
        _hideout_production_finder,
    ),
    _Category(
        "hideout-area",
        CHARACTERS,
        "/characters/{character}/Hideout/Areas",
        _hideout_area_finder,
    ),
    _Category(
        "unlocked-recipe",
        CHARACTERS,
        "/characters/{character}/UnlockedInfo/unlockedProductionRecipe",
        _unlocked_recipe_finder,
    ),
    _Category(
        "insured-item",
        CHARACTERS,
        "/characters/{character}/InsuredItems",
        _insured_item_finder,
    ),
    _Category(
        "achievement",
        ("pmc",),
        "/characters/{character}/Achievements",
        _achievement_finder,
    ),
    _Category("insurance", ("",), "/insurance", _insurance_finder),
    _Category("dialogue", ("",), "/dialogues", _dialogue_finder),
    _Category("userbuild", ("",), "/userbuilds", _userbuild_finder),
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
