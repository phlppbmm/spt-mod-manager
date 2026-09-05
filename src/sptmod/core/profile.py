"""Collecting a mod's content IDs and finding their residue in profiles."""

import json
import os
import re
import tempfile
from collections.abc import Callable, Container, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    ProfileSummary,
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
REPEATABLE_QUEST_KEYS: Final = ("activeQuests", "inactiveQuests")
CHARACTERS: Final = ("pmc", "scav")
UNREADABLE_PROFILE: Final = "This file could not be read as a player profile."

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
    parsed = parse_profile(profile_path)
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
        if (parsed := parse_profile(path)) is not None
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


def profile_summaries(layout: GameLayout) -> list[ProfileSummary]:
    """Describe every profile file of the installation, sorted by file name.

    Reads the PMC character of each file returned by :func:`profile_paths`.
    A file that cannot be read or is no valid JSON is reported as unreadable
    with a message that can be shown as is; the other files are still
    described. Missing character data is no error: the profile is readable and
    simply carries no name, level or faction yet.
    """
    return [_summary(path) for path in profile_paths(layout)]


def _summary(path: Path) -> ProfileSummary:
    """Describe one profile file by its PMC character and its file data."""
    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    parsed = parse_profile(path)
    if parsed is None:
        return ProfileSummary(
            file=path.name,
            profile_id=path.stem,
            modified=modified,
            readable=False,
            error=UNREADABLE_PROFILE,
        )
    info = _dict_at(parsed.root, "characters", "pmc", "Info")
    nickname = info.get("Nickname")
    level = info.get("Level")
    side = info.get("Side")
    return ProfileSummary(
        file=path.name,
        profile_id=path.stem,
        nickname=nickname if isinstance(nickname, str) else "",
        level=level if isinstance(level, int) else 0,
        side=side if isinstance(side, str) else "",
        modified=modified,
    )


@dataclass(frozen=True)
class _ParsedProfile:
    """A profile file read and parsed once, ready to match several mods against."""

    name: str
    text: str
    root: dict[str, object]


def parse_profile(profile_path: Path) -> _ParsedProfile | None:
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
class _Scope:
    """The part of a profile one category is read from and cleaned in.

    Attributes:
        node: A character of the profile, or the profile root for the sections
            both characters share. Removers change it in place.
        ids: The content IDs of the mod being looked for.
        item_closure: The IDs of the character's inventory items that belong to
            the mod, including everything stored inside them. Empty for the
            shared sections, which hold no inventory.
    """

    node: dict[str, object]
    ids: ModIds
    item_closure: frozenset[str]


@dataclass(frozen=True)
class _Category:
    """Where content IDs of one kind can sit in a profile, and how to clear it.

    Attributes:
        category: The reported category.
        characters: The characters the container belongs to, ``''`` for the
            sections both characters share.
        path: JSON pointer of the container, with ``{character}`` standing for
            the character it is read for.
        finder: Returns the hits in one scope.
        remover: Deletes from the scope exactly what the finder reports.
    """

    category: ResidueCategory
    characters: tuple[str, ...]
    path: str
    finder: Callable[[_Scope], _Hits]
    remover: Callable[[_Scope], None]


@dataclass(frozen=True)
class _Cleaned:
    """A profile document freed of a mod's content, and what that cost.

    The residues carry no profile name; the caller knows the file they came
    from.

    Attributes:
        document: The cleaned copy of the profile document.
        removed: The residues the document no longer holds.
        untouched: The residues that stayed, because no category could say
            what they are used for.
    """

    document: dict[str, object]
    removed: list[ProfileResidue]
    untouched: list[ProfileResidue]


def _residues_in(profile: _ParsedProfile, ids: ModIds) -> list[ProfileResidue]:
    """Find which IDs from ``ids`` appear in ``profile``.

    Runs every category of ``_CATEGORIES`` over the characters it applies to,
    character-major and category-minor, then text-searches the profile for the
    IDs no category matched. Untyped text hits are reported in the 'string'
    category.
    """
    residues: list[ProfileResidue] = []
    matched: set[str] = set()
    for entry, _, character, hits in _hits_in(profile.root, ids):
        matched |= hits.ids
        residues.append(_residue(profile.name, entry, character, hits))
    return [*residues, *_string_residue(profile.name, profile.text, ids, matched)]


def clean_document(profile: _ParsedProfile, ids: ModIds) -> _Cleaned:
    """Return a copy of ``profile`` freed of the content of ``ids``.

    Every category that reports a hit also clears it, so the report is exactly
    what the copy lost and a preview can never disagree with the real run. IDs
    that no category could attribute stay: the text fallback cannot say what
    they mean, so removing them would be guesswork; it reads the same profile
    text a check reads, so both agree on what is left.

    ``profile`` itself is never changed, and the reported residues carry no
    profile name -- only the caller knows which file they came from.
    """
    document = deepcopy(profile.root)
    removed: list[ProfileResidue] = []
    matched: set[str] = set()
    for entry, scope, character, hits in _hits_in(document, ids):
        entry.remover(scope)
        matched |= hits.ids
        removed.append(_residue("", entry, character, hits))
    return _Cleaned(
        document=document,
        removed=removed,
        untouched=_string_residue("", profile.text, ids, matched),
    )


def write_profile(path: Path, data: dict[str, object]) -> None:
    """Write ``data`` to ``path`` the way the game writes profiles itself.

    Serialises without indentation, without a trailing newline and with
    non-ASCII characters kept literally, so a document that was not changed is
    written back byte for byte. The bytes go to a temporary file next to
    ``path`` that is then moved into place, so an interrupted write cannot
    leave behind a half-written profile.

    Raises:
        OSError: the profile could not be written.
    """
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(_dumps(data).encode())
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _dumps(data: dict[str, object]) -> str:
    """Return the JSON text of a profile document in the game's own format."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _hits_in(
    root: dict[str, object], ids: ModIds
) -> list[tuple[_Category, _Scope, str, _Hits]]:
    """Return every category that matches something, with its scope and hits.

    Walks the characters first, then the sections they share, and within each
    scope the categories in the order of ``_CATEGORIES``. The whole document is
    read before the caller can act on the result, so a category never reports
    against what an earlier one removed.
    """
    hits_found: list[tuple[_Category, _Scope, str, _Hits]] = []
    for character, scope in _scopes(root, ids):
        for entry in _CATEGORIES:
            if character not in entry.characters:
                continue
            hits = entry.finder(scope)
            if hits.ids:
                hits_found.append((entry, scope, character, hits))
    return hits_found


def _scopes(root: dict[str, object], ids: ModIds) -> list[tuple[str, _Scope]]:
    """Return the scopes of a profile: both characters, then the shared part.

    The inventory closure of a character is computed once here, because both
    the items themselves and the insurance entries pointing at them need it.
    """
    scopes: list[tuple[str, _Scope]] = []
    for character in CHARACTERS:
        node = _dict_at(root, "characters", character)
        closure = _item_closure(_list_at(node, "Inventory", "items"), ids.items)
        scopes.append(
            (character, _Scope(node=node, ids=ids, item_closure=frozenset(closure)))
        )
    scopes.append(("", _Scope(node=root, ids=ids, item_closure=frozenset())))
    return scopes


def _residue(
    name: str, entry: _Category, character: str, hits: _Hits
) -> ProfileResidue:
    """Return the residue one category found in one profile."""
    return ProfileResidue(
        profile=name,
        category=entry.category,
        ids=sorted(hits.ids),
        count=hits.count,
        character=character,
        path=entry.path.format(character=character),
    )


def _string_residue(
    name: str, text: str, ids: ModIds, matched: set[str]
) -> list[ProfileResidue]:
    """Return the residue of the IDs no category matched, if there is one.

    The fallback scans the whole profile text once per unmatched ID; mods add
    tens of IDs and profiles are a few megabytes, so this stays cheap.
    """
    counts = {
        found: text.count(found) for found in _all_ids(ids) - matched if found in text
    }
    if not counts:
        return []
    return [
        ProfileResidue(
            profile=name,
            category="string",
            ids=sorted(counts),
            count=sum(counts.values()),
        )
    ]


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


type _Match = Callable[[str, object], set[str]]


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


def _remove_keys(container: dict[str, object], wanted: set[str]) -> None:
    """Delete the keys of ``container`` that are in ``wanted``."""
    for key in [key for key in container if key in wanted]:
        del container[key]


def _entry_hits(container: dict[str, object], match: _Match) -> _Hits:
    """Return the hits of a container whose entries match by key and value."""
    found: set[str] = set()
    count = 0
    for key, value in container.items():
        matched = match(key, value)
        if matched:
            found |= matched
            count += 1
    return _Hits(frozenset(found), count)


def _remove_entries(container: dict[str, object], match: _Match) -> None:
    """Delete the entries of ``container`` that ``match`` ties to the mod."""
    for key in [key for key, value in container.items() if match(key, value)]:
        del container[key]


def _filter_list(
    owner: dict[str, object], key: str, keep: Callable[[object], bool]
) -> None:
    """Keep only the entries of the list ``key`` of ``owner`` that ``keep``."""
    values = owner.get(key)
    if isinstance(values, list):
        owner[key] = [value for value in values if keep(value)]


def _id_in(value: object, wanted: Container[str]) -> bool:
    """Return whether ``value`` is an ID that ``wanted`` holds."""
    return isinstance(value, str) and value in wanted


def _item_id(item: object) -> str:
    """Return the ``_id`` of an inventory item, empty when it has none."""
    if isinstance(item, dict) and isinstance(item_id := item.get(_ID_FIELD), str):
        return item_id
    return ""


def _remove_items(owner: dict[str, object], removed: Container[str]) -> None:
    """Delete the items of ``removed`` from the ``items`` list of ``owner``."""
    _filter_list(owner, "items", lambda item: _item_id(item) not in removed)


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


def _item_finder(scope: _Scope) -> _Hits:
    """Return inventory items of the mod, counting the items inside them."""
    items = _list_at(scope.node, "Inventory", "items")
    matched = _matching_templates(items, scope.ids.items)
    return _Hits(frozenset(matched), len(scope.item_closure))


def _item_remover(scope: _Scope) -> None:
    """Delete the mod's inventory items and the references pointing at them."""
    inventory = _dict_at(scope.node, "Inventory")
    _remove_items(inventory, scope.item_closure)
    _filter_list(
        inventory,
        "favoriteItems",
        lambda favorite: not _id_in(favorite, scope.item_closure),
    )
    panel = inventory.get("fastPanel")
    if isinstance(panel, dict):
        inventory["fastPanel"] = {
            slot: item
            for slot, item in panel.items()
            if not _id_in(item, scope.item_closure)
        }


def _encyclopedia_finder(scope: _Scope) -> _Hits:
    """Return encyclopedia entries for the mod's item templates."""
    return _key_hits(_dict_at(scope.node, "Encyclopedia"), scope.ids.items)


def _encyclopedia_remover(scope: _Scope) -> None:
    """Delete the encyclopedia entries of the mod's item templates."""
    _remove_keys(_dict_at(scope.node, "Encyclopedia"), scope.ids.items)


def _wishlist_finder(scope: _Scope) -> _Hits:
    """Return wish list entries for the mod's item templates."""
    return _key_hits(_dict_at(scope.node, "WishList"), scope.ids.items)


def _wishlist_remover(scope: _Scope) -> None:
    """Delete the wish list entries of the mod's item templates."""
    _remove_keys(_dict_at(scope.node, "WishList"), scope.ids.items)


def _trader_finder(scope: _Scope) -> _Hits:
    """Return trader standing entries of the mod's traders."""
    return _key_hits(_dict_at(scope.node, "TradersInfo"), scope.ids.traders)


def _trader_remover(scope: _Scope) -> None:
    """Delete the trader standing entries of the mod's traders."""
    _remove_keys(_dict_at(scope.node, "TradersInfo"), scope.ids.traders)


def _trader_purchase_finder(scope: _Scope) -> _Hits:
    """Return purchase counters of the mod's traders."""
    return _key_hits(_dict_at(scope.node, "traderPurchases"), scope.ids.traders)


def _trader_purchase_remover(scope: _Scope) -> None:
    """Delete the purchase counters of the mod's traders."""
    _remove_keys(_dict_at(scope.node, "traderPurchases"), scope.ids.traders)


def _quest_id_of(quest: object, ids: ModIds) -> str:
    """Return the mod's quest a quest entry belongs to, empty when it is none."""
    if not isinstance(quest, dict):
        return ""
    quest_id = quest.get("qid")
    if isinstance(quest_id, str) and quest_id in ids.quests:
        return quest_id
    return ""


def _quest_finder(scope: _Scope) -> _Hits:
    """Return quests of the mod the character has accepted or finished."""
    counts: dict[str, int] = {}
    for quest in _list_at(scope.node, "Quests"):
        quest_id = _quest_id_of(quest, scope.ids)
        if quest_id:
            counts[quest_id] = counts.get(quest_id, 0) + 1
    return _counted(counts)


def _quest_remover(scope: _Scope) -> None:
    """Delete the quests of the mod from the character's quest list."""
    _filter_list(scope.node, "Quests", lambda quest: not _quest_id_of(quest, scope.ids))


def _repeatable_quest_finder(scope: _Scope) -> _Hits:
    """Return daily and weekly quests belonging to the mod."""
    found: set[str] = set()
    count = 0
    for group in _list_at(scope.node, "RepeatableQuests"):
        for key in REPEATABLE_QUEST_KEYS:
            for quest in _list_at(group, key):
                matched = _repeatable_quest_ids(quest, scope.ids)
                if matched:
                    found |= matched
                    count += 1
    return _Hits(frozenset(found), count)


def _repeatable_quest_remover(scope: _Scope) -> None:
    """Delete the mod's daily and weekly quests from their lists."""
    for group in _list_at(scope.node, "RepeatableQuests"):
        if not isinstance(group, dict):
            continue
        for key in REPEATABLE_QUEST_KEYS:
            _filter_list(
                group, key, lambda quest: not _repeatable_quest_ids(quest, scope.ids)
            )


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


def _task_counter_ids(key: str, value: object, ids: ModIds) -> set[str]:
    """Return the mod's quests one progress counter belongs to."""
    matched = {key} if key in ids.quests else set()
    if isinstance(value, dict):
        source = value.get("sourceId")
        if isinstance(source, str) and source in ids.quests:
            matched.add(source)
    return matched


def _task_counter_finder(scope: _Scope) -> _Hits:
    """Return progress counters of the mod's quests."""
    return _entry_hits(
        _dict_at(scope.node, "TaskConditionCounters"),
        lambda key, value: _task_counter_ids(key, value, scope.ids),
    )


def _task_counter_remover(scope: _Scope) -> None:
    """Delete the progress counters of the mod's quests."""
    _remove_entries(
        _dict_at(scope.node, "TaskConditionCounters"),
        lambda key, value: _task_counter_ids(key, value, scope.ids),
    )


def _hideout_production_ids(key: str, value: object, ids: ModIds) -> set[str]:
    """Return the mod's recipes one hideout production belongs to."""
    matched = {key} if key in ids.recipes else set()
    if isinstance(value, dict):
        recipe = value.get("recipeId")
        if isinstance(recipe, str) and recipe in ids.recipes:
            matched.add(recipe)
    return matched


def _hideout_production_finder(scope: _Scope) -> _Hits:
    """Return running hideout productions of the mod's recipes."""
    return _entry_hits(
        _dict_at(scope.node, "Hideout", "Production"),
        lambda key, value: _hideout_production_ids(key, value, scope.ids),
    )


def _hideout_production_remover(scope: _Scope) -> None:
    """Delete the running hideout productions of the mod's recipes."""
    _remove_entries(
        _dict_at(scope.node, "Hideout", "Production"),
        lambda key, value: _hideout_production_ids(key, value, scope.ids),
    )


def _hideout_slot_hits(scope: _Scope) -> Iterator[tuple[list[object], int, set[str]]]:
    """Yield the hideout slots holding items of the mod.

    Each hit names the slot list, the position of the slot in it and the item
    templates of the mod found below that slot.
    """
    for area in _list_at(scope.node, "Hideout", "Areas"):
        slots = _list_at(area, "slots")
        for index, slot in enumerate(slots):
            matched = _templates_below(slot, scope.ids.items)
            if matched:
                yield slots, index, matched


def _hideout_area_finder(scope: _Scope) -> _Hits:
    """Return hideout area slots holding an item of the mod."""
    found: set[str] = set()
    count = 0
    for _, _, matched in _hideout_slot_hits(scope):
        found |= matched
        count += 1
    return _Hits(frozenset(found), count)


def _hideout_area_remover(scope: _Scope) -> None:
    """Empty the hideout slots holding items of the mod.

    A slot keeps its position and loses everything else: the payload of a
    filled slot differs per hideout area, and leaving a part of an item the
    game no longer knows would break the hideout.
    """
    for slots, index, _ in _hideout_slot_hits(scope):
        slots[index] = _empty_slot(slots[index])


def _empty_slot(slot: object) -> dict[str, object]:
    """Return the empty form of a hideout slot, keeping its position."""
    index = slot.get("locationIndex") if isinstance(slot, dict) else None
    return {"locationIndex": index} if isinstance(index, int) else {}


def _unlocked_recipe_finder(scope: _Scope) -> _Hits:
    """Return unlocked hideout recipes of the mod."""
    counts: dict[str, int] = {}
    for recipe in _list_at(scope.node, "UnlockedInfo", "unlockedProductionRecipe"):
        if isinstance(recipe, str) and recipe in scope.ids.recipes:
            counts[recipe] = counts.get(recipe, 0) + 1
    return _counted(counts)


def _unlocked_recipe_remover(scope: _Scope) -> None:
    """Delete the mod's recipes from the unlocked hideout recipes."""
    _filter_list(
        _dict_at(scope.node, "UnlockedInfo"),
        "unlockedProductionRecipe",
        lambda recipe: not _id_in(recipe, scope.ids.recipes),
    )


def _insured_item_ids(entry: object, scope: _Scope) -> set[str]:
    """Return the IDs that tie one insured item to the mod."""
    if not isinstance(entry, dict):
        return set()
    matched: set[str] = set()
    trader = entry.get("tid")
    if isinstance(trader, str) and trader in scope.ids.traders:
        matched.add(trader)
    item_id = entry.get("itemId")
    if isinstance(item_id, str) and item_id in scope.item_closure:
        matched.add(item_id)
    return matched


def _insured_item_finder(scope: _Scope) -> _Hits:
    """Return insurance entries for the mod's traders or inventory items."""
    found: set[str] = set()
    count = 0
    for entry in _list_at(scope.node, "InsuredItems"):
        matched = _insured_item_ids(entry, scope)
        if matched:
            found |= matched
            count += 1
    return _Hits(frozenset(found), count)


def _insured_item_remover(scope: _Scope) -> None:
    """Delete the insurance entries of the mod's traders and items."""
    _filter_list(
        scope.node, "InsuredItems", lambda entry: not _insured_item_ids(entry, scope)
    )


def _achievement_finder(scope: _Scope) -> _Hits:
    """Return achievements of the mod the character has earned."""
    return _key_hits(_dict_at(scope.node, "Achievements"), scope.ids.achievements)


def _achievement_remover(scope: _Scope) -> None:
    """Delete the achievements of the mod the character has earned."""
    _remove_keys(_dict_at(scope.node, "Achievements"), scope.ids.achievements)


def _insurance_trader(entry: object, ids: ModIds) -> str:
    """Return the mod's trader an insurance return is from, empty if none."""
    if not isinstance(entry, dict):
        return ""
    trader = entry.get("traderId")
    if isinstance(trader, str) and trader in ids.traders:
        return trader
    return ""


def _insurance_finder(scope: _Scope) -> _Hits:
    """Return pending insurance returns of the mod's traders or items."""
    found: set[str] = set()
    count = 0
    for entry in _list_at(scope.node, "insurance"):
        trader = _insurance_trader(entry, scope.ids)
        if trader:
            found.add(trader)
            count += 1
            continue
        items = _list_at(entry, "items")
        matched = _matching_templates(items, scope.ids.items)
        if matched:
            found |= matched
            count += len(_item_closure(items, scope.ids.items))
    return _Hits(frozenset(found), count)


def _insurance_remover(scope: _Scope) -> None:
    """Delete insurance returns of the mod's traders and its items."""
    _filter_list(
        scope.node,
        "insurance",
        lambda entry: not _insurance_trader(entry, scope.ids),
    )
    for entry in _list_at(scope.node, "insurance"):
        if isinstance(entry, dict):
            items = _list_at(entry, "items")
            _remove_items(entry, _item_closure(items, scope.ids.items))


def _dialogue_finder(scope: _Scope) -> _Hits:
    """Return dialogues of the mod's traders and messages about its content."""
    found: set[str] = set()
    count = 0
    for key, dialogue in _dict_at(scope.node, "dialogues").items():
        if key in scope.ids.traders:
            found.add(key)
            count += 1
            continue
        for message in _list_at(dialogue, "messages"):
            matched = _message_ids(message, scope.ids)
            if matched:
                found |= matched
                count += 1
    return _Hits(frozenset(found), count)


def _dialogue_remover(scope: _Scope) -> None:
    """Delete the dialogues of the mod's traders and messages about its content."""
    dialogues = _dict_at(scope.node, "dialogues")
    _remove_keys(dialogues, scope.ids.traders)
    for dialogue in dialogues.values():
        if isinstance(dialogue, dict):
            _filter_list(
                dialogue,
                "messages",
                lambda message: not _message_ids(message, scope.ids),
            )


def _message_ids(message: object, ids: ModIds) -> set[str]:
    """Return the IDs that tie one dialogue message to the mod."""
    if not isinstance(message, dict):
        return set()
    matched = _templates_below(message.get("items"), ids.items)
    template = message.get("templateId")
    if isinstance(template, str):
        matched |= {quest for quest in ids.quests if template.startswith(quest)}
    return matched


def _userbuild_finder(scope: _Scope) -> _Hits:
    """Return saved builds using one of the mod's item templates."""
    builds = _dict_at(scope.node, "userbuilds")
    found: set[str] = set()
    count = 0
    for key in BUILD_LIST_KEYS:
        for build in _list_at(builds, key):
            matched = _templates_below(build, scope.ids.items)
            if matched:
                found |= matched
                count += 1
    return _Hits(frozenset(found), count)


def _userbuild_remover(scope: _Scope) -> None:
    """Delete the saved builds using one of the mod's item templates."""
    builds = _dict_at(scope.node, "userbuilds")
    for key in BUILD_LIST_KEYS:
        _filter_list(
            builds,
            key,
            lambda build: not _templates_below(build, scope.ids.items),
        )


_CATEGORIES: Final[tuple[_Category, ...]] = (
    # Residue categories in emission order: character-major, then category-minor.
    _Category(
        "item",
        CHARACTERS,
        "/characters/{character}/Inventory/items",
        _item_finder,
        _item_remover,
    ),
    _Category(
        "encyclopedia",
        CHARACTERS,
        "/characters/{character}/Encyclopedia",
        _encyclopedia_finder,
        _encyclopedia_remover,
    ),
    _Category(
        "wishlist",
        CHARACTERS,
        "/characters/{character}/WishList",
        _wishlist_finder,
        _wishlist_remover,
    ),
    _Category(
        "trader",
        CHARACTERS,
        "/characters/{character}/TradersInfo",
        _trader_finder,
        _trader_remover,
    ),
    _Category(
        "trader-purchase",
        ("",),
        "/traderPurchases",
        _trader_purchase_finder,
        _trader_purchase_remover,
    ),
    _Category(
        "quest",
        CHARACTERS,
        "/characters/{character}/Quests",
        _quest_finder,
        _quest_remover,
    ),
    _Category(
        "repeatable-quest",
        ("pmc",),
        "/characters/{character}/RepeatableQuests",
        _repeatable_quest_finder,
        _repeatable_quest_remover,
    ),
    _Category(
        "task-counter",
        CHARACTERS,
        "/characters/{character}/TaskConditionCounters",
        _task_counter_finder,
        _task_counter_remover,
    ),
    _Category(
        "hideout-production",
        CHARACTERS,
        "/characters/{character}/Hideout/Production",
        _hideout_production_finder,
        _hideout_production_remover,
    ),
    _Category(
        "hideout-area",
        CHARACTERS,
        "/characters/{character}/Hideout/Areas",
        _hideout_area_finder,
        _hideout_area_remover,
    ),
    _Category(
        "unlocked-recipe",
        CHARACTERS,
        "/characters/{character}/UnlockedInfo/unlockedProductionRecipe",
        _unlocked_recipe_finder,
        _unlocked_recipe_remover,
    ),
    _Category(
        "insured-item",
        CHARACTERS,
        "/characters/{character}/InsuredItems",
        _insured_item_finder,
        _insured_item_remover,
    ),
    _Category(
        "achievement",
        ("pmc",),
        "/characters/{character}/Achievements",
        _achievement_finder,
        _achievement_remover,
    ),
    _Category("insurance", ("",), "/insurance", _insurance_finder, _insurance_remover),
    _Category("dialogue", ("",), "/dialogues", _dialogue_finder, _dialogue_remover),
    _Category("userbuild", ("",), "/userbuilds", _userbuild_finder, _userbuild_remover),
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
