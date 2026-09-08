"""Unit tests for the category-specific matching details of ``find_residues``.

Covers the match variants and non-dict tolerance of the repeatable-quest,
task-counter, hideout-production, insured-item, insurance, dialogue,
userbuild and hideout-area categories, including the "no hit leaves no
residue" case for each of them.
"""

import json
from pathlib import Path

import pytest

from conftest import empty_pmc_profile
from fixture_types import JsonDict
from sptmod.core.models import ProfileResidue, ResidueCategory
from sptmod.core.profile import ModIds, find_residues

ITEM_A = "5c0d1b8b86f7744bb2683cfa"
ITEM_B = "5c0d1b8b86f7744bb2683cfb"
TRADER_ID = "5ac3b934136fb568fa78b1e2"
QUEST_ID = "quest-mod"
RECIPE_ID = "5c0d1b8b86f7744bb2683cfd"


def _make_profile(tmp_path: Path, name: str, content: JsonDict) -> Path:
    """Write a JSON profile file to tmp_path with given content."""
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# find_residues -- repeatable-quest match variants (traderId, _id, templateId,
# questStatus.qid) and non-dict entries
# --------------------------------------------------------------------------


def _pmc_with_repeatable(active: list[JsonDict]) -> JsonDict:
    """Build a PMC profile with a repeatable-quests structure."""
    content = empty_pmc_profile()
    content["characters"]["pmc"]["RepeatableQuests"] = [
        {
            "id": "daily",
            "name": "Daily",
            "activeQuests": active,
            "inactiveQuests": [],
        }
    ]
    return content


@pytest.mark.parametrize(
    "quest_entry",
    [
        {"_id": QUEST_ID, "templateId": "other", "traderId": "other-trader"},
        {"_id": "other", "templateId": QUEST_ID, "traderId": "other-trader"},
        {
            "_id": "other",
            "templateId": "other",
            "traderId": "other-trader",
            "questStatus": {"qid": QUEST_ID},
        },
    ],
)
def test_find_residues_repeatable_quest_matches_by_id_templateid_or_qid(tmp_path: Path, quest_entry: JsonDict) -> None:
    """Repeatable-quest category matches via _id, templateId, or questStatus.qid."""
    ids = ModIds(quests={QUEST_ID})
    content = _pmc_with_repeatable([quest_entry])
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.REPEATABLE_QUEST,
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/RepeatableQuests",
        )
    ]


def test_find_residues_repeatable_quest_matches_by_trader_id(tmp_path: Path) -> None:
    """Repeatable-quest also matches when traderId field references a mod trader."""
    ids = ModIds(traders={TRADER_ID})
    content = _pmc_with_repeatable([{"_id": "other", "templateId": "other", "traderId": TRADER_ID}])
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.REPEATABLE_QUEST,
            ids=[TRADER_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/RepeatableQuests",
        )
    ]


def test_find_residues_repeatable_quest_skips_non_dict_groups_and_entries(
    tmp_path: Path,
) -> None:
    """Non-dict entries in RepeatableQuests groups and quest lists are skipped."""
    ids = ModIds(quests={QUEST_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["RepeatableQuests"] = [
        "not-a-dict",
        {
            "id": "daily",
            "name": "Daily",
            "activeQuests": ["not-a-dict", {"_id": QUEST_ID}],
            "inactiveQuests": [{"_id": "other"}],
        },
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.REPEATABLE_QUEST,
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/RepeatableQuests",
        )
    ]


def test_find_residues_repeatable_quest_no_hit_leaves_no_residue(
    tmp_path: Path,
) -> None:
    """No repeatable-quest residue when entry IDs and traders don't match."""
    ids = ModIds(quests={QUEST_ID}, traders={TRADER_ID})
    content = _pmc_with_repeatable([{"_id": "other", "templateId": "other", "traderId": "other-trader"}])
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- task-counter and hideout-production: key vs. nested field
# --------------------------------------------------------------------------


def test_find_residues_task_counter_matches_by_key(tmp_path: Path) -> None:
    """Task-counter category matches when the dict key is a mod quest ID."""
    ids = ModIds(quests={QUEST_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["TaskConditionCounters"] = {
        QUEST_ID: {"id": QUEST_ID, "type": "Counter", "value": 1, "sourceId": "other"}
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.TASK_COUNTER,
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/TaskConditionCounters",
        )
    ]


def test_find_residues_task_counter_matches_by_source_id(tmp_path: Path) -> None:
    """Task-counter also matches when entry's sourceId field references a mod quest."""
    ids = ModIds(quests={QUEST_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["TaskConditionCounters"] = {
        "cond1": {"id": "cond1", "type": "Counter", "value": 1, "sourceId": QUEST_ID}
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.TASK_COUNTER,
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/TaskConditionCounters",
        )
    ]


def test_find_residues_task_counter_tolerates_a_non_dict_value_and_no_hit(
    tmp_path: Path,
) -> None:
    """Non-dict values in TaskConditionCounters dict are tolerated."""
    ids = ModIds(quests={QUEST_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["TaskConditionCounters"] = {"cond1": "not-a-dict"}
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


def test_find_residues_hideout_production_matches_by_key(tmp_path: Path) -> None:
    """Hideout-production category matches when the Production dict key is a recipe ID."""
    ids = ModIds(recipes={RECIPE_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {"Production": {RECIPE_ID: {"Progress": 0}}}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.HIDEOUT_PRODUCTION,
            ids=[RECIPE_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/Hideout/Production",
        )
    ]


def test_find_residues_hideout_production_matches_by_recipe_id(
    tmp_path: Path,
) -> None:
    """Hideout-production also matches when entry's recipeId field is a mod recipe."""
    ids = ModIds(recipes={RECIPE_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {"Production": {"prod1": {"recipeId": RECIPE_ID, "Progress": 0}}}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.HIDEOUT_PRODUCTION,
            ids=[RECIPE_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/Hideout/Production",
        )
    ]


def test_find_residues_hideout_production_tolerates_a_non_dict_value_and_no_hit(
    tmp_path: Path,
) -> None:
    """Non-dict values in Production dict are tolerated."""
    ids = ModIds(recipes={RECIPE_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {"Production": {"prod1": "not-a-dict"}}
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- insured-item: trader id or item closure
# --------------------------------------------------------------------------


def test_find_residues_insured_item_matches_by_trader_id(tmp_path: Path) -> None:
    """Insured-item category matches when entry's tid field is a mod trader."""
    ids = ModIds(traders={TRADER_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["InsuredItems"] = [{"tid": TRADER_ID, "itemId": "x"}]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.INSURED_ITEM,
            ids=[TRADER_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/InsuredItems",
        )
    ]


def test_find_residues_insured_item_matches_by_item_closure(tmp_path: Path) -> None:
    """Insured-item also matches via inventory item closure referenced by itemId."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {"_id": "container", "_tpl": ITEM_A, "slotId": "main"},
        {"_id": "child", "_tpl": ITEM_B, "parentId": "container", "slotId": "main"},
    ]
    content["characters"]["pmc"]["InsuredItems"] = [{"tid": "unrelated-trader", "itemId": "child"}]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    insured = next(r for r in residues if r.category == "insured-item")
    assert insured.ids == ["child"]
    assert insured.count == 1


def test_find_residues_insured_item_skips_non_dict_entries_and_no_hit(
    tmp_path: Path,
) -> None:
    """Non-dict entries in InsuredItems array are skipped."""
    ids = ModIds(traders={TRADER_ID}, items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["InsuredItems"] = [
        "not-a-dict",
        {"tid": "other-trader", "itemId": "unrelated-item"},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- insurance: trader id, or nested item _tpl plus its closure
# --------------------------------------------------------------------------


def test_find_residues_insurance_matches_by_trader_id(tmp_path: Path) -> None:
    """Insurance category matches when entry's traderId field is a mod trader."""
    ids = ModIds(traders={TRADER_ID})
    content = empty_pmc_profile()
    content["insurance"] = [{"traderId": TRADER_ID, "items": []}]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.INSURANCE,
            ids=[TRADER_ID],
            count=1,
            character="",
            path="/insurance",
        )
    ]


def test_find_residues_insurance_matches_by_item_and_its_closure(
    tmp_path: Path,
) -> None:
    """Insurance also matches entries holding mod items with their nested children."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["insurance"] = [
        {
            "traderId": "unrelated-trader",
            "items": [
                {"_id": "i1", "_tpl": ITEM_A},
                {"_id": "i2", "_tpl": ITEM_B, "parentId": "i1"},
            ],
        }
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.INSURANCE,
            ids=[ITEM_A],
            count=2,
            character="",
            path="/insurance",
        )
    ]


def test_find_residues_insurance_skips_non_dict_entries_and_no_hit(
    tmp_path: Path,
) -> None:
    """Non-dict entries in insurance array are skipped."""
    ids = ModIds(traders={TRADER_ID}, items={ITEM_A})
    content = empty_pmc_profile()
    content["insurance"] = ["not-a-dict", {"traderId": "other", "items": []}]
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- dialogue: trader key, message templateId prefix, message items
# --------------------------------------------------------------------------


def test_find_residues_dialogue_matches_by_trader_key(tmp_path: Path) -> None:
    """Dialogue category matches when the dialogues dict key is a mod trader."""
    ids = ModIds(traders={TRADER_ID})
    content = empty_pmc_profile()
    content["dialogues"] = {TRADER_ID: {"_id": TRADER_ID, "messages": []}}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.DIALOGUE,
            ids=[TRADER_ID],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_matches_message_by_template_id_prefix(
    tmp_path: Path,
) -> None:
    """Dialogue messages match when templateId starts with a mod quest ID."""
    ids = ModIds(quests={QUEST_ID})
    content = empty_pmc_profile()
    content["dialogues"] = {
        "unrelated-trader": {
            "_id": "unrelated-trader",
            "messages": [{"_id": "m1", "templateId": f"{QUEST_ID}-suffix", "items": {}}],
        }
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.DIALOGUE,
            ids=[QUEST_ID],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_matches_message_by_item_tpl(tmp_path: Path) -> None:
    """Dialogue messages match when their items hold mod item templates."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["dialogues"] = {
        "unrelated-trader": {
            "_id": "unrelated-trader",
            "messages": [
                {
                    "_id": "m1",
                    "templateId": "unrelated",
                    "items": {"data": [{"_id": "i1", "_tpl": ITEM_A}]},
                }
            ],
        }
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.DIALOGUE,
            ids=[ITEM_A],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_skips_non_dict_messages(tmp_path: Path) -> None:
    """Non-dict entries in dialogue messages array are skipped."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["dialogues"] = {
        "unrelated-trader": {
            "_id": "unrelated-trader",
            "messages": [
                "not-a-dict",
                {"_id": "m1", "templateId": "unrelated", "items": {}},
            ],
        }
    }
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


def test_find_residues_dialogue_matches_message_by_item_when_template_id_is_missing(
    tmp_path: Path,
) -> None:
    """Dialogue message items are still checked even when templateId is absent."""
    ids = ModIds(items={ITEM_A}, quests={QUEST_ID})
    content = empty_pmc_profile()
    content["dialogues"] = {
        "unrelated-trader": {
            "_id": "unrelated-trader",
            "messages": [
                {
                    "_id": "m1",
                    "items": {"data": [{"_id": "i1", "_tpl": ITEM_A}]},
                }
            ],
        }
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.DIALOGUE,
            ids=[ITEM_A],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_no_hit_leaves_no_residue(tmp_path: Path) -> None:
    """No dialogue residue when trader keys and message IDs/items don't match."""
    ids = ModIds(traders={TRADER_ID}, items={ITEM_A}, quests={QUEST_ID})
    content = empty_pmc_profile()
    content["dialogues"] = {
        "unrelated-trader": {
            "_id": "unrelated-trader",
            "messages": [
                {"_id": "m1", "templateId": "unrelated", "items": {}},
            ],
        }
    }
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- userbuild: nested _tpl in weapon, equipment and magazine builds
# --------------------------------------------------------------------------


@pytest.mark.parametrize("build_key", ["weaponBuilds", "equipmentBuilds", "magazineBuilds"])
def test_find_residues_userbuild_matches_all_three_build_lists(tmp_path: Path, build_key: str) -> None:
    """Userbuild category matches items in weaponBuilds, equipmentBuilds, magazineBuilds."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["userbuilds"] = {
        "weaponBuilds": [],
        "equipmentBuilds": [],
        "magazineBuilds": [],
    }
    content["userbuilds"][build_key] = [{"Id": "b1", "Name": "Build", "Items": [{"_id": "i1", "_tpl": ITEM_A}]}]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.USERBUILD,
            ids=[ITEM_A],
            count=1,
            character="",
            path="/userbuilds",
        )
    ]


def test_find_residues_userbuild_no_hit_leaves_no_residue(tmp_path: Path) -> None:
    """No userbuild residue when build items don't reference mod items."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["userbuilds"] = {
        "weaponBuilds": [{"Id": "b1", "Name": "Build", "Items": [{"_id": "i1", "_tpl": ITEM_B}]}],
        "equipmentBuilds": [],
        "magazineBuilds": [],
    }
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- hideout-area: nested _tpl anywhere below Areas[].slots[]
# --------------------------------------------------------------------------


def test_find_residues_hideout_area_matches_a_nested_tpl_below_slots(
    tmp_path: Path,
) -> None:
    """Hideout-area category matches items nested anywhere below Areas[].slots[]."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {
        "Areas": [
            {
                "type": 0,
                "slots": [
                    {"locationIndex": 0},
                    {
                        "locationIndex": 1,
                        "items": [{"_id": "x1", "_tpl": ITEM_A}],
                    },
                ],
            }
        ]
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.HIDEOUT_AREA,
            ids=[ITEM_A],
            count=1,
            character="pmc",
            path="/characters/pmc/Hideout/Areas",
        )
    ]


def test_find_residues_hideout_area_no_hit_leaves_no_residue(
    tmp_path: Path,
) -> None:
    """No hideout-area residue when slots don't hold mod items."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {"Areas": [{"type": 0, "slots": [{"locationIndex": 0}]}]}
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []
