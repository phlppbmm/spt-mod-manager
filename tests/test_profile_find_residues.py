"""Unit tests for the basic ``sptmod.core.profile.find_residues`` matching rules.

Covers the structural hits for inventory items, trader info keys and quest
``qid`` entries, the string fallback and its exclusion of IDs already matched
structurally, the ordering of the reported categories, deduplication and
sorting of IDs, and the tolerance for missing, malformed or non-object
profile JSON.
"""

import json
from pathlib import Path

from conftest import empty_pmc_profile
from fixture_types import JsonDict
from sptmod.core.models import ProfileResidue, ResidueCategory
from sptmod.core.profile import ModIds, find_residues

ITEM_A = "5c0d1b8b86f7744bb2683cfa"
ITEM_B = "5c0d1b8b86f7744bb2683cfb"
TRADER_ID = "5ac3b934136fb568fa78b1e2"


def _make_profile(tmp_path: Path, name: str, content: JsonDict) -> Path:
    """Write a JSON profile file to tmp_path with given content."""
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# find_residues
# --------------------------------------------------------------------------


def test_find_residues_reports_item_hits_from_inventory_items(tmp_path: Path) -> None:
    """find_residues detects inventory items whose template ID matches a mod item."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "x1",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        },
        {
            "_id": "x2",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        },
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.ITEM,
            ids=[ITEM_A],
            count=2,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_legacy_tpl_key_is_not_matched_structurally(
    tmp_path: Path,
) -> None:
    """SPT 4.x profiles key the item template as ``_tpl``, not ``tpl``.

    An inventory item stored under the legacy ``tpl`` key must not be
    reported as an 'item' residue; it can only surface through the raw-text
    'string' fallback, since the structural match looks at ``_tpl``.
    """
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "x1",
            "tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [ProfileResidue(profile="player1", category=ResidueCategory.STRING, ids=[ITEM_A], count=1)]


def test_find_residues_reports_trader_hits_from_traders_info_keys(
    tmp_path: Path,
) -> None:
    """Trader IDs in TradersInfo dict keys are reported as residue."""
    ids = ModIds(traders={TRADER_ID})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["TradersInfo"] = {TRADER_ID: {"salesSum": 0}}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.TRADER,
            ids=[TRADER_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/TradersInfo",
        )
    ]


def test_find_residues_reports_quest_hits_from_quests_qid(tmp_path: Path) -> None:
    """Quest IDs in Quests array qid fields are reported as residue."""
    ids = ModIds(quests={"quest-one"})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Quests"] = [
        {"qid": "quest-one", "status": 1},
        {"qid": "quest-one", "status": 4},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.QUEST,
            ids=["quest-one"],
            count=2,
            character="pmc",
            path="/characters/pmc/Quests",
        )
    ]


def test_find_residues_string_fallback_finds_id_embedded_elsewhere(
    tmp_path: Path,
) -> None:
    """String category catches IDs found in raw text but not structurally."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["someOtherSection"] = {"lastEquippedTpl": ITEM_A}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [ProfileResidue(profile="player1", category=ResidueCategory.STRING, ids=[ITEM_A], count=1)]


def test_find_residues_string_fallback_excludes_ids_already_matched_structurally(
    tmp_path: Path,
) -> None:
    """String category excludes IDs already matched by structural rules."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "x1",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    content["backup"] = {"_tpl": ITEM_A}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    categories = {residue.category for residue in residues}
    assert categories == {"item"}


def test_find_residues_orders_categories_item_trader_quest_string(
    tmp_path: Path,
) -> None:
    """Residues are ordered by category: item, trader, quest, then string."""
    string_only_id = "loose-mod-marker-id"
    ids = ModIds(items={ITEM_A}, traders={TRADER_ID}, quests={"quest-one", string_only_id})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "x1",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    content["characters"]["pmc"]["TradersInfo"] = {TRADER_ID: {}}
    content["characters"]["pmc"]["Quests"] = [{"qid": "quest-one"}]
    content["misc"] = {"note": string_only_id}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert [residue.category for residue in residues] == [
        "item",
        "trader",
        "quest",
        "string",
    ]


def test_find_residues_ids_are_distinct_and_sorted(tmp_path: Path) -> None:
    """Residue IDs list is sorted and deduplicated; count includes all matches."""
    ids = ModIds(items={ITEM_B, ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "x1",
            "_tpl": ITEM_B,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        },
        {
            "_id": "x2",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        },
        {
            "_id": "x3",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        },
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.ITEM,
            ids=[ITEM_A, ITEM_B],
            count=3,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_returns_empty_list_for_a_clean_profile(tmp_path: Path) -> None:
    """find_residues returns empty list when profile contains no mod IDs."""
    ids = ModIds(items={ITEM_A}, traders={TRADER_ID}, quests={"quest-one"})
    profile = _make_profile(tmp_path, "player1", empty_pmc_profile())

    assert find_residues(profile, ids) == []


def test_find_residues_returns_empty_list_for_malformed_profile_json(
    tmp_path: Path,
) -> None:
    """find_residues returns empty list for profiles with invalid JSON."""
    profile = tmp_path / "broken.json"
    profile.write_text("{not valid json", encoding="utf-8")

    assert find_residues(profile, ModIds(items={ITEM_A})) == []


def test_find_residues_returns_empty_list_for_a_missing_profile(
    tmp_path: Path,
) -> None:
    """find_residues returns empty list when profile file does not exist."""
    profile = tmp_path / "missing.json"

    assert find_residues(profile, ModIds(items={ITEM_A})) == []


def test_find_residues_returns_empty_list_for_a_valid_json_non_object_root(
    tmp_path: Path,
) -> None:
    """find_residues returns empty list when JSON root is not an object."""
    profile = tmp_path / "array-root.json"
    profile.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert find_residues(profile, ModIds(items={ITEM_A})) == []


def test_find_residues_malformed_inventory_items_is_ignored_without_crashing(
    tmp_path: Path,
) -> None:
    """find_residues tolerates Inventory.items that is not a list."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = "not-a-list"
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


def test_find_residues_skips_non_dict_entries_in_inventory_items(
    tmp_path: Path,
) -> None:
    """Non-dict entries in inventory items array are skipped without crashing."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        "not-a-dict",
        {
            "_id": "x1",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        },
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.ITEM,
            ids=[ITEM_A],
            count=1,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_skips_non_dict_entries_in_quests_but_counts_the_rest(
    tmp_path: Path,
) -> None:
    """Non-dict entries in Quests array are skipped; valid matches are counted."""
    ids = ModIds(quests={"quest-one"})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Quests"] = [
        "not-a-dict",
        {"qid": "some-other-quest", "status": 1},
        {"qid": "quest-one", "status": 4},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.QUEST,
            ids=["quest-one"],
            count=1,
            character="pmc",
            path="/characters/pmc/Quests",
        )
    ]
