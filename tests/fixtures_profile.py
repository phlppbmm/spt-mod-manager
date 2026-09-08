"""Profile fixtures for the sptmod test suite.

Registered through ``pytest_plugins`` in :mod:`conftest`.
"""

import pytest

from fixture_types import JsonDict, MakeProfile


@pytest.fixture
def make_profile() -> MakeProfile:
    """Build a realistic minimal SPT 4.1.3 profile document.

    Both characters (``pmc``, ``scav``), every container the residue category
    table (``sptmod.core.profile.categories.CATEGORIES``) knows, and a nested inventory
    per character (a stash root, a container item, a child inside it via
    ``parentId`` and a grandchild inside the child), so the ``parentId``
    closure is exercised. Every container is always populated -- with the
    "other" id by default -- so a test proves a placement is a genuine miss,
    not an absent container.

    Returns a callable
    ``make_profile(hits=frozenset(), item_id=..., other_item_id=..., ...)
    -> dict``. ``hits`` is a set of ``"<character>:<category>"`` tokens
    (``character`` is ``"pmc"``, ``"scav"`` or ``""`` for the shared
    sections), each swapping that placement's id for the mod's id instead of
    the unrelated one; some categories (``repeatable-quest``, ``dialogue``,
    ``insurance``) additionally accept a ``:<field>`` suffix to select which
    matching field carries the mod's id, e.g. ``"pmc:repeatable-quest:qid"``.
    """

    def _make(
        *,
        hits: frozenset[str] = frozenset(),
        item_id: str = "5c0d1b8b86f7744bb2683cfa",
        other_item_id: str = "5c0d1b8b86f7744bb2683c00",
        trader_id: str = "5ac3b934136fb568fa78b1e2",
        other_trader_id: str = "5ac3b934136fb568fa78b100",
        quest_id: str = "quest-mod",
        other_quest_id: str = "quest-unrelated",
        recipe_id: str = "5c0d1b8b86f7744bb2683cfd",
        other_recipe_id: str = "5c0d1b8b86f7744bb2683c01",
        achievement_id: str = "achievement-mod",
        other_achievement_id: str = "achievement-unrelated",
    ) -> JsonDict:
        def v(placement: str, mod_value: object, other_value: object) -> object:
            return mod_value if placement in hits else other_value

        def character(name: str, *, achievements: bool, repeatable: bool) -> JsonDict:
            root_id = f"{name}-stash-root"
            container_id = f"{name}-container"
            child_id = f"{name}-child"
            grandchild_id = f"{name}-grandchild"
            unrelated_id = f"{name}-unrelated"
            data: JsonDict = {
                "Inventory": {
                    "items": [
                        {"_id": root_id, "_tpl": other_item_id, "slotId": "stash"},
                        {
                            "_id": container_id,
                            "_tpl": v(f"{name}:item", item_id, other_item_id),
                            "parentId": root_id,
                            "slotId": "main",
                        },
                        {
                            "_id": child_id,
                            "_tpl": other_item_id,
                            "parentId": container_id,
                            "slotId": "cartridges",
                        },
                        {
                            "_id": grandchild_id,
                            "_tpl": other_item_id,
                            "parentId": child_id,
                            "slotId": "main",
                        },
                        {
                            "_id": unrelated_id,
                            "_tpl": other_item_id,
                            "parentId": root_id,
                            "slotId": "pocket1",
                        },
                    ],
                    "equipment": root_id,
                    "stash": root_id,
                    "sortingTable": f"{name}-sorting",
                    "questRaidItems": f"{name}-raid",
                    "questStashItems": f"{name}-stash-items",
                    "hideoutAreaStashes": {},
                    "fastPanel": {},
                    "favoriteItems": [],
                    "hideoutCustomizationStashId": f"{name}-customization-stash",
                },
                "Encyclopedia": {v(f"{name}:encyclopedia", item_id, other_item_id): True},
                "WishList": {v(f"{name}:wishlist", item_id, other_item_id): 1},
                "TradersInfo": {v(f"{name}:trader", trader_id, other_trader_id): {"salesSum": 0}},
                "Quests": [
                    {
                        "qid": v(f"{name}:quest", quest_id, other_quest_id),
                        "status": 1,
                        "statusTimers": {},
                        "completedConditions": [],
                        "availableAfter": 0,
                    }
                ],
                "TaskConditionCounters": {
                    f"{name}-cond": {
                        "id": f"{name}-cond",
                        "type": "Counter",
                        "value": 1,
                        "sourceId": v(f"{name}:task-counter", quest_id, other_quest_id),
                    }
                },
                "Hideout": {
                    "Production": {
                        f"{name}-prod": {
                            "recipeId": v(
                                f"{name}:hideout-production",
                                recipe_id,
                                other_recipe_id,
                            ),
                            "Progress": 0,
                        }
                    },
                    "Areas": [
                        {
                            "type": 0,
                            "level": 1,
                            "active": True,
                            "passiveBonusesEnabled": True,
                            "completeTime": 0,
                            "constructing": False,
                            "slots": [
                                {"locationIndex": 0},
                                {
                                    "locationIndex": 1,
                                    "items": [
                                        {
                                            "_id": f"{name}-slot-item",
                                            "_tpl": v(
                                                f"{name}:hideout-area",
                                                item_id,
                                                other_item_id,
                                            ),
                                        }
                                    ],
                                },
                            ],
                            "lastRecipe": "",
                        }
                    ],
                    "Improvements": {},
                    "HideoutCounters": None,
                    "Seed": 1,
                    "MannequinPoses": {},
                    "sptUpdateLastRunTimestamp": 0,
                    "Customization": {},
                },
                "UnlockedInfo": {
                    "unlockedProductionRecipe": [v(f"{name}:unlocked-recipe", recipe_id, other_recipe_id)]
                },
                "InsuredItems": [
                    {
                        "tid": v(f"{name}:insured-item:tid", trader_id, other_trader_id),
                        "itemId": v(f"{name}:insured-item:itemId", container_id, unrelated_id),
                    }
                ],
            }
            if repeatable:
                data["RepeatableQuests"] = [
                    {
                        "id": f"{name}-repeatable",
                        "name": "Daily",
                        "activeQuests": [
                            {
                                "_id": v(f"{name}:repeatable-quest:id", quest_id, "rq-id"),
                                "templateId": v(
                                    f"{name}:repeatable-quest:templateId",
                                    quest_id,
                                    "rq-template",
                                ),
                                "traderId": v(
                                    f"{name}:repeatable-quest:traderId",
                                    trader_id,
                                    other_trader_id,
                                ),
                                "questStatus": {
                                    "id": "rq-status-id",
                                    "uid": f"{name}-uid",
                                    "qid": v(
                                        f"{name}:repeatable-quest:qid",
                                        quest_id,
                                        "rq-qid",
                                    ),
                                },
                            }
                        ],
                        "inactiveQuests": [],
                        "endTime": 0,
                    }
                ]
            if achievements:
                data["Achievements"] = {v("pmc:achievement", achievement_id, other_achievement_id): (1700000000)}
            return data

        return {
            "info": {"nickname": "tester"},
            "characters": {
                "pmc": character("pmc", achievements=True, repeatable=True),
                "scav": character("scav", achievements=False, repeatable=False),
            },
            "userbuilds": {
                "weaponBuilds": [
                    {
                        "Id": "build-1",
                        "Name": "Build 1",
                        "Items": [
                            {
                                "_id": "build-item-1",
                                "_tpl": v("userbuild:weapon", item_id, other_item_id),
                            }
                        ],
                    }
                ],
                "equipmentBuilds": [
                    {
                        "Id": "build-2",
                        "Name": "Build 2",
                        "Items": [
                            {
                                "_id": "build-item-2",
                                "_tpl": v("userbuild:equipment", item_id, other_item_id),
                            }
                        ],
                    }
                ],
                "magazineBuilds": [
                    {
                        "Id": "build-3",
                        "Name": "Build 3",
                        "Items": [
                            {
                                "_id": "build-item-3",
                                "_tpl": v("userbuild:magazine", item_id, other_item_id),
                            }
                        ],
                    }
                ],
            },
            "dialogues": {
                v(":dialogue:trader", trader_id, other_trader_id): {
                    "_id": v(":dialogue:trader", trader_id, other_trader_id),
                    "type": 2,
                    "new": 0,
                    "attachmentsNew": 0,
                    "pinned": False,
                    "messages": [
                        {
                            "_id": "message-1",
                            "uid": other_trader_id,
                            "type": 2,
                            "dt": 0,
                            "templateId": v(
                                ":dialogue:templateId",
                                f"{quest_id}-reward",
                                "unrelated-template-id",
                            ),
                            "text": "",
                            "hasRewards": False,
                            "rewardCollected": False,
                            "items": {
                                "data": [
                                    {
                                        "_id": "message-item-1",
                                        "_tpl": v(":dialogue:items", item_id, other_item_id),
                                    }
                                ]
                            },
                        }
                    ],
                }
            },
            "insurance": [
                {
                    "scheduledTime": 0,
                    "traderId": v(":insurance:trader", trader_id, other_trader_id),
                    "maxStorageTime": 0,
                    "systemData": {},
                    "messageType": 0,
                    "messageTemplateId": "",
                    "items": [
                        {
                            "_id": "insured-1",
                            "_tpl": v(":insurance:item", item_id, other_item_id),
                        },
                        {
                            "_id": "insured-2",
                            "_tpl": other_item_id,
                            "parentId": "insured-1",
                        },
                    ],
                }
            ],
            "traderPurchases": {
                v(":trader-purchase", trader_id, other_trader_id): {"assort-1": {"count": 1, "purchaseTimestamp": 0}}
            },
            "customisationUnlocks": [{"id": "unlock-1", "source": "default", "type": "suite"}],
            "spt": {"version": "4.1.3", "mods": []},
        }

    return _make


def empty_pmc_profile() -> JsonDict:
    """Return a minimal valid PMC profile with empty containers."""
    return {
        "characters": {
            "pmc": {
                "Inventory": {"items": []},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }


def profile_with_item(item_id: str) -> JsonDict:
    """Build a profile document containing an inventory item with the given ID."""
    return {
        "characters": {
            "pmc": {
                "Inventory": {
                    "items": [
                        {
                            "_id": "i1",
                            "_tpl": item_id,
                            "parentId": "5c0d1b8b86f7744bb2683c99",
                            "slotId": "hideout",
                        }
                    ]
                },
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
