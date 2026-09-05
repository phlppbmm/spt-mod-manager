"""Unit tests for ``sptmod.core.profile``.

Covers ``collect_mod_ids`` (reading db content from an install commit, never
the working tree, tolerating broken JSON), ``find_residues`` (matching a
mod's IDs against a player profile, per-category counts, the string
fallback and its exclusion of structurally-matched IDs), ``profile_paths``
and ``check_all``.

CONTRACT CHANGE (spt-mod-manager-fyx): ``collect_mod_ids``, ``profile_paths``
and ``check_all`` now take a ``GameLayout`` instead of a bare game-dir
``Path``, so the whitelisted db/profile paths are relative to the detected
runtime directory. Updated accordingly; the ``SPT_Runtime`` counterpart of
this layout-awareness is covered in tests/test_game_layout.py.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from fixture_types import InstallCommit, JsonDict, MakeProfile
from sptmod.core.errors import GitError
from sptmod.core.game_dir import GameLayout, detect_layout
from sptmod.core.models import ProfileResidue
from sptmod.core.profile import (
    ModIds,
    check_all,
    collect_mod_ids,
    find_residues,
    profile_paths,
)
from sptmod.core.repo import Repo

ITEM_A = "5c0d1b8b86f7744bb2683cfa"
ITEM_B = "5c0d1b8b86f7744bb2683cfb"
ITEM_C = "5c0d1b8b86f7744bb2683cfc"
TRADER_ID = "5ac3b934136fb568fa78b1e2"
QUEST_ID = "quest-mod"
RECIPE_ID = "5c0d1b8b86f7744bb2683cfd"
ACHIEVEMENT_ID = "achievement-mod"


def _make_profile(tmp_path: Path, name: str, content: JsonDict) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _empty_pmc_profile() -> JsonDict:
    return {
        "characters": {
            "pmc": {
                "Inventory": {"items": []},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }


# --------------------------------------------------------------------------
# collect_mod_ids
# --------------------------------------------------------------------------


def test_collect_mod_ids_reads_item_trader_and_quest_ids_from_the_commit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps(
            {
                ITEM_A: {"_id": ITEM_A, "_name": "item_a"},
                ITEM_B: {"_id": ITEM_B, "_name": "item_b"},
            }
        ),
        f"SPT/user/mods/ServerMod/db/traders/{TRADER_ID}/base.json": json.dumps(
            {"_id": TRADER_ID, "nickname": "Test Trader"}
        ),
        "SPT/user/mods/ServerMod/db/quests/quests.json": json.dumps(
            [{"_id": "quest-one"}, {"_id": "quest-two"}]
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    # Mutate/delete the working tree after the commit: collect_mod_ids must
    # read from the commit only, never the working tree.
    (
        clean_repo.path / "SPT/user/mods/ServerMod/db/templates/items.json"
    ).write_text("{}", encoding="utf-8")
    shutil.rmtree(clean_repo.path / "SPT/user/mods/ServerMod/db/traders")
    (clean_repo.path / "SPT/user/mods/ServerMod/db/quests/quests.json").unlink()

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.items == {ITEM_A, ITEM_B}
    assert result.traders == {TRADER_ID}
    assert result.quests == {"quest-one", "quest-two"}
    assert result.warnings == []


def test_collect_mod_ids_trader_id_includes_both_folder_name_and_base_json_id(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    folder_name = "customtrader"
    files = {
        f"SPT/user/mods/ServerMod/db/traders/{folder_name}/base.json": json.dumps(
            {"_id": TRADER_ID, "nickname": "Custom Trader"}
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.traders == {folder_name, TRADER_ID}


def test_collect_mod_ids_finds_item_ids_below_an_items_path_segment(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/templates/items/custom.json": json.dumps(
            {"_id": ITEM_C, "_name": "custom_item"}
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.items == {ITEM_C}


def test_collect_mod_ids_broken_json_warns_and_keeps_scanning_other_files(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/templates/items.json": "{not valid json",
        f"SPT/user/mods/ServerMod/db/traders/{TRADER_ID}/base.json": json.dumps(
            {"_id": TRADER_ID, "nickname": "Test Trader"}
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.traders == {TRADER_ID}
    assert result.items == set()
    assert len(result.warnings) == 1
    assert "items.json" in result.warnings[0]


def test_collect_mod_ids_ignores_files_under_the_mod_dir_but_not_below_db(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/assets/icon.json": json.dumps({"color": "red"}),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result == ModIds()


def test_collect_mod_ids_mod_without_db_dir_yields_no_ids_and_no_warnings(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    commit_hash = install_commit(
        clean_repo, "ClientMod", files={"BepInEx/plugins/ClientMod.dll": "content"}
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result == ModIds()


def test_collect_mod_ids_raises_git_error_for_an_unknown_commit(
    clean_repo: Repo,
) -> None:
    with pytest.raises(GitError):
        collect_mod_ids(clean_repo, "0" * 40, detect_layout(clean_repo.path))


def test_collect_mod_ids_trader_folder_non_base_file_registers_only_the_folder(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    folder_name = "customtrader"
    files = {
        f"SPT/user/mods/ServerMod/db/traders/{folder_name}/questassort.json": (
            json.dumps({"started": {TRADER_ID: "quest-one"}})
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    # The trader folder name is registered, but questassort.json is not
    # base.json, so its content is never scanned for a trader _id.
    assert result.traders == {folder_name}


def test_collect_mod_ids_malformed_quest_entries_are_skipped_without_crashing(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/quests/quests.json": json.dumps(
            [
                {"_id": "quest-one"},
                "not-a-dict",
                {"no_id_field": True},
                {"_id": ""},
                {"_id": "quest-two"},
            ]
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.quests == {"quest-one", "quest-two"}
    assert result.warnings == []


# --------------------------------------------------------------------------
# find_residues
# --------------------------------------------------------------------------


def test_find_residues_reports_item_hits_from_inventory_items(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="item",
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
    content = _empty_pmc_profile()
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

    assert residues == [
        ProfileResidue(profile="player1", category="string", ids=[ITEM_A], count=1)
    ]


def test_find_residues_reports_trader_hits_from_traders_info_keys(
    tmp_path: Path,
) -> None:
    ids = ModIds(traders={TRADER_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["TradersInfo"] = {TRADER_ID: {"salesSum": 0}}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="trader",
            ids=[TRADER_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/TradersInfo",
        )
    ]


def test_find_residues_reports_quest_hits_from_quests_qid(tmp_path: Path) -> None:
    ids = ModIds(quests={"quest-one"})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Quests"] = [
        {"qid": "quest-one", "status": 1},
        {"qid": "quest-one", "status": 4},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="quest",
            ids=["quest-one"],
            count=2,
            character="pmc",
            path="/characters/pmc/Quests",
        )
    ]


def test_find_residues_string_fallback_finds_id_embedded_elsewhere(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["someOtherSection"] = {"lastEquippedTpl": ITEM_A}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(profile="player1", category="string", ids=[ITEM_A], count=1)
    ]


def test_find_residues_string_fallback_excludes_ids_already_matched_structurally(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
    string_only_id = "loose-mod-marker-id"
    ids = ModIds(
        items={ITEM_A}, traders={TRADER_ID}, quests={"quest-one", string_only_id}
    )
    content = _empty_pmc_profile()
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
    ids = ModIds(items={ITEM_B, ITEM_A})
    content = _empty_pmc_profile()
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
            category="item",
            ids=[ITEM_A, ITEM_B],
            count=3,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_returns_empty_list_for_a_clean_profile(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A}, traders={TRADER_ID}, quests={"quest-one"})
    profile = _make_profile(tmp_path, "player1", _empty_pmc_profile())

    assert find_residues(profile, ids) == []


def test_find_residues_returns_empty_list_for_malformed_profile_json(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "broken.json"
    profile.write_text("{not valid json", encoding="utf-8")

    assert find_residues(profile, ModIds(items={ITEM_A})) == []


def test_find_residues_returns_empty_list_for_a_missing_profile(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "missing.json"

    assert find_residues(profile, ModIds(items={ITEM_A})) == []


def test_find_residues_returns_empty_list_for_a_valid_json_non_object_root(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "array-root.json"
    profile.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert find_residues(profile, ModIds(items={ITEM_A})) == []


def test_find_residues_malformed_inventory_items_is_ignored_without_crashing(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = "not-a-list"
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


def test_find_residues_skips_non_dict_entries_in_inventory_items(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="item",
            ids=[ITEM_A],
            count=1,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_skips_non_dict_entries_in_quests_but_counts_the_rest(
    tmp_path: Path,
) -> None:
    ids = ModIds(quests={"quest-one"})
    content = _empty_pmc_profile()
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
            category="quest",
            ids=["quest-one"],
            count=1,
            character="pmc",
            path="/characters/pmc/Quests",
        )
    ]


# --------------------------------------------------------------------------
# find_residues -- category table (pmc, scav, top-level; make_profile fixture)
# --------------------------------------------------------------------------


def _delete_at(doc: JsonDict, path: tuple[str, ...]) -> None:
    node = doc
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]


def _corrupt_at(doc: JsonDict, path: tuple[str, ...]) -> None:
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
    category: str,
    path: str,
    ids: ModIds,
    expected_ids: list[str],
    expected_count: int,
) -> None:
    doc = make_profile(hits=frozenset({hit}))
    profile = _make_profile(tmp_path, "player1", doc)

    residues = find_residues(profile, ids)

    matching = [
        residue
        for residue in residues
        if residue.category == category and residue.character == character
    ]
    assert matching == [
        ProfileResidue(
            profile="player1",
            category=category,  # type: ignore[arg-type]
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
    doc = make_profile()
    corrupt(doc, path)
    profile = _make_profile(tmp_path, "player1", doc)

    assert find_residues(profile, ids) == []


def test_find_residues_reports_pmc_and_scav_item_hits_separately_with_distinct_ids(
    tmp_path: Path, make_profile: MakeProfile
) -> None:
    doc = make_profile(hits=frozenset({"pmc:item"}), item_id=ITEM_A)
    doc["characters"]["scav"]["Inventory"]["items"][1]["_tpl"] = ITEM_B
    profile = _make_profile(tmp_path, "player1", doc)
    ids = ModIds(items={ITEM_A, ITEM_B})

    residues = find_residues(profile, ids)

    item_residues = {
        residue.character: residue for residue in residues if residue.category == "item"
    }
    assert item_residues["pmc"].ids == [ITEM_A]
    assert item_residues["scav"].ids == [ITEM_B]


def test_find_residues_orders_character_major_then_category_then_string_last(
    tmp_path: Path, make_profile: MakeProfile
) -> None:
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
    assert residues[-1] == ProfileResidue(
        profile="player1", category="string", ids=[marker], count=1
    )


def test_find_residues_item_closure_counts_children_and_grandchildren_and_is_cycle_safe(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="item",
            ids=[ITEM_A],
            count=3,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_item_closure_skips_items_without_a_usable_id(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="item",
            ids=[ITEM_A],
            count=1,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_find_residues_string_fallback_excludes_matched_ids_but_keeps_unmatched(
    tmp_path: Path,
) -> None:
    content = _empty_pmc_profile()
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
    assert all(
        residue.category != "string" or ITEM_A not in residue.ids
        for residue in residues
    )


# --------------------------------------------------------------------------
# find_residues -- repeatable-quest match variants (traderId, _id, templateId,
# questStatus.qid) and non-dict entries
# --------------------------------------------------------------------------


def _pmc_with_repeatable(active: list[JsonDict]) -> JsonDict:
    content = _empty_pmc_profile()
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
def test_find_residues_repeatable_quest_matches_by_id_templateid_or_qid(
    tmp_path: Path, quest_entry: JsonDict
) -> None:
    ids = ModIds(quests={QUEST_ID})
    content = _pmc_with_repeatable([quest_entry])
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="repeatable-quest",
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/RepeatableQuests",
        )
    ]


def test_find_residues_repeatable_quest_matches_by_trader_id(tmp_path: Path) -> None:
    ids = ModIds(traders={TRADER_ID})
    content = _pmc_with_repeatable(
        [{"_id": "other", "templateId": "other", "traderId": TRADER_ID}]
    )
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="repeatable-quest",
            ids=[TRADER_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/RepeatableQuests",
        )
    ]


def test_find_residues_repeatable_quest_skips_non_dict_groups_and_entries(
    tmp_path: Path,
) -> None:
    ids = ModIds(quests={QUEST_ID})
    content = _empty_pmc_profile()
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
            category="repeatable-quest",
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/RepeatableQuests",
        )
    ]


def test_find_residues_repeatable_quest_no_hit_leaves_no_residue(
    tmp_path: Path,
) -> None:
    ids = ModIds(quests={QUEST_ID}, traders={TRADER_ID})
    content = _pmc_with_repeatable(
        [{"_id": "other", "templateId": "other", "traderId": "other-trader"}]
    )
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- task-counter and hideout-production: key vs. nested field
# --------------------------------------------------------------------------


def test_find_residues_task_counter_matches_by_key(tmp_path: Path) -> None:
    ids = ModIds(quests={QUEST_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["TaskConditionCounters"] = {
        QUEST_ID: {"id": QUEST_ID, "type": "Counter", "value": 1, "sourceId": "other"}
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="task-counter",
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/TaskConditionCounters",
        )
    ]


def test_find_residues_task_counter_matches_by_source_id(tmp_path: Path) -> None:
    ids = ModIds(quests={QUEST_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["TaskConditionCounters"] = {
        "cond1": {"id": "cond1", "type": "Counter", "value": 1, "sourceId": QUEST_ID}
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="task-counter",
            ids=[QUEST_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/TaskConditionCounters",
        )
    ]


def test_find_residues_task_counter_tolerates_a_non_dict_value_and_no_hit(
    tmp_path: Path,
) -> None:
    ids = ModIds(quests={QUEST_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["TaskConditionCounters"] = {"cond1": "not-a-dict"}
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


def test_find_residues_hideout_production_matches_by_key(tmp_path: Path) -> None:
    ids = ModIds(recipes={RECIPE_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {
        "Production": {RECIPE_ID: {"Progress": 0}}
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="hideout-production",
            ids=[RECIPE_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/Hideout/Production",
        )
    ]


def test_find_residues_hideout_production_matches_by_recipe_id(
    tmp_path: Path,
) -> None:
    ids = ModIds(recipes={RECIPE_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {
        "Production": {"prod1": {"recipeId": RECIPE_ID, "Progress": 0}}
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="hideout-production",
            ids=[RECIPE_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/Hideout/Production",
        )
    ]


def test_find_residues_hideout_production_tolerates_a_non_dict_value_and_no_hit(
    tmp_path: Path,
) -> None:
    ids = ModIds(recipes={RECIPE_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {"Production": {"prod1": "not-a-dict"}}
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- insured-item: trader id or item closure
# --------------------------------------------------------------------------


def test_find_residues_insured_item_matches_by_trader_id(tmp_path: Path) -> None:
    ids = ModIds(traders={TRADER_ID})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["InsuredItems"] = [{"tid": TRADER_ID, "itemId": "x"}]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="insured-item",
            ids=[TRADER_ID],
            count=1,
            character="pmc",
            path="/characters/pmc/InsuredItems",
        )
    ]


def test_find_residues_insured_item_matches_by_item_closure(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {"_id": "container", "_tpl": ITEM_A, "slotId": "main"},
        {"_id": "child", "_tpl": ITEM_B, "parentId": "container", "slotId": "main"},
    ]
    content["characters"]["pmc"]["InsuredItems"] = [
        {"tid": "unrelated-trader", "itemId": "child"}
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    insured = next(r for r in residues if r.category == "insured-item")
    assert insured.ids == ["child"]
    assert insured.count == 1


def test_find_residues_insured_item_skips_non_dict_entries_and_no_hit(
    tmp_path: Path,
) -> None:
    ids = ModIds(traders={TRADER_ID}, items={ITEM_A})
    content = _empty_pmc_profile()
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
    ids = ModIds(traders={TRADER_ID})
    content = _empty_pmc_profile()
    content["insurance"] = [{"traderId": TRADER_ID, "items": []}]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="insurance",
            ids=[TRADER_ID],
            count=1,
            character="",
            path="/insurance",
        )
    ]


def test_find_residues_insurance_matches_by_item_and_its_closure(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="insurance",
            ids=[ITEM_A],
            count=2,
            character="",
            path="/insurance",
        )
    ]


def test_find_residues_insurance_skips_non_dict_entries_and_no_hit(
    tmp_path: Path,
) -> None:
    ids = ModIds(traders={TRADER_ID}, items={ITEM_A})
    content = _empty_pmc_profile()
    content["insurance"] = ["not-a-dict", {"traderId": "other", "items": []}]
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# find_residues -- dialogue: trader key, message templateId prefix, message items
# --------------------------------------------------------------------------


def test_find_residues_dialogue_matches_by_trader_key(tmp_path: Path) -> None:
    ids = ModIds(traders={TRADER_ID})
    content = _empty_pmc_profile()
    content["dialogues"] = {TRADER_ID: {"_id": TRADER_ID, "messages": []}}
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="dialogue",
            ids=[TRADER_ID],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_matches_message_by_template_id_prefix(
    tmp_path: Path,
) -> None:
    ids = ModIds(quests={QUEST_ID})
    content = _empty_pmc_profile()
    content["dialogues"] = {
        "unrelated-trader": {
            "_id": "unrelated-trader",
            "messages": [
                {"_id": "m1", "templateId": f"{QUEST_ID}-suffix", "items": {}}
            ],
        }
    }
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="dialogue",
            ids=[QUEST_ID],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_matches_message_by_item_tpl(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="dialogue",
            ids=[ITEM_A],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_skips_non_dict_messages(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
    ids = ModIds(items={ITEM_A}, quests={QUEST_ID})
    content = _empty_pmc_profile()
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
            category="dialogue",
            ids=[ITEM_A],
            count=1,
            character="",
            path="/dialogues",
        )
    ]


def test_find_residues_dialogue_no_hit_leaves_no_residue(tmp_path: Path) -> None:
    ids = ModIds(traders={TRADER_ID}, items={ITEM_A}, quests={QUEST_ID})
    content = _empty_pmc_profile()
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


@pytest.mark.parametrize(
    "build_key", ["weaponBuilds", "equipmentBuilds", "magazineBuilds"]
)
def test_find_residues_userbuild_matches_all_three_build_lists(
    tmp_path: Path, build_key: str
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["userbuilds"] = {
        "weaponBuilds": [],
        "equipmentBuilds": [],
        "magazineBuilds": [],
    }
    content["userbuilds"][build_key] = [
        {"Id": "b1", "Name": "Build", "Items": [{"_id": "i1", "_tpl": ITEM_A}]}
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1",
            category="userbuild",
            ids=[ITEM_A],
            count=1,
            character="",
            path="/userbuilds",
        )
    ]


def test_find_residues_userbuild_no_hit_leaves_no_residue(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["userbuilds"] = {
        "weaponBuilds": [
            {"Id": "b1", "Name": "Build", "Items": [{"_id": "i1", "_tpl": ITEM_B}]}
        ],
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
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
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
            category="hideout-area",
            ids=[ITEM_A],
            count=1,
            character="pmc",
            path="/characters/pmc/Hideout/Areas",
        )
    ]


def test_find_residues_hideout_area_no_hit_leaves_no_residue(
    tmp_path: Path,
) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Hideout"] = {
        "Areas": [{"type": 0, "slots": [{"locationIndex": 0}]}]
    }
    profile = _make_profile(tmp_path, "player1", content)

    assert find_residues(profile, ids) == []


# --------------------------------------------------------------------------
# collect_mod_ids -- recipes and achievements
# --------------------------------------------------------------------------


def test_collect_mod_ids_recipes_from_recipe_list_keys_below_hideout(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/hideout/production.json": json.dumps(
            {
                "recipes": [{"_id": RECIPE_ID}],
                "scavRecipes": [{"_id": "5c0d1b8b86f7744bb2683c02"}],
                "cultistRecipes": [{"_id": "5c0d1b8b86f7744bb2683c03"}],
            }
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.recipes == {
        RECIPE_ID,
        "5c0d1b8b86f7744bb2683c02",
        "5c0d1b8b86f7744bb2683c03",
    }
    # The recipe list keys are not 24-hex object keys, so nothing leaks into items.
    assert result.items == set()


def test_collect_mod_ids_recipes_from_a_plain_list_below_hideout(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/hideout/areas.json": json.dumps(
            [{"_id": RECIPE_ID, "type": 5}]
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.recipes == {RECIPE_ID}


def test_collect_mod_ids_achievements_from_achievements_json_uses_id_field(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/templates/achievements.json": json.dumps(
            [{"id": ACHIEVEMENT_ID, "imageUrl": "x.png"}]
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.achievements == {ACHIEVEMENT_ID}
    assert result.items == set()


def test_collect_mod_ids_achievements_from_an_achievement_path_segment(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/achievement/custom.json": json.dumps(
            [{"id": ACHIEVEMENT_ID}]
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.achievements == {ACHIEVEMENT_ID}


# --------------------------------------------------------------------------
# profile_paths
# --------------------------------------------------------------------------


def test_profile_paths_returns_json_files_sorted_and_skips_backups(
    tmp_path: Path,
) -> None:
    profiles_dir = tmp_path / "SPT" / "user" / "profiles"
    (profiles_dir / "backups").mkdir(parents=True)
    (profiles_dir / "b.json").write_text("{}", encoding="utf-8")
    (profiles_dir / "a.json").write_text("{}", encoding="utf-8")
    (profiles_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (profiles_dir / "backups" / "old.json").write_text("{}", encoding="utf-8")

    result = profile_paths(GameLayout(root=tmp_path, runtime_dir_name="SPT"))

    assert result == [profiles_dir / "a.json", profiles_dir / "b.json"]


def test_profile_paths_returns_empty_list_when_profiles_dir_is_missing(
    tmp_path: Path,
) -> None:
    assert profile_paths(GameLayout(root=tmp_path, runtime_dir_name="SPT")) == []


# --------------------------------------------------------------------------
# check_all
# --------------------------------------------------------------------------


def test_check_all_keys_residues_by_mod_name_across_mods_and_profiles(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    item_x = "5c0d1b8b86f7744bb2683cf1"
    item_y = "5c0d1b8b86f7744bb2683cf2"
    install_commit(
        clean_repo,
        "ModX",
        mod_type="server",
        minute=1,
        files={
            "SPT/user/mods/ModX/db/templates/items.json": json.dumps(
                {item_x: {"_id": item_x, "_name": "item_x"}}
            )
        },
    )
    install_commit(
        clean_repo,
        "ModY",
        mod_type="server",
        minute=2,
        files={
            "SPT/user/mods/ModY/db/templates/items.json": json.dumps(
                {item_y: {"_id": item_y, "_name": "item_y"}}
            )
        },
    )

    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile_with_x = _empty_pmc_profile()
    profile_with_x["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "i1",
            "_tpl": item_x,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    (profiles_dir / "abc.json").write_text(
        json.dumps(profile_with_x), encoding="utf-8"
    )
    (profiles_dir / "def.json").write_text(
        json.dumps(_empty_pmc_profile()), encoding="utf-8"
    )

    result = check_all(clean_repo, detect_layout(clean_repo.path))

    assert set(result.residues.keys()) == {"ModX"}
    assert result.residues["ModX"] == [
        ProfileResidue(
            profile="abc",
            category="item",
            ids=[item_x],
            count=1,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_check_all_skips_removed_mods(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    item_x = "5c0d1b8b86f7744bb2683cf1"
    commit_hash = install_commit(
        clean_repo,
        "ModZ",
        mod_type="server",
        files={
            "SPT/user/mods/ModZ/db/templates/items.json": json.dumps(
                {item_x: {"_id": item_x, "_name": "item_x"}}
            )
        },
    )
    clean_repo.revert(
        commit_hash, extra_trailers={"Mod": "ModZ", "Mod-State": "removed"}
    )

    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile_with_x = _empty_pmc_profile()
    profile_with_x["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "i1",
            "_tpl": item_x,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    (profiles_dir / "abc.json").write_text(
        json.dumps(profile_with_x), encoding="utf-8"
    )

    result = check_all(clean_repo, detect_layout(clean_repo.path))

    assert result.residues == {}


def test_check_all_prefixes_collection_warnings_with_the_mod_name(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    install_commit(
        clean_repo,
        "BrokenMod",
        mod_type="server",
        files={
            "SPT/user/mods/BrokenMod/db/templates/items.json": "{not valid json"
        },
    )

    result = check_all(clean_repo, detect_layout(clean_repo.path))

    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("BrokenMod")


def test_check_all_does_not_modify_the_repository_or_profiles(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    item_x = "5c0d1b8b86f7744bb2683cf1"
    install_commit(
        clean_repo,
        "ModX",
        mod_type="server",
        files={
            "SPT/user/mods/ModX/db/templates/items.json": json.dumps(
                {item_x: {"_id": item_x, "_name": "item_x"}}
            )
        },
    )
    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile_with_x = _empty_pmc_profile()
    profile_with_x["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "i1",
            "_tpl": item_x,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    (profiles_dir / "abc.json").write_text(
        json.dumps(profile_with_x), encoding="utf-8"
    )
    before = (profiles_dir / "abc.json").read_bytes()

    check_all(clean_repo, detect_layout(clean_repo.path))

    assert (profiles_dir / "abc.json").read_bytes() == before


def test_collect_mod_ids_accepts_db_files_saved_with_a_utf8_bom(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/templates/items.json": (
            "﻿" + json.dumps({ITEM_A: {"_id": ITEM_A, "_name": "item_a"}})
        ).encode("utf-8"),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.items == {ITEM_A}
    assert result.warnings == []
