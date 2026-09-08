"""Content IDs and profile builders shared by the profile-cleanup tests.

Not a test module: ``test_services_profile_cleanup`` and
``test_services_profile_cleanup_errors`` import these builders so the mod that
provides the content IDs and the profile documents referencing them are
described in one place.
"""

import json
from pathlib import Path

from fixture_types import InstallCommit, JsonDict
from sptmod.core.repo import Repo

# --------------------------------------------------------------------------
# Fixed content IDs shared by every profile-cleanup test. Item and trader IDs
# are 24-character hex, matching the pattern ``_collect_from_file`` requires
# for the generic "dict of dicts" item rule; quest/recipe/achievement IDs are
# collected without a hex requirement, so plain strings are fine.
# --------------------------------------------------------------------------
MOD_ITEM_ID = "5c0d1b8b86f7744bb2683cfa"
OTHER_ITEM_ID = "5c0d1b8b86f7744bb2683c00"
MOD_TRADER_ID = "5ac3b934136fb568fa78b1e2"
OTHER_TRADER_ID = "5ac3b934136fb568fa78b100"
MOD_QUEST_ID = "quest-mod"
OTHER_QUEST_ID = "quest-other"
MOD_RECIPE_ID = "recipe-mod"
OTHER_RECIPE_ID = "recipe-other"
MOD_ACHIEVEMENT_ID = "achievement-mod"
OTHER_ACHIEVEMENT_ID = "achievement-other"
# Collected into ModIds but never placed in a structured container by any
# test profile, so it can only ever be found by the string fallback.
STRING_ONLY_ITEM_ID = "5c0d1b8b86f7744bb2683cbb"

MOD_NAME = "TestMod"


def install_mod(repo: Repo, install_commit: InstallCommit, name: str = MOD_NAME, minute: int = 1) -> str:
    """Install a server mod that provides every content ID used above."""
    return install_commit(
        repo,
        name,
        mod_type="server",
        minute=minute,
        files={
            f"SPT/user/mods/{name}/db/templates/items.json": json.dumps(
                {
                    MOD_ITEM_ID: {"_id": MOD_ITEM_ID, "_name": "item"},
                    STRING_ONLY_ITEM_ID: {
                        "_id": STRING_ONLY_ITEM_ID,
                        "_name": "string-only-item",
                    },
                }
            ),
            f"SPT/user/mods/{name}/db/traders/{MOD_TRADER_ID}/base.json": json.dumps(
                {"_id": MOD_TRADER_ID, "nickname": "Trader"}
            ),
            f"SPT/user/mods/{name}/db/quests/quests.json": json.dumps([{"_id": MOD_QUEST_ID}]),
            f"SPT/user/mods/{name}/db/hideout/production.json": json.dumps([{"_id": MOD_RECIPE_ID}]),
            f"SPT/user/mods/{name}/db/templates/achievements.json": json.dumps([{"id": MOD_ACHIEVEMENT_ID}]),
        },
    )


def write_profile(repo: Repo, stem: str, data: JsonDict) -> Path:
    """Write a profile document and commit it, keeping the tree clean."""
    relpath = Path(f"SPT/user/profiles/{stem}.json")
    profile_path = repo.path / relpath
    profile_path.write_text(json.dumps(data), encoding="utf-8")
    repo.commit("chore: profile snapshot", paths=[relpath])
    return profile_path


def character(name: str, *, achievements: bool, repeatable: bool) -> JsonDict:
    """Build one character with mixed mod/unrelated entries in every container."""
    root_id = f"{name}-root"
    container_id = f"{name}-container"
    child_id = f"{name}-child"
    grandchild_id = f"{name}-grandchild"
    unrelated_id = f"{name}-unrelated"
    naked_slot_item = f"{name}-naked-slot-item"

    data: JsonDict = {
        "Inventory": {
            "items": [
                {"_id": root_id, "_tpl": OTHER_ITEM_ID, "slotId": "stash"},
                {
                    "_id": container_id,
                    "_tpl": MOD_ITEM_ID,
                    "parentId": root_id,
                    "slotId": "main",
                },
                {
                    "_id": child_id,
                    "_tpl": OTHER_ITEM_ID,
                    "parentId": container_id,
                    "slotId": "cartridges",
                },
                {
                    "_id": grandchild_id,
                    "_tpl": OTHER_ITEM_ID,
                    "parentId": child_id,
                    "slotId": "main",
                },
                {
                    "_id": unrelated_id,
                    "_tpl": OTHER_ITEM_ID,
                    "parentId": root_id,
                    "slotId": "pocket1",
                },
            ],
            "favoriteItems": [container_id, unrelated_id],
            "fastPanel": {"0": container_id, "1": unrelated_id},
        },
        "Encyclopedia": {MOD_ITEM_ID: True, OTHER_ITEM_ID: True},
        "WishList": {MOD_ITEM_ID: 1, OTHER_ITEM_ID: 2},
        "TradersInfo": {
            MOD_TRADER_ID: {"salesSum": 0},
            OTHER_TRADER_ID: {"salesSum": 0},
        },
        "Quests": [
            {"qid": MOD_QUEST_ID, "status": 1, "completedConditions": []},
            {"qid": OTHER_QUEST_ID, "status": 1, "completedConditions": []},
        ],
        "TaskConditionCounters": {
            f"{name}-cond-mod": {"id": "c1", "sourceId": MOD_QUEST_ID},
            f"{name}-cond-other": {"id": "c2", "sourceId": OTHER_QUEST_ID},
        },
        "Hideout": {
            "Production": {
                f"{name}-prod-mod": {"recipeId": MOD_RECIPE_ID, "Progress": 0},
                f"{name}-prod-other": {"recipeId": OTHER_RECIPE_ID, "Progress": 0},
            },
            "Areas": [
                {
                    "type": 0,
                    "slots": [
                        {"locationIndex": 0},
                        {
                            "locationIndex": 1,
                            "items": [{"_id": f"{name}-slot-item", "_tpl": MOD_ITEM_ID}],
                        },
                        {
                            "locationIndex": 2,
                            "items": [{"_id": f"{name}-other-slot", "_tpl": OTHER_ITEM_ID}],
                        },
                        {
                            # No int locationIndex: _empty_slot's non-int branch.
                            "items": [{"_id": naked_slot_item, "_tpl": MOD_ITEM_ID}]
                        },
                    ],
                }
            ],
        },
        "UnlockedInfo": {"unlockedProductionRecipe": [MOD_RECIPE_ID, OTHER_RECIPE_ID]},
        "InsuredItems": [
            {"tid": MOD_TRADER_ID, "itemId": "irrelevant-item-id"},
            {"tid": OTHER_TRADER_ID, "itemId": container_id},
            {"tid": OTHER_TRADER_ID, "itemId": "totally-unrelated"},
        ],
    }
    if repeatable:
        data["RepeatableQuests"] = [
            {
                "id": f"{name}-repeatable",
                "name": "Daily",
                "activeQuests": [
                    {"_id": MOD_QUEST_ID, "templateId": "t1", "traderId": "x"},
                    {"_id": "rq-other", "templateId": "t2", "traderId": "y"},
                ],
                "inactiveQuests": [
                    {"_id": "rq-i1", "templateId": "t3", "traderId": MOD_TRADER_ID},
                    {"_id": "rq-i2", "templateId": "t4", "traderId": OTHER_TRADER_ID},
                ],
            }
        ]
    if achievements:
        data["Achievements"] = {
            MOD_ACHIEVEMENT_ID: 1700000000,
            OTHER_ACHIEVEMENT_ID: 1700000001,
        }
    return data


def full_profile() -> JsonDict:
    """A profile document with mixed mod/unrelated content in every container
    the cleanup knows, on both characters and the shared top-level sections.
    """
    return {
        "info": {"nickname": "tester", "note": "Тест"},
        "characters": {
            "pmc": character("pmc", achievements=True, repeatable=True),
            "scav": character("scav", achievements=False, repeatable=False),
        },
        "traderPurchases": {
            MOD_TRADER_ID: {"a": {"count": 1}},
            OTHER_TRADER_ID: {"a": {"count": 1}},
        },
        "insurance": [
            {"traderId": MOD_TRADER_ID, "items": []},
            {
                "traderId": OTHER_TRADER_ID,
                "items": [
                    {"_id": "i1", "_tpl": MOD_ITEM_ID},
                    {"_id": "i2", "_tpl": OTHER_ITEM_ID, "parentId": "i1"},
                    {"_id": "i3", "_tpl": OTHER_ITEM_ID},
                ],
            },
        ],
        "dialogues": {
            MOD_TRADER_ID: {"_id": MOD_TRADER_ID, "messages": []},
            OTHER_TRADER_ID: {
                "_id": OTHER_TRADER_ID,
                "messages": [
                    {
                        "_id": "m1",
                        "templateId": f"{MOD_QUEST_ID}-reward",
                        "items": {},
                    },
                    {
                        "_id": "m2",
                        "templateId": "unrelated",
                        "items": {"data": [{"_tpl": MOD_ITEM_ID}]},
                    },
                    {"_id": "m3", "templateId": "unrelated", "items": {}},
                ],
            },
        },
        "userbuilds": {
            "weaponBuilds": [
                {"Id": "w1", "Items": [{"_tpl": MOD_ITEM_ID}]},
                {"Id": "w2", "Items": [{"_tpl": OTHER_ITEM_ID}]},
            ],
            "equipmentBuilds": [
                {"Id": "e1", "Items": [{"_tpl": MOD_ITEM_ID}]},
                {"Id": "e2", "Items": [{"_tpl": OTHER_ITEM_ID}]},
            ],
            "magazineBuilds": [
                {"Id": "mg1", "Items": [{"_tpl": MOD_ITEM_ID}]},
                {"Id": "mg2", "Items": [{"_tpl": OTHER_ITEM_ID}]},
            ],
        },
    }
