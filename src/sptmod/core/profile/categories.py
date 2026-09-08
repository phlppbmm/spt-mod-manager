"""Where a mod's content IDs can sit in a profile, and how to clear each place."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sptmod.core.models import Json, ResidueCategory
from sptmod.core.profile.ids import ModIds
from sptmod.core.profile.nodes import (
    ID_FIELD,
    Hits,
    counted,
    dict_at,
    entry_hits,
    filter_list,
    id_in,
    item_closure,
    key_hits,
    list_at,
    matching_templates,
    remove_entries,
    remove_items,
    remove_keys,
    templates_below,
)


class ResidueCharacter(StrEnum):
    """The character a residue scope belongs to; ``SHARED`` for both."""

    PMC = "pmc"
    SCAV = "scav"
    SHARED = ""


BUILD_LIST_KEYS: Final = ("weaponBuilds", "equipmentBuilds", "magazineBuilds")
REPEATABLE_QUEST_KEYS: Final = ("activeQuests", "inactiveQuests")
CHARACTERS: Final = (ResidueCharacter.PMC, ResidueCharacter.SCAV)


@dataclass(frozen=True)
class Scope:
    """The part of a profile one category is read from and cleaned in.

    Attributes:
        node: A character of the profile, or the profile root for the sections
            both characters share. Removers change it in place.
        ids: The content IDs of the mod being looked for.
        item_closure: The IDs of the character's inventory items that belong to
            the mod, including everything stored inside them. Empty for the
            shared sections, which hold no inventory.
    """

    node: dict[str, Json]
    ids: ModIds
    item_closure: frozenset[str]


@dataclass(frozen=True)
class Category:
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
    characters: tuple[ResidueCharacter, ...]
    path: str
    finder: Callable[[Scope], Hits]
    remover: Callable[[Scope], None]


def _item_finder(scope: Scope) -> Hits:
    """Return inventory items of the mod, counting the items inside them."""
    items = list_at(scope.node, "Inventory", "items")
    matched = matching_templates(items, scope.ids.items)
    return Hits(frozenset(matched), len(scope.item_closure))


def _item_remover(scope: Scope) -> None:
    """Delete the mod's inventory items and the references pointing at them."""
    inventory = dict_at(scope.node, "Inventory")
    remove_items(inventory, scope.item_closure)
    filter_list(
        inventory,
        "favoriteItems",
        lambda favorite: not id_in(favorite, scope.item_closure),
    )
    panel = inventory.get("fastPanel")
    if isinstance(panel, dict):
        inventory["fastPanel"] = {
            slot: item
            for slot, item in panel.items()
            if not id_in(item, scope.item_closure)
        }


def _encyclopedia_finder(scope: Scope) -> Hits:
    """Return encyclopedia entries for the mod's item templates."""
    return key_hits(dict_at(scope.node, "Encyclopedia"), scope.ids.items)


def _encyclopedia_remover(scope: Scope) -> None:
    """Delete the encyclopedia entries of the mod's item templates."""
    remove_keys(dict_at(scope.node, "Encyclopedia"), scope.ids.items)


def _wishlist_finder(scope: Scope) -> Hits:
    """Return wish list entries for the mod's item templates."""
    return key_hits(dict_at(scope.node, "WishList"), scope.ids.items)


def _wishlist_remover(scope: Scope) -> None:
    """Delete the wish list entries of the mod's item templates."""
    remove_keys(dict_at(scope.node, "WishList"), scope.ids.items)


def _trader_finder(scope: Scope) -> Hits:
    """Return trader standing entries of the mod's traders."""
    return key_hits(dict_at(scope.node, "TradersInfo"), scope.ids.traders)


def _trader_remover(scope: Scope) -> None:
    """Delete the trader standing entries of the mod's traders."""
    remove_keys(dict_at(scope.node, "TradersInfo"), scope.ids.traders)


def _trader_purchase_finder(scope: Scope) -> Hits:
    """Return purchase counters of the mod's traders."""
    return key_hits(dict_at(scope.node, "traderPurchases"), scope.ids.traders)


def _trader_purchase_remover(scope: Scope) -> None:
    """Delete the purchase counters of the mod's traders."""
    remove_keys(dict_at(scope.node, "traderPurchases"), scope.ids.traders)


def _quest_id_of(quest: Json, ids: ModIds) -> str:
    """Return the mod's quest a quest entry belongs to, empty when it is none."""
    if not isinstance(quest, dict):
        return ""
    quest_id = quest.get("qid")
    if isinstance(quest_id, str) and quest_id in ids.quests:
        return quest_id
    return ""


def _quest_finder(scope: Scope) -> Hits:
    """Return quests of the mod the character has accepted or finished."""
    counts: dict[str, int] = {}
    for quest in list_at(scope.node, "Quests"):
        quest_id = _quest_id_of(quest, scope.ids)
        if quest_id:
            counts[quest_id] = counts.get(quest_id, 0) + 1
    return counted(counts)


def _quest_remover(scope: Scope) -> None:
    """Delete the quests of the mod from the character's quest list."""
    filter_list(scope.node, "Quests", lambda quest: not _quest_id_of(quest, scope.ids))


def _repeatable_quest_finder(scope: Scope) -> Hits:
    """Return daily and weekly quests belonging to the mod."""
    found: set[str] = set()
    count = 0
    for group in list_at(scope.node, "RepeatableQuests"):
        for key in REPEATABLE_QUEST_KEYS:
            for quest in list_at(group, key):
                matched = _repeatable_quest_ids(quest, scope.ids)
                if matched:
                    found |= matched
                    count += 1
    return Hits(frozenset(found), count)


def _repeatable_quest_remover(scope: Scope) -> None:
    """Delete the mod's daily and weekly quests from their lists."""
    for group in list_at(scope.node, "RepeatableQuests"):
        if not isinstance(group, dict):
            continue
        for key in REPEATABLE_QUEST_KEYS:
            filter_list(
                group, key, lambda quest: not _repeatable_quest_ids(quest, scope.ids)
            )


def _repeatable_quest_ids(quest: Json, ids: ModIds) -> set[str]:
    """Return the IDs that tie one repeatable quest to the mod."""
    if not isinstance(quest, dict):
        return set()
    candidates = [quest.get(ID_FIELD), quest.get("templateId")]
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


def _task_counter_ids(key: str, value: Json, ids: ModIds) -> set[str]:
    """Return the mod's quests one progress counter belongs to."""
    matched: set[str] = {key} if key in ids.quests else set()
    if isinstance(value, dict):
        source = value.get("sourceId")
        if isinstance(source, str) and source in ids.quests:
            matched.add(source)
    return matched


def _task_counter_finder(scope: Scope) -> Hits:
    """Return progress counters of the mod's quests."""
    return entry_hits(
        dict_at(scope.node, "TaskConditionCounters"),
        lambda key, value: _task_counter_ids(key, value, scope.ids),
    )


def _task_counter_remover(scope: Scope) -> None:
    """Delete the progress counters of the mod's quests."""
    remove_entries(
        dict_at(scope.node, "TaskConditionCounters"),
        lambda key, value: _task_counter_ids(key, value, scope.ids),
    )


def _hideout_production_ids(key: str, value: Json, ids: ModIds) -> set[str]:
    """Return the mod's recipes one hideout production belongs to."""
    matched: set[str] = {key} if key in ids.recipes else set()
    if isinstance(value, dict):
        recipe = value.get("recipeId")
        if isinstance(recipe, str) and recipe in ids.recipes:
            matched.add(recipe)
    return matched


def _hideout_production_finder(scope: Scope) -> Hits:
    """Return running hideout productions of the mod's recipes."""
    return entry_hits(
        dict_at(scope.node, "Hideout", "Production"),
        lambda key, value: _hideout_production_ids(key, value, scope.ids),
    )


def _hideout_production_remover(scope: Scope) -> None:
    """Delete the running hideout productions of the mod's recipes."""
    remove_entries(
        dict_at(scope.node, "Hideout", "Production"),
        lambda key, value: _hideout_production_ids(key, value, scope.ids),
    )


def _hideout_slot_hits(scope: Scope) -> Iterator[tuple[list[Json], int, set[str]]]:
    """Yield the hideout slots holding items of the mod.

    Each hit names the slot list, the position of the slot in it and the item
    templates of the mod found below that slot.
    """
    for area in list_at(scope.node, "Hideout", "Areas"):
        slots = list_at(area, "slots")
        for index, slot in enumerate(slots):
            matched = templates_below(slot, scope.ids.items)
            if matched:
                yield slots, index, matched


def _hideout_area_finder(scope: Scope) -> Hits:
    """Return hideout area slots holding an item of the mod."""
    found: set[str] = set()
    count = 0
    for _, _, matched in _hideout_slot_hits(scope):
        found |= matched
        count += 1
    return Hits(frozenset(found), count)


def _hideout_area_remover(scope: Scope) -> None:
    """Empty the hideout slots holding items of the mod.

    A slot keeps its position and loses everything else: the payload of a
    filled slot differs per hideout area, and leaving a part of an item the
    game no longer knows would break the hideout.
    """
    for slots, index, _ in _hideout_slot_hits(scope):
        slots[index] = _empty_slot(slots[index])


def _empty_slot(slot: Json) -> dict[str, Json]:
    """Return the empty form of a hideout slot, keeping its position."""
    index = slot.get("locationIndex") if isinstance(slot, dict) else None
    return {"locationIndex": index} if isinstance(index, int) else {}


def _unlocked_recipe_finder(scope: Scope) -> Hits:
    """Return unlocked hideout recipes of the mod."""
    counts: dict[str, int] = {}
    for recipe in list_at(scope.node, "UnlockedInfo", "unlockedProductionRecipe"):
        if isinstance(recipe, str) and recipe in scope.ids.recipes:
            counts[recipe] = counts.get(recipe, 0) + 1
    return counted(counts)


def _unlocked_recipe_remover(scope: Scope) -> None:
    """Delete the mod's recipes from the unlocked hideout recipes."""
    filter_list(
        dict_at(scope.node, "UnlockedInfo"),
        "unlockedProductionRecipe",
        lambda recipe: not id_in(recipe, scope.ids.recipes),
    )


def _insured_item_ids(entry: Json, scope: Scope) -> set[str]:
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


def _insured_item_finder(scope: Scope) -> Hits:
    """Return insurance entries for the mod's traders or inventory items."""
    found: set[str] = set()
    count = 0
    for entry in list_at(scope.node, "InsuredItems"):
        matched = _insured_item_ids(entry, scope)
        if matched:
            found |= matched
            count += 1
    return Hits(frozenset(found), count)


def _insured_item_remover(scope: Scope) -> None:
    """Delete the insurance entries of the mod's traders and items."""
    filter_list(
        scope.node, "InsuredItems", lambda entry: not _insured_item_ids(entry, scope)
    )


def _achievement_finder(scope: Scope) -> Hits:
    """Return achievements of the mod the character has earned."""
    return key_hits(dict_at(scope.node, "Achievements"), scope.ids.achievements)


def _achievement_remover(scope: Scope) -> None:
    """Delete the achievements of the mod the character has earned."""
    remove_keys(dict_at(scope.node, "Achievements"), scope.ids.achievements)


def _insurance_trader(entry: Json, ids: ModIds) -> str:
    """Return the mod's trader an insurance return is from, empty if none."""
    if not isinstance(entry, dict):
        return ""
    trader = entry.get("traderId")
    if isinstance(trader, str) and trader in ids.traders:
        return trader
    return ""


def _insurance_finder(scope: Scope) -> Hits:
    """Return pending insurance returns of the mod's traders or items."""
    found: set[str] = set()
    count = 0
    for entry in list_at(scope.node, "insurance"):
        trader = _insurance_trader(entry, scope.ids)
        if trader:
            found.add(trader)
            count += 1
            continue
        items = list_at(entry, "items")
        matched = matching_templates(items, scope.ids.items)
        if matched:
            found |= matched
            count += len(item_closure(items, scope.ids.items))
    return Hits(frozenset(found), count)


def _insurance_remover(scope: Scope) -> None:
    """Delete insurance returns of the mod's traders and its items."""
    filter_list(
        scope.node,
        "insurance",
        lambda entry: not _insurance_trader(entry, scope.ids),
    )
    for entry in list_at(scope.node, "insurance"):
        if isinstance(entry, dict):
            items = list_at(entry, "items")
            remove_items(entry, item_closure(items, scope.ids.items))


def _dialogue_finder(scope: Scope) -> Hits:
    """Return dialogues of the mod's traders and messages about its content."""
    found: set[str] = set()
    count = 0
    for key, dialogue in dict_at(scope.node, "dialogues").items():
        if key in scope.ids.traders:
            found.add(key)
            count += 1
            continue
        for message in list_at(dialogue, "messages"):
            matched = _message_ids(message, scope.ids)
            if matched:
                found |= matched
                count += 1
    return Hits(frozenset(found), count)


def _dialogue_remover(scope: Scope) -> None:
    """Delete the dialogues of the mod's traders and messages about its content."""
    dialogues = dict_at(scope.node, "dialogues")
    remove_keys(dialogues, scope.ids.traders)
    for dialogue in dialogues.values():
        if isinstance(dialogue, dict):
            filter_list(
                dialogue,
                "messages",
                lambda message: not _message_ids(message, scope.ids),
            )


def _message_ids(message: Json, ids: ModIds) -> set[str]:
    """Return the IDs that tie one dialogue message to the mod."""
    if not isinstance(message, dict):
        return set()
    matched = templates_below(message.get("items"), ids.items)
    template = message.get("templateId")
    if isinstance(template, str):
        matched |= {quest for quest in ids.quests if template.startswith(quest)}
    return matched


def _userbuild_finder(scope: Scope) -> Hits:
    """Return saved builds using one of the mod's item templates."""
    builds = dict_at(scope.node, "userbuilds")
    found: set[str] = set()
    count = 0
    for key in BUILD_LIST_KEYS:
        for build in list_at(builds, key):
            matched = templates_below(build, scope.ids.items)
            if matched:
                found |= matched
                count += 1
    return Hits(frozenset(found), count)


def _userbuild_remover(scope: Scope) -> None:
    """Delete the saved builds using one of the mod's item templates."""
    builds = dict_at(scope.node, "userbuilds")
    for key in BUILD_LIST_KEYS:
        filter_list(
            builds,
            key,
            lambda build: not templates_below(build, scope.ids.items),
        )


CATEGORIES: Final[tuple[Category, ...]] = (
    # Residue categories in emission order: character-major, then category-minor.
    Category(
        ResidueCategory.ITEM,
        CHARACTERS,
        "/characters/{character}/Inventory/items",
        _item_finder,
        _item_remover,
    ),
    Category(
        ResidueCategory.ENCYCLOPEDIA,
        CHARACTERS,
        "/characters/{character}/Encyclopedia",
        _encyclopedia_finder,
        _encyclopedia_remover,
    ),
    Category(
        ResidueCategory.WISHLIST,
        CHARACTERS,
        "/characters/{character}/WishList",
        _wishlist_finder,
        _wishlist_remover,
    ),
    Category(
        ResidueCategory.TRADER,
        CHARACTERS,
        "/characters/{character}/TradersInfo",
        _trader_finder,
        _trader_remover,
    ),
    Category(
        ResidueCategory.TRADER_PURCHASE,
        (ResidueCharacter.SHARED,),
        "/traderPurchases",
        _trader_purchase_finder,
        _trader_purchase_remover,
    ),
    Category(
        ResidueCategory.QUEST,
        CHARACTERS,
        "/characters/{character}/Quests",
        _quest_finder,
        _quest_remover,
    ),
    Category(
        ResidueCategory.REPEATABLE_QUEST,
        (ResidueCharacter.PMC,),
        "/characters/{character}/RepeatableQuests",
        _repeatable_quest_finder,
        _repeatable_quest_remover,
    ),
    Category(
        ResidueCategory.TASK_COUNTER,
        CHARACTERS,
        "/characters/{character}/TaskConditionCounters",
        _task_counter_finder,
        _task_counter_remover,
    ),
    Category(
        ResidueCategory.HIDEOUT_PRODUCTION,
        CHARACTERS,
        "/characters/{character}/Hideout/Production",
        _hideout_production_finder,
        _hideout_production_remover,
    ),
    Category(
        ResidueCategory.HIDEOUT_AREA,
        CHARACTERS,
        "/characters/{character}/Hideout/Areas",
        _hideout_area_finder,
        _hideout_area_remover,
    ),
    Category(
        ResidueCategory.UNLOCKED_RECIPE,
        CHARACTERS,
        "/characters/{character}/UnlockedInfo/unlockedProductionRecipe",
        _unlocked_recipe_finder,
        _unlocked_recipe_remover,
    ),
    Category(
        ResidueCategory.INSURED_ITEM,
        CHARACTERS,
        "/characters/{character}/InsuredItems",
        _insured_item_finder,
        _insured_item_remover,
    ),
    Category(
        ResidueCategory.ACHIEVEMENT,
        (ResidueCharacter.PMC,),
        "/characters/{character}/Achievements",
        _achievement_finder,
        _achievement_remover,
    ),
    Category(
        ResidueCategory.INSURANCE,
        (ResidueCharacter.SHARED,),
        "/insurance",
        _insurance_finder,
        _insurance_remover,
    ),
    Category(
        ResidueCategory.DIALOGUE,
        (ResidueCharacter.SHARED,),
        "/dialogues",
        _dialogue_finder,
        _dialogue_remover,
    ),
    Category(
        ResidueCategory.USERBUILD,
        (ResidueCharacter.SHARED,),
        "/userbuilds",
        _userbuild_finder,
        _userbuild_remover,
    ),
)
