"""Unit tests for the profile entry points that read from the game repository.

Covers ``collect_mod_ids`` (reading db content from an install commit, never
the working tree, tolerating broken JSON and UTF-8 BOMs, and picking up item,
trader, quest, recipe and achievement IDs), ``profile_paths`` (sorted profile
JSON files with backups skipped) and ``check_all`` (residues keyed by mod
name across mods and profiles, without modifying anything).

CONTRACT CHANGE (spt-mod-manager-fyx): ``collect_mod_ids``, ``profile_paths``
and ``check_all`` now take a ``GameLayout`` instead of a bare game-dir
``Path``, so the whitelisted db/profile paths are relative to the detected
runtime directory. Updated accordingly; the ``SPT_Runtime`` counterpart of
this layout-awareness is covered in tests/test_game_layout.py.
"""

import json
import shutil
from pathlib import Path

import pytest

from fixture_types import InstallCommit
from fixtures_profile import empty_pmc_profile
from sptmod.core.errors import GitError
from sptmod.core.game_dir import GameLayout, detect_layout
from sptmod.core.models import ProfileResidue, ResidueCategory
from sptmod.core.profile import ModIds, check_all, collect_mod_ids, profile_paths
from sptmod.core.repo import Repo

ITEM_A = "5c0d1b8b86f7744bb2683cfa"
ITEM_B = "5c0d1b8b86f7744bb2683cfb"
ITEM_C = "5c0d1b8b86f7744bb2683cfc"
TRADER_ID = "5ac3b934136fb568fa78b1e2"
RECIPE_ID = "5c0d1b8b86f7744bb2683cfd"
ACHIEVEMENT_ID = "achievement-mod"


# --------------------------------------------------------------------------
# collect_mod_ids
# --------------------------------------------------------------------------


def test_collect_mod_ids_reads_item_trader_and_quest_ids_from_the_commit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Extract item, trader, and quest IDs from db files in the commit tree."""
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
        "SPT/user/mods/ServerMod/db/quests/quests.json": json.dumps([{"_id": "quest-one"}, {"_id": "quest-two"}]),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    # Mutate/delete the working tree after the commit: collect_mod_ids must
    # read from the commit only, never the working tree.
    (clean_repo.path / "SPT/user/mods/ServerMod/db/templates/items.json").write_text("{}", encoding="utf-8")
    shutil.rmtree(clean_repo.path / "SPT/user/mods/ServerMod/db/traders")
    (clean_repo.path / "SPT/user/mods/ServerMod/db/quests/quests.json").unlink()

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.items == {ITEM_A, ITEM_B}
    assert result.traders == {TRADER_ID}
    assert result.quests == {"quest-one", "quest-two"}
    assert not result.warnings


def test_collect_mod_ids_trader_id_includes_both_folder_name_and_base_json_id(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Trader ID set includes both folder name and _id from base.json."""
    folder_name = "customtrader"
    files = {
        f"SPT/user/mods/ServerMod/db/traders/{folder_name}/base.json": json.dumps(
            {"_id": TRADER_ID, "nickname": "Custom Trader"}
        ),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.traders == {folder_name, TRADER_ID}


def test_collect_mod_ids_finds_item_ids_below_an_items_path_segment(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Item IDs can be in db/templates/items/ subdirectories, not just items.json."""
    files = {
        "SPT/user/mods/ServerMod/db/templates/items/custom.json": json.dumps({"_id": ITEM_C, "_name": "custom_item"}),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.items == {ITEM_C}


def test_collect_mod_ids_broken_json_warns_and_keeps_scanning_other_files(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Malformed JSON logs a warning but scanning continues for other files."""
    files = {
        "SPT/user/mods/ServerMod/db/templates/items.json": "{not valid json",
        f"SPT/user/mods/ServerMod/db/traders/{TRADER_ID}/base.json": json.dumps(
            {"_id": TRADER_ID, "nickname": "Test Trader"}
        ),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.traders == {TRADER_ID}
    assert result.items == set()
    assert len(result.warnings) == 1
    assert "items.json" in result.warnings[0]


def test_collect_mod_ids_ignores_files_under_the_mod_dir_but_not_below_db(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """JSON files outside db/ (e.g., assets/) are ignored."""
    files = {
        "SPT/user/mods/ServerMod/assets/icon.json": json.dumps({"color": "red"}),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result == ModIds()


def test_collect_mod_ids_mod_without_db_dir_yields_no_ids_and_no_warnings(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """collect_mod_ids returns empty results for mods without a db/ directory."""
    commit_hash = install_commit(clean_repo, "ClientMod", files={"BepInEx/plugins/ClientMod.dll": "content"})

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result == ModIds()


def test_collect_mod_ids_raises_git_error_for_an_unknown_commit(
    clean_repo: Repo,
) -> None:
    """collect_mod_ids raises GitError when passed a non-existent commit hash."""
    with pytest.raises(GitError):
        collect_mod_ids(clean_repo, "0" * 40, detect_layout(clean_repo.path))


def test_collect_mod_ids_trader_folder_non_base_file_registers_only_the_folder(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Trader folder name is captured, but non-base.json files aren't scanned."""
    folder_name = "customtrader"
    files = {
        f"SPT/user/mods/ServerMod/db/traders/{folder_name}/questassort.json": (
            json.dumps({"started": {TRADER_ID: "quest-one"}})
        ),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    # The trader folder name is registered, but questassort.json is not
    # base.json, so its content is never scanned for a trader _id.
    assert result.traders == {folder_name}


def test_collect_mod_ids_malformed_quest_entries_are_skipped_without_crashing(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Malformed quest entries (non-dict, missing _id, empty _id) are ignored."""
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
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.quests == {"quest-one", "quest-two"}
    assert not result.warnings


# --------------------------------------------------------------------------
# collect_mod_ids -- recipes and achievements
# --------------------------------------------------------------------------


def test_collect_mod_ids_recipes_from_recipe_list_keys_below_hideout(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Recipe IDs extracted from recipes, scavRecipes, cultistRecipes lists."""
    files = {
        "SPT/user/mods/ServerMod/db/hideout/production.json": json.dumps(
            {
                "recipes": [{"_id": RECIPE_ID}],
                "scavRecipes": [{"_id": "5c0d1b8b86f7744bb2683c02"}],
                "cultistRecipes": [{"_id": "5c0d1b8b86f7744bb2683c03"}],
            }
        ),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

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
    """Recipe IDs extracted from list entries below hideout/ with type=5."""
    files = {
        "SPT/user/mods/ServerMod/db/hideout/areas.json": json.dumps([{"_id": RECIPE_ID, "type": 5}]),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.recipes == {RECIPE_ID}


def test_collect_mod_ids_achievements_from_achievements_json_uses_id_field(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Achievement IDs use the 'id' field (not '_id') from achievements.json."""
    files = {
        "SPT/user/mods/ServerMod/db/templates/achievements.json": json.dumps(
            [{"id": ACHIEVEMENT_ID, "imageUrl": "x.png"}]
        ),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.achievements == {ACHIEVEMENT_ID}
    assert result.items == set()


def test_collect_mod_ids_achievements_from_an_achievement_path_segment(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Achievement IDs extracted from files below achievement/ or achievements/ path."""
    files = {
        "SPT/user/mods/ServerMod/db/achievement/custom.json": json.dumps([{"id": ACHIEVEMENT_ID}]),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.achievements == {ACHIEVEMENT_ID}


# --------------------------------------------------------------------------
# profile_paths
# --------------------------------------------------------------------------


def test_profile_paths_returns_json_files_sorted_and_skips_backups(
    tmp_path: Path,
) -> None:
    """profile_paths lists profile JSON files in sorted order and excludes backups/."""
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
    """profile_paths returns empty list when the profiles directory does not exist."""
    assert profile_paths(GameLayout(root=tmp_path, runtime_dir_name="SPT")) == []


# --------------------------------------------------------------------------
# check_all
# --------------------------------------------------------------------------


def test_check_all_keys_residues_by_mod_name_across_mods_and_profiles(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """check_all aggregates residues by mod name across all profiles."""
    item_x = "5c0d1b8b86f7744bb2683cf1"
    item_y = "5c0d1b8b86f7744bb2683cf2"
    install_commit(
        clean_repo,
        "ModX",
        mod_type="server",
        minute=1,
        files={"SPT/user/mods/ModX/db/templates/items.json": json.dumps({item_x: {"_id": item_x, "_name": "item_x"}})},
    )
    install_commit(
        clean_repo,
        "ModY",
        mod_type="server",
        minute=2,
        files={"SPT/user/mods/ModY/db/templates/items.json": json.dumps({item_y: {"_id": item_y, "_name": "item_y"}})},
    )

    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile_with_x = empty_pmc_profile()
    profile_with_x["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "i1",
            "_tpl": item_x,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    (profiles_dir / "abc.json").write_text(json.dumps(profile_with_x), encoding="utf-8")
    (profiles_dir / "def.json").write_text(json.dumps(empty_pmc_profile()), encoding="utf-8")

    result = check_all(clean_repo, detect_layout(clean_repo.path))

    assert set(result.residues.keys()) == {"ModX"}
    assert result.residues["ModX"] == [
        ProfileResidue(
            profile="abc",
            category=ResidueCategory.ITEM,
            ids=[item_x],
            count=1,
            character="pmc",
            path="/characters/pmc/Inventory/items",
        )
    ]


def test_check_all_skips_removed_mods(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """check_all excludes mods with Mod-State: removed from residue analysis."""
    item_x = "5c0d1b8b86f7744bb2683cf1"
    commit_hash = install_commit(
        clean_repo,
        "ModZ",
        mod_type="server",
        files={"SPT/user/mods/ModZ/db/templates/items.json": json.dumps({item_x: {"_id": item_x, "_name": "item_x"}})},
    )
    clean_repo.revert(commit_hash, extra_trailers={"Mod": "ModZ", "Mod-State": "removed"})

    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile_with_x = empty_pmc_profile()
    profile_with_x["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "i1",
            "_tpl": item_x,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    (profiles_dir / "abc.json").write_text(json.dumps(profile_with_x), encoding="utf-8")

    result = check_all(clean_repo, detect_layout(clean_repo.path))

    assert not result.residues


def test_check_all_prefixes_collection_warnings_with_the_mod_name(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """check_all prefixes collect_mod_ids warnings with the mod's name."""
    install_commit(
        clean_repo,
        "BrokenMod",
        mod_type="server",
        files={"SPT/user/mods/BrokenMod/db/templates/items.json": "{not valid json"},
    )

    result = check_all(clean_repo, detect_layout(clean_repo.path))

    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("BrokenMod")


def test_check_all_does_not_modify_the_repository_or_profiles(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """check_all is read-only; it does not modify repo or profile files."""
    item_x = "5c0d1b8b86f7744bb2683cf1"
    install_commit(
        clean_repo,
        "ModX",
        mod_type="server",
        files={"SPT/user/mods/ModX/db/templates/items.json": json.dumps({item_x: {"_id": item_x, "_name": "item_x"}})},
    )
    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile_with_x = empty_pmc_profile()
    profile_with_x["characters"]["pmc"]["Inventory"]["items"] = [
        {
            "_id": "i1",
            "_tpl": item_x,
            "parentId": "5c0d1b8b86f7744bb2683c99",
            "slotId": "hideout",
        }
    ]
    (profiles_dir / "abc.json").write_text(json.dumps(profile_with_x), encoding="utf-8")
    before = (profiles_dir / "abc.json").read_bytes()

    check_all(clean_repo, detect_layout(clean_repo.path))

    assert (profiles_dir / "abc.json").read_bytes() == before


def test_collect_mod_ids_accepts_db_files_saved_with_a_utf8_bom(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Collect IDs from UTF-8 BOM-marked JSON files."""
    files = {
        "SPT/user/mods/ServerMod/db/templates/items.json": (
            "﻿" + json.dumps({ITEM_A: {"_id": ITEM_A, "_name": "item_a"}})
        ).encode("utf-8"),
    }
    commit_hash = install_commit(clean_repo, "ServerMod", mod_type="server", files=files)

    result = collect_mod_ids(clean_repo, commit_hash, detect_layout(clean_repo.path))

    assert result.items == {ITEM_A}
    assert not result.warnings
