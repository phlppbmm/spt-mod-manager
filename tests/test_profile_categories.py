"""Unit tests for the per-category residue table of ``sptmod.core.profile``.

Covers the parametrized table that asserts one hit for every residue
category on the PMC character, the scav character and the top-level profile
document, the tolerance for a missing or wrongly-typed container per
category, the separation of PMC and scav hits, the character-major ordering
of the reported residues and the item closure over children and
grandchildren.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import empty_pmc_profile
from fixture_types import JsonDict, MakeProfile
from sptmod.core.models import ProfileResidue, ResidueCategory
from sptmod.core.profile import ModIds, find_residues

ITEM_A = "5c0d1b8b86f7744bb2683cfa"
ITEM_B = "5c0d1b8b86f7744bb2683cfb"
TRADER_ID = "5ac3b934136fb568fa78b1e2"
QUEST_ID = "quest-mod"
RECIPE_ID = "5c0d1b8b86f7744bb2683cfd"
ACHIEVEMENT_ID = "achievement-mod"


def _make_profile(tmp_path: Path, name: str, content: JsonDict) -> Path:
    """Write a JSON profile file to tmp_path with given content."""
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# find_residues -- category table (pmc, scav, top-level; make_profile fixture)
# --------------------------------------------------------------------------


def _delete_at(doc: JsonDict, path: tuple[str, ...]) -> None:
    """Delete a key at the given path tuple in a nested dict."""
    node = doc
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]


def _corrupt_at(doc: JsonDict, path: tuple[str, ...]) -> None:
    """Replace a value at the given path with a non-container value."""
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = "not-a-container"


@pytest.mark.parametrize(
    ("hit", "character", "category", "path", "ids", "expected_ids", "expected_count"),
    [
        (
            "pmc:item",
            "pmc",
            "item",
            "/characters/pmc/Inventory/items",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            3,
        ),
        (
            "scav:item",
            "scav",
            "item",
            "/characters/scav/Inventory/items",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            3,
        ),
        (
            "pmc:encyclopedia",
            "pmc",
            "encyclopedia",
            "/characters/pmc/Encyclopedia",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
        (
            "scav:encyclopedia",
            "scav",
            "encyclopedia",
            "/characters/scav/Encyclopedia",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
        (
            "pmc:wishlist",
            "pmc",
            "wishlist",
            "/characters/pmc/WishList",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
        (
            "scav:wishlist",
            "scav",
            "wishlist",
            "/characters/scav/WishList",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
        (
            "pmc:trader",
            "pmc",
            "trader",
            "/characters/pmc/TradersInfo",
            ModIds(traders={TRADER_ID}),
            [TRADER_ID],
            1,
        ),
        (
            "scav:trader",
            "scav",
            "trader",
            "/characters/scav/TradersInfo",
            ModIds(traders={TRADER_ID}),
            [TRADER_ID],
            1,
        ),
        (
            ":trader-purchase",
            "",
            "trader-purchase",
            "/traderPurchases",
            ModIds(traders={TRADER_ID}),
            [TRADER_ID],
            1,
        ),
        (
            "pmc:quest",
            "pmc",
            "quest",
            "/characters/pmc/Quests",
            ModIds(quests={QUEST_ID}),
            [QUEST_ID],
            1,
        ),
        (
            "scav:quest",
            "scav",
            "quest",
            "/characters/scav/Quests",
            ModIds(quests={QUEST_ID}),
            [QUEST_ID],
            1,
        ),
        (
            "pmc:task-counter",
            "pmc",
            "task-counter",
            "/characters/pmc/TaskConditionCounters",
            ModIds(quests={QUEST_ID}),
            [QUEST_ID],
            1,
        ),
        (
            "scav:task-counter",
            "scav",
            "task-counter",
            "/characters/scav/TaskConditionCounters",
            ModIds(quests={QUEST_ID}),
            [QUEST_ID],
            1,
        ),
        (
            "pmc:hideout-production",
            "pmc",
            "hideout-production",
            "/characters/pmc/Hideout/Production",
            ModIds(recipes={RECIPE_ID}),
            [RECIPE_ID],
            1,
        ),
        (
            "scav:hideout-production",
            "scav",
            "hideout-production",
            "/characters/scav/Hideout/Production",
            ModIds(recipes={RECIPE_ID}),
            [RECIPE_ID],
            1,
        ),
        (
            "pmc:hideout-area",
            "pmc",
            "hideout-area",
            "/characters/pmc/Hideout/Areas",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
        (
            "scav:hideout-area",
            "scav",
            "hideout-area",
            "/characters/scav/Hideout/Areas",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
        (
            "pmc:unlocked-recipe",
            "pmc",
            "unlocked-recipe",
            "/characters/pmc/UnlockedInfo/unlockedProductionRecipe",
            ModIds(recipes={RECIPE_ID}),
            [RECIPE_ID],
            1,
        ),
        (
            "scav:unlocked-recipe",
            "scav",
            "unlocked-recipe",
            "/characters/scav/UnlockedInfo/unlockedProductionRecipe",
            ModIds(recipes={RECIPE_ID}),
            [RECIPE_ID],
            1,
        ),
        (
            "pmc:achievement",
            "pmc",
            "achievement",
            "/characters/pmc/Achievements",
            ModIds(achievements={ACHIEVEMENT_ID}),
            [ACHIEVEMENT_ID],
            1,
        ),
        (
            "pmc:repeatable-quest:templateId",
            "pmc",
            "repeatable-quest",
            "/characters/pmc/RepeatableQuests",
            ModIds(quests={QUEST_ID}),
            [QUEST_ID],
            1,
        ),
        (
            ":insurance:trader",
            "",
            "insurance",
            "/insurance",
            ModIds(traders={TRADER_ID}),
            [TRADER_ID],
            1,
        ),
        (
            ":dialogue:trader",
            "",
            "dialogue",
            "/dialogues",
            ModIds(traders={TRADER_ID}),
            [TRADER_ID],
            1,
        ),
        (
            "userbuild:weapon",
            "",
            "userbuild",
            "/userbuilds",
            ModIds(items={ITEM_A}),
            [ITEM_A],
            1,
        ),
    ],
)
def test_find_residues_reports_a_hit_for_every_category(
    tmp_path: Path,
    make_profile: MakeProfile,
    hit: str,
    character: str,
    category: ResidueCategory,
    path: str,
    ids: ModIds,
    expected_ids: list[str],
    expected_count: int,
) -> None:
    """All 16 residue categories are detected with correct character scope and path."""
    doc = make_profile(hits=frozenset({hit}))
    profile = _make_profile(tmp_path, "player1", doc)

    residues = find_residues(profile, ids)

    matching = [residue for residue in residues if residue.category == category and residue.character == character]
    assert matching == [
        ProfileResidue(
            profile="player1",
            category=category,
            ids=expected_ids,
            count=expected_count,
            character=character,
            path=path,
        )
    ]


_UNIFORM_CATEGORY_CASES: list[tuple[str, tuple[str, ...], ModIds]] = [
    ("item", ("characters", "pmc", "Inventory", "items"), ModIds(items={ITEM_A})),
    ("encyclopedia", ("characters", "pmc", "Encyclopedia"), ModIds(items={ITEM_A})),
    ("wishlist", ("characters", "pmc", "WishList"), ModIds(items={ITEM_A})),
    ("trader", ("characters", "pmc", "TradersInfo"), ModIds(traders={TRADER_ID})),
    ("trader-purchase", ("traderPurchases",), ModIds(traders={TRADER_ID})),
    ("quest", ("characters", "pmc", "Quests"), ModIds(quests={QUEST_ID})),
    (
        "repeatable-quest",
        ("characters", "pmc", "RepeatableQuests"),
        ModIds(quests={QUEST_ID}),
    ),
    (
        "task-counter",
        ("characters", "pmc", "TaskConditionCounters"),
        ModIds(quests={QUEST_ID}),
    ),
    (
        "hideout-production",
        ("characters", "pmc", "Hideout", "Production"),
        ModIds(recipes={RECIPE_ID}),
    ),
    (
        "hideout-area",
        ("characters", "pmc", "Hideout", "Areas"),
        ModIds(items={ITEM_A}),
    ),
    (
        "unlocked-recipe",
        ("characters", "pmc", "UnlockedInfo", "unlockedProductionRecipe"),
        ModIds(recipes={RECIPE_ID}),
    ),
    (
        "insured-item",
        ("characters", "pmc", "InsuredItems"),
        ModIds(traders={TRADER_ID}, items={ITEM_A}),
    ),
    (
        "achievement",
        ("characters", "pmc", "Achievements"),
        ModIds(achievements={ACHIEVEMENT_ID}),
    ),
    (
        "insurance",
        ("insurance",),
        ModIds(traders={TRADER_ID}, items={ITEM_A}),
    ),
    (
        "dialogue",
        ("dialogues",),
        ModIds(traders={TRADER_ID}, items={ITEM_A}, quests={QUEST_ID}),
    ),
    ("userbuild", ("userbuilds",), ModIds(items={ITEM_A})),
]


@pytest.mark.parametrize("category,path,ids", _UNIFORM_CATEGORY_CASES)
@pytest.mark.parametrize("corrupt", [_delete_at, _corrupt_at])
def test_find_residues_missing_or_wrong_type_container_yields_no_residue(
    tmp_path: Path,
    make_profile: MakeProfile,
    corrupt: Callable[[JsonDict, tuple[str, ...]], None],
    category: str,
    path: tuple[str, ...],
    ids: ModIds,
) -> None:
    """Missing or malformed residue containers degrade to no residue."""
    # ``category`` only labels the case here; the shared case list feeds it to
    # the tests that do check it.
    del category
    doc = make_profile()
    corrupt(doc, path)
    profile = _make_profile(tmp_path, "player1", doc)

    assert find_residues(profile, ids) == []


def test_find_residues_reports_pmc_and_scav_item_hits_separately_with_distinct_ids(
    tmp_path: Path, make_profile: MakeProfile
) -> None:
    """PMC and scav characters are reported separately with distinct matched IDs."""
    doc = make_profile(hits=frozenset({"pmc:item"}), item_id=ITEM_A)
    doc["characters"]["scav"]["Inventory"]["items"][1]["_tpl"] = ITEM_B
    profile = _make_profile(tmp_path, "player1", doc)
    ids = ModIds(items={ITEM_A, ITEM_B})

    residues = find_residues(profile, ids)

    item_residues = {residue.character: residue for residue in residues if residue.category == "item"}
    assert item_residues["pmc"].ids == [ITEM_A]
    assert item_residues["scav"].ids == [ITEM_B]


def test_find_residues_orders_character_major_then_category_then_string_last(
    tmp_path: Path, make_profile: MakeProfile
) -> None:
    """Residues ordered: PMC categories, then scav categories, then shared, then string."""
    marker = "string-only-marker-id"
    doc = make_profile(
        hits=frozenset(
            {
                "pmc:item",
                "pmc:encyclopedia",
                "scav:trader",
                "scav:quest",
                ":trader-purchase",
                ":insurance:trader",
            }
        )
    )
    doc["info"]["nickname"] = f"prefix-{marker}-suffix"
    profile = _make_profile(tmp_path, "player1", doc)
    ids = ModIds(
        items={ITEM_A},
        traders={TRADER_ID},
        quests={QUEST_ID},
        achievements={marker},
    )

    residues = find_residues(profile, ids)

    assert [(residue.character, residue.category) for residue in residues] == [
        ("pmc", "item"),
        ("pmc", "encyclopedia"),
        ("scav", "trader"),
        ("scav", "quest"),
        ("", "trader-purchase"),
        ("", "insurance"),
        ("", "string"),
    ]
    assert residues[-1] == ProfileResidue(profile="player1", category=ResidueCategory.STRING, ids=[marker], count=1)


def test_find_residues_item_closure_counts_children_and_grandchildren_and_is_cycle_safe(
    tmp_path: Path,
) -> None:
    """Item category closure includes nested items via parentId and handles cycles."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        # A parentId cycle between "container" and "child": neither can be
        # revisited once it enters the closure, so the walk still terminates.
        {"_id": "container", "_tpl": ITEM_A, "parentId": "child", "slotId": "main"},
        {"_id": "child", "_tpl": ITEM_B, "parentId": "container", "slotId": "main"},
        {"_id": "grandchild", "_tpl": ITEM_B, "parentId": "child", "slotId": "main"},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category=ResidueCategory.ITEM,
            ids=[ITEM_A],
            count=3,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_item_closure_skips_items_without_a_usable_id(
    tmp_path: Path,
) -> None:
    """Item closure skips entries with missing or non-string _id fields."""
    ids = ModIds(items={ITEM_A})
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {"_id": "container", "_tpl": ITEM_A, "slotId": "main"},
        {"_tpl": ITEM_B, "parentId": "container", "slotId": "main"},
        {"_id": "", "_tpl": ITEM_B, "parentId": "container", "slotId": "main"},
        {"_id": 123, "_tpl": ITEM_B, "parentId": "container", "slotId": "main"},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    # Only "container" itself has a usable string _id, so the closure is size 1:
    # the malformed entries are skipped rather than crashing or being counted.
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


def test_find_residues_string_fallback_excludes_matched_ids_but_keeps_unmatched(
    tmp_path: Path,
) -> None:
    """String fallback excludes IDs matched structurally but keeps others."""
    content = empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "x1",
            "_tpl": ITEM_A,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    # ITEM_A is matched structurally (must not show up as 'string'); RECIPE_ID
    # and ACHIEVEMENT_ID are only embedded in raw text and must still surface.
    content["misc"] = {"note": f"{RECIPE_ID} and {ACHIEVEMENT_ID}"}
    profile = _make_profile(tmp_path, "player1", content)
    ids = ModIds(items={ITEM_A}, recipes={RECIPE_ID}, achievements={ACHIEVEMENT_ID})

    residues = find_residues(profile, ids)

    string_residue = next(r for r in residues if r.category == "string")
    assert string_residue.ids == sorted([RECIPE_ID, ACHIEVEMENT_ID])
    assert all(residue.category != "string" or ITEM_A not in residue.ids for residue in residues)
