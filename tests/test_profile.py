"""Unit tests for ``sptmod.core.profile``.

Covers ``collect_mod_ids`` (reading db content from an install commit, never
the working tree, tolerating broken JSON), ``find_residues`` (matching a
mod's IDs against a player profile, per-category counts, the string
fallback and its exclusion of structurally-matched IDs), ``profile_paths``
and ``check_all``.
"""

import json
import shutil
from pathlib import Path

import pytest

from sptmod.core.errors import GitError
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


def _make_profile(tmp_path: Path, name: str, content: dict) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _empty_pmc_profile() -> dict:
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
    clean_repo: Repo, install_commit
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

    result = collect_mod_ids(clean_repo, commit_hash)

    assert result.items == {ITEM_A, ITEM_B}
    assert result.traders == {TRADER_ID}
    assert result.quests == {"quest-one", "quest-two"}
    assert result.warnings == []


def test_collect_mod_ids_trader_id_includes_both_folder_name_and_base_json_id(
    clean_repo: Repo, install_commit
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

    result = collect_mod_ids(clean_repo, commit_hash)

    assert result.traders == {folder_name, TRADER_ID}


def test_collect_mod_ids_finds_item_ids_below_an_items_path_segment(
    clean_repo: Repo, install_commit
) -> None:
    files = {
        "SPT/user/mods/ServerMod/db/templates/items/custom.json": json.dumps(
            {"_id": ITEM_C, "_name": "custom_item"}
        ),
    }
    commit_hash = install_commit(
        clean_repo, "ServerMod", mod_type="server", files=files
    )

    result = collect_mod_ids(clean_repo, commit_hash)

    assert result.items == {ITEM_C}


def test_collect_mod_ids_broken_json_warns_and_keeps_scanning_other_files(
    clean_repo: Repo, install_commit
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

    result = collect_mod_ids(clean_repo, commit_hash)

    assert result.traders == {TRADER_ID}
    assert result.items == set()
    assert len(result.warnings) == 1
    assert "items.json" in result.warnings[0]


def test_collect_mod_ids_mod_without_db_dir_yields_no_ids_and_no_warnings(
    clean_repo: Repo, install_commit
) -> None:
    commit_hash = install_commit(
        clean_repo, "ClientMod", files={"BepInEx/plugins/ClientMod.dll": "content"}
    )

    result = collect_mod_ids(clean_repo, commit_hash)

    assert result == ModIds()


def test_collect_mod_ids_raises_git_error_for_an_unknown_commit(
    clean_repo: Repo,
) -> None:
    with pytest.raises(GitError):
        collect_mod_ids(clean_repo, "0" * 40)


# --------------------------------------------------------------------------
# find_residues
# --------------------------------------------------------------------------


def test_find_residues_reports_item_hits_from_inventory_items(tmp_path: Path) -> None:
    ids = ModIds(items={ITEM_A})
    content = _empty_pmc_profile()
    content["characters"]["pmc"]["Inventory"]["items"] = [
        {"_id": "x1", "tpl": ITEM_A},
        {"_id": "x2", "tpl": ITEM_A},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(profile="player1", category="item", ids=[ITEM_A], count=2)
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
        ProfileResidue(profile="player1", category="trader", ids=[TRADER_ID], count=1)
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
            profile="player1", category="quest", ids=["quest-one"], count=2
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
        {"_id": "x1", "tpl": ITEM_A}
    ]
    content["backup"] = {"tpl": ITEM_A}
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
        {"_id": "x1", "tpl": ITEM_A}
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
        {"_id": "x1", "tpl": ITEM_B},
        {"_id": "x2", "tpl": ITEM_A},
        {"_id": "x3", "tpl": ITEM_A},
    ]
    profile = _make_profile(tmp_path, "player1", content)

    residues = find_residues(profile, ids)

    assert residues == [
        ProfileResidue(
            profile="player1", category="item", ids=[ITEM_A, ITEM_B], count=3
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

    result = profile_paths(tmp_path)

    assert result == [profiles_dir / "a.json", profiles_dir / "b.json"]


def test_profile_paths_returns_empty_list_when_profiles_dir_is_missing(
    tmp_path: Path,
) -> None:
    assert profile_paths(tmp_path) == []


# --------------------------------------------------------------------------
# check_all
# --------------------------------------------------------------------------


def test_check_all_keys_residues_by_mod_name_across_mods_and_profiles(
    clean_repo: Repo, install_commit
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
        {"_id": "i1", "tpl": item_x}
    ]
    (profiles_dir / "abc.json").write_text(
        json.dumps(profile_with_x), encoding="utf-8"
    )
    (profiles_dir / "def.json").write_text(
        json.dumps(_empty_pmc_profile()), encoding="utf-8"
    )

    result = check_all(clean_repo, clean_repo.path)

    assert set(result.residues.keys()) == {"ModX"}
    assert result.residues["ModX"] == [
        ProfileResidue(profile="abc", category="item", ids=[item_x], count=1)
    ]


def test_check_all_skips_removed_mods(clean_repo: Repo, install_commit) -> None:
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
        {"_id": "i1", "tpl": item_x}
    ]
    (profiles_dir / "abc.json").write_text(
        json.dumps(profile_with_x), encoding="utf-8"
    )

    result = check_all(clean_repo, clean_repo.path)

    assert result.residues == {}


def test_check_all_prefixes_collection_warnings_with_the_mod_name(
    clean_repo: Repo, install_commit
) -> None:
    install_commit(
        clean_repo,
        "BrokenMod",
        mod_type="server",
        files={
            "SPT/user/mods/BrokenMod/db/templates/items.json": "{not valid json"
        },
    )

    result = check_all(clean_repo, clean_repo.path)

    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("BrokenMod")


def test_check_all_does_not_modify_the_repository_or_profiles(
    clean_repo: Repo, install_commit
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
        {"_id": "i1", "tpl": item_x}
    ]
    (profiles_dir / "abc.json").write_text(
        json.dumps(profile_with_x), encoding="utf-8"
    )
    before = (profiles_dir / "abc.json").read_bytes()

    check_all(clean_repo, clean_repo.path)

    assert (profiles_dir / "abc.json").read_bytes() == before
