"""Tests for ``sptmod.core.services.plan_profile_cleanup`` and ``clean_profile``,
and for ``remove_mod(clean_profiles=True)``.

Covers the plan/apply dry-run guarantee, structural removal of every residue
category on both characters and the shared top-level sections (including the
``parentId`` item closure, dangling ``favoriteItems``/``fastPanel``
references, and the hideout-area slot reset), the backup-and-snapshot safety
net, byte-level formatting of an untouched profile, profile selection and
error handling, and the ordering/skip-guard semantics of
``remove_mod(clean_profiles=True)``.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fixture_types import InstallCommit, JsonDict
from sptmod.core import game_dir, services
from sptmod.core.errors import (
    AmbiguousModError,
    GameRunningError,
    InvalidPathError,
    ModNotFoundError,
    SptmodError,
)
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

# --------------------------------------------------------------------------
# Fixed content IDs shared by every test in this module. Item and trader IDs
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


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the game process check to prevent GameRunningError during profile cleanup tests."""
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _install_mod(repo: Repo, install_commit: InstallCommit, name: str = MOD_NAME, minute: int = 1) -> str:
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


def _write_profile(repo: Repo, stem: str, data: JsonDict) -> Path:
    """Write a profile document and commit it, keeping the tree clean."""
    relpath = Path(f"SPT/user/profiles/{stem}.json")
    profile_path = repo.path / relpath
    profile_path.write_text(json.dumps(data), encoding="utf-8")
    repo.commit("chore: profile snapshot", paths=[relpath])
    return profile_path


def _character(name: str, *, achievements: bool, repeatable: bool) -> JsonDict:
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


def _full_profile() -> JsonDict:
    """A profile document with mixed mod/unrelated content in every container
    the cleanup knows, on both characters and the shared top-level sections.
    """
    return {
        "info": {"nickname": "tester", "note": "Тест"},
        "characters": {
            "pmc": _character("pmc", achievements=True, repeatable=True),
            "scav": _character("scav", achievements=False, repeatable=False),
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


# --------------------------------------------------------------------------
# Plan is a dry run.
# --------------------------------------------------------------------------


def test_plan_profile_cleanup_does_not_modify_the_profile_repo_or_history(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup is a dry run: it reports removals but changes nothing.

    After installing a mod with full content IDs and writing a profile with
    residues, calling plan_profile_cleanup returns a non-empty plan but the
    profile bytes, commit history, and backup files are all unchanged.
    """
    _install_mod(clean_repo, install_commit)
    profile_path = _write_profile(clean_repo, "abc", _full_profile())
    before_bytes = profile_path.read_bytes()
    before_head = clean_repo.log()[0].hash
    layout = detect_layout(clean_repo.path)
    backups_dir = layout.root / layout.profile_backups_dir
    # fake_game_dir seeds a dummy backups/old.json; only new entries count.
    before_backup_entries = set(backups_dir.iterdir())

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)

    assert plan.removals != []
    assert profile_path.read_bytes() == before_bytes
    assert clean_repo.log()[0].hash == before_head
    assert clean_repo.is_clean() is True
    assert set(backups_dir.iterdir()) == before_backup_entries


def test_plan_and_apply_report_identical_removals_for_the_same_input(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup and clean_profile report identical removals and profiles.

    Both functions report the same list of ProfileResidue entries to be
    removed and the same profile IDs that were touched.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)
    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert plan.removals == result.removed
    assert plan.profiles == result.profiles


def test_plan_profile_cleanup_works_for_an_already_removed_mod(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup finds residues even after the mod's install commit is reverted.

    After installing a mod and later removing it with remove_mod(force=True),
    plan_profile_cleanup can still find that mod's residues in profiles,
    reporting plan.removals != [] and plan.mod == MOD_NAME.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())
    services.remove_mod(clean_repo.path, MOD_NAME, force=True)

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)

    assert plan.removals != []
    assert plan.mod == MOD_NAME


def test_plan_profile_cleanup_skips_an_unreadable_profile_and_warns(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup reports unreadable profiles in warnings and continues.

    A profile with invalid JSON ('{not valid json') is included in
    plan.warnings with its filename but skipped from plan.profiles, and
    planning proceeds without raising.
    """
    _install_mod(clean_repo, install_commit)
    broken_relpath = Path("SPT/user/profiles/broken.json")
    (clean_repo.path / broken_relpath).write_text("{not valid json", encoding="utf-8")
    clean_repo.commit("chore: broken profile", paths=[broken_relpath])

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)

    assert "broken.json" in " ".join(plan.warnings)
    assert "broken" not in plan.profiles


def test_plan_profile_cleanup_only_lists_profiles_that_have_a_hit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup reports only profiles that contain mod residues.

    When two profiles exist—one with the mod's item and one with an empty
    inventory—only the first is included in plan.profiles.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "hit", _full_profile())
    _write_profile(
        clean_repo,
        "untouched",
        {"characters": {"pmc": {"Inventory": {"items": []}}}},
    )

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)

    assert plan.profiles == ["hit"]


def test_plan_profile_cleanup_raises_mod_not_found_for_unknown_name(
    clean_repo: Repo,
) -> None:
    """plan_profile_cleanup raises ModNotFoundError when the mod name does not exist.

    Calling plan_profile_cleanup(clean_repo.path, "Ghost") raises
    ModNotFoundError when no mod by that name has been installed.
    """
    with pytest.raises(ModNotFoundError):
        services.plan_profile_cleanup(clean_repo.path, "Ghost")


def test_plan_profile_cleanup_raises_ambiguous_mod_error_for_case_variants(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup raises AmbiguousModError when multiple case variants exist.

    After installing both "CoolMod" and "coolmod", calling
    plan_profile_cleanup(path, "COOLMOD") raises AmbiguousModError because
    the name matches both case-insensitively.
    """
    _install_mod(clean_repo, install_commit, name="CoolMod", minute=1)
    install_commit(clean_repo, "coolmod", minute=2)

    with pytest.raises(AmbiguousModError):
        services.plan_profile_cleanup(clean_repo.path, "COOLMOD")


# --------------------------------------------------------------------------
# Apply: structural removal of every category, both characters, top level.
# --------------------------------------------------------------------------


def test_clean_profile_removes_every_category_hit_and_keeps_unrelated_ids(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile removes all 16 residue categories while preserving unrelated content.

    After installing a mod and writing a comprehensive profile with mod IDs
    in every category (item, encyclopedia, wishlist, trader, etc.) on both
    pmc and scav characters plus top-level sections, clean_profile removes
    all mod references but keeps unrelated IDs and the profile structure intact.
    """
    _install_mod(clean_repo, install_commit)
    original = _full_profile()
    profile_path = _write_profile(clean_repo, "abc", original)
    before_bytes = profile_path.read_bytes()

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert result.profiles == ["abc"]
    assert result.untouched == []
    cleaned = json.loads(profile_path.read_text(encoding="utf-8"))

    for name in ("pmc", "scav"):
        char = cleaned["characters"][name]
        original_char = original["characters"][name]

        # item: the container and its whole parentId closure (child,
        # grandchild) are gone; the stash root and the unrelated sibling stay.
        remaining_ids = {item["_id"] for item in char["Inventory"]["items"]}
        assert remaining_ids == {f"{name}-root", f"{name}-unrelated"}

        # dangling favoriteItems/fastPanel references to the removed item.
        assert char["Inventory"]["favoriteItems"] == [f"{name}-unrelated"]
        assert char["Inventory"]["fastPanel"] == {"1": f"{name}-unrelated"}

        # single-id-keyed containers: mod's key gone, unrelated key stays.
        assert char["Encyclopedia"] == {OTHER_ITEM_ID: True}
        assert char["WishList"] == {OTHER_ITEM_ID: 2}
        assert char["TradersInfo"] == {OTHER_TRADER_ID: {"salesSum": 0}}
        assert [q["qid"] for q in char["Quests"]] == [OTHER_QUEST_ID]
        assert list(char["TaskConditionCounters"]) == [f"{name}-cond-other"]
        assert list(char["Hideout"]["Production"]) == [f"{name}-prod-other"]
        assert char["UnlockedInfo"]["unlockedProductionRecipe"] == [OTHER_RECIPE_ID]

        # insured-item: matched by trader and by item closure; unrelated stays.
        assert char["InsuredItems"] == [{"tid": OTHER_TRADER_ID, "itemId": "totally-unrelated"}]

        # hideout-area: the mod's item is gone from both slots holding it,
        # one keeping its locationIndex, the other (no int index) emptied
        # entirely; the untouched slots are unchanged.
        slots = char["Hideout"]["Areas"][0]["slots"]
        assert slots[0] == {"locationIndex": 0}
        assert slots[1] == {"locationIndex": 1}
        assert slots[2] == original_char["Hideout"]["Areas"][0]["slots"][2]
        assert slots[3] == {}

    # achievement / repeatable-quest: pmc only.
    assert cleaned["characters"]["pmc"]["Achievements"] == {OTHER_ACHIEVEMENT_ID: 1700000001}
    assert "Achievements" not in cleaned["characters"]["scav"]
    pmc_repeatable = cleaned["characters"]["pmc"]["RepeatableQuests"][0]
    assert [q["_id"] for q in pmc_repeatable["activeQuests"]] == ["rq-other"]
    assert [q["_id"] for q in pmc_repeatable["inactiveQuests"]] == ["rq-i2"]

    # trader-purchase
    assert cleaned["traderPurchases"] == {OTHER_TRADER_ID: {"a": {"count": 1}}}

    # insurance: the mod-trader entry is dropped whole; the other entry's
    # matched item and its parentId child are dropped, the unrelated item stays.
    assert len(cleaned["insurance"]) == 1
    assert cleaned["insurance"][0]["traderId"] == OTHER_TRADER_ID
    assert [item["_id"] for item in cleaned["insurance"][0]["items"]] == ["i3"]

    # dialogue: the mod-trader dialogue is dropped whole; the other trader's
    # dialogue keeps only the message that names neither the quest nor an item.
    assert MOD_TRADER_ID not in cleaned["dialogues"]
    other_dialogue = cleaned["dialogues"][OTHER_TRADER_ID]
    assert [m["_id"] for m in other_dialogue["messages"]] == ["m3"]

    # userbuild: the build using the mod's item is dropped from every list;
    # the unrelated build in each list stays.
    for key in ("weaponBuilds", "equipmentBuilds", "magazineBuilds"):
        assert [b["Id"] for b in cleaned["userbuilds"][key]][0].endswith("2")

    # non-ascii text elsewhere in the document survives literally.
    assert cleaned["info"]["note"] == "Тест"

    categories = {residue.category for residue in result.removed}
    assert categories == {
        "item",
        "encyclopedia",
        "wishlist",
        "trader",
        "trader-purchase",
        "quest",
        "repeatable-quest",
        "task-counter",
        "hideout-production",
        "hideout-area",
        "unlocked-recipe",
        "insured-item",
        "achievement",
        "insurance",
        "dialogue",
        "userbuild",
    }
    assert before_bytes != profile_path.read_bytes()


# --------------------------------------------------------------------------
# Formatting.
# --------------------------------------------------------------------------


def test_clean_profile_with_no_hits_leaves_the_file_byte_identical_and_no_backup(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile skips profiles with no residues without modifying them.

    A profile with empty residue sets gets returned with profiles=[],
    backups=[], snapshot="", and the file bytes unchanged. No backup file
    or snapshot commit is created.
    """
    _install_mod(clean_repo, install_commit)
    untouched_profile = {
        "characters": {
            "pmc": {
                "Inventory": {"items": []},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
    profile_path = _write_profile(clean_repo, "abc", untouched_profile)
    before_bytes = profile_path.read_bytes()
    before_commits = len(clean_repo.log())
    layout = detect_layout(clean_repo.path)
    backups_dir = layout.root / layout.profile_backups_dir
    before_backup_entries = set(backups_dir.iterdir())

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert result.profiles == []
    assert result.backups == []
    assert result.snapshot == ""
    assert profile_path.read_bytes() == before_bytes
    assert len(clean_repo.log()) == before_commits
    assert set(backups_dir.iterdir()) == before_backup_entries


def test_clean_profile_writes_compact_json_and_preserves_key_order_and_non_ascii(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile writes JSON in compact format matching SPT's own format."""
    _install_mod(clean_repo, install_commit)
    original = _full_profile()
    profile_path = _write_profile(clean_repo, "abc", original)

    services.clean_profile(clean_repo.path, MOD_NAME)

    raw = profile_path.read_bytes().decode("utf-8")
    assert not raw.startswith(" ") and not raw.endswith("\n")
    assert ", " not in raw and ": " not in raw
    assert "Тест" in raw
    cleaned = json.loads(raw)
    assert list(cleaned.keys()) == list(original.keys())
    assert list(cleaned["info"].keys()) == list(original["info"].keys())


# --------------------------------------------------------------------------
# Safety net: backup and snapshot.
# --------------------------------------------------------------------------


def test_clean_profile_writes_a_backup_with_the_pre_clean_bytes_within_a_utc_window(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile creates a timestamped backup of the original profile before cleaning.

    The backup file is named with a UTC timestamp (YYYY-MM-DD_HH-MM-SS),
    stored in backups/ within the profiles directory, and contains the
    exact bytes read before any modification.
    """
    _install_mod(clean_repo, install_commit)
    profile_path = _write_profile(clean_repo, "abc", _full_profile())
    before_bytes = profile_path.read_bytes()
    layout = detect_layout(clean_repo.path)

    started = datetime.now(UTC)
    result = services.clean_profile(clean_repo.path, MOD_NAME)
    finished = datetime.now(UTC)

    assert len(result.backups) == 1
    backup_relpath = result.backups[0]
    assert backup_relpath.name == "abc.json"
    assert backup_relpath.parts[:3] == ("SPT", "user", "profiles")
    assert backup_relpath.parts[3] == "backups"
    stamp = backup_relpath.parts[4]
    stamp_time = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=UTC)
    assert started - timedelta(seconds=1) <= stamp_time <= finished + timedelta(seconds=1)

    backup_path = layout.root / backup_relpath
    assert backup_path.read_bytes() == before_bytes


def test_clean_profile_snapshot_has_no_mod_trailer_and_leaves_the_tree_clean(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile creates a snapshot commit with no Mod trailer and leaves the tree clean.

    The snapshot commit subject is 'snapshot: cleaned profiles for {MOD_NAME}',
    contains no Mod trailer, and leaves the repository clean with no
    uncommitted changes.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert result.snapshot != ""
    snapshot_commit = next(c for c in clean_repo.log() if c.hash == result.snapshot)
    assert snapshot_commit.subject == f"snapshot: cleaned profiles for {MOD_NAME}"
    assert "Mod" not in snapshot_commit.trailers
    assert clean_repo.is_clean() is True


def test_clean_profile_secures_a_dirty_profile_with_an_automatic_snapshot_first(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile snapshots uncommitted profile changes before cleaning.

    When the profile directory is dirty (has uncommitted changes), clean_profile
    creates two commits: an automatic snapshot, then the cleanup snapshot.
    The newer cleanup snapshot is returned as result.snapshot.
    """
    _install_mod(clean_repo, install_commit)
    profile_path = clean_repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(json.dumps(_full_profile()), encoding="utf-8")
    before_commits = len(clean_repo.log())

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    after = clean_repo.log()
    assert len(after) == before_commits + 2
    assert after[0].subject == f"snapshot: cleaned profiles for {MOD_NAME}"
    assert after[1].subject == "snapshot: automatic snapshot before changing mods"
    assert result.snapshot == after[0].hash


# --------------------------------------------------------------------------
# Selection and errors.
# --------------------------------------------------------------------------


def test_clean_profile_profiles_param_restricts_cleaning_to_the_named_stem(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile with profiles parameter cleans only named stems.

    When profiles=["abc"] is passed, only the profile matching that stem is
    cleaned, and other profiles like def.json are left unchanged.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())
    other_path = _write_profile(clean_repo, "def", _full_profile())
    before_other_bytes = other_path.read_bytes()

    result = services.clean_profile(clean_repo.path, MOD_NAME, profiles=["abc"])

    assert result.profiles == ["abc"]
    assert other_path.read_bytes() == before_other_bytes


def test_clean_profile_raises_invalid_path_error_for_an_unknown_profile_stem(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile raises InvalidPathError when profiles parameter names a non-existent stem.

    Passing profiles=["ghost"] when only abc.json exists raises InvalidPathError.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())

    with pytest.raises(InvalidPathError):
        services.clean_profile(clean_repo.path, MOD_NAME, profiles=["ghost"])


def test_clean_profile_skips_an_unreadable_profile_and_warns(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """clean_profile reports unreadable profiles in warnings and continues processing.

    When one profile has invalid JSON and another is valid, clean_profile
    includes the broken one in result.warnings, skips it, cleans the good one,
    and returns normally.
    """
    _install_mod(clean_repo, install_commit)
    good_path = _write_profile(clean_repo, "abc", _full_profile())
    broken_relpath = Path("SPT/user/profiles/broken.json")
    broken_path = clean_repo.path / broken_relpath
    broken_path.write_text("{not valid json", encoding="utf-8")
    clean_repo.commit("chore: broken profile", paths=[broken_relpath])
    broken_before = broken_path.read_bytes()

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert "broken.json" in " ".join(result.warnings)
    assert broken_path.read_bytes() == broken_before
    assert good_path.exists()
    assert "abc" in result.profiles


def test_clean_profile_raises_game_running_error_while_the_game_runs(
    clean_repo: Repo, install_commit: InstallCommit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clean_profile raises GameRunningError before touching profiles when the game is running.

    When running_game_processes() returns a non-empty list, clean_profile
    raises GameRunningError without modifying any files.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"])

    with pytest.raises(GameRunningError):
        services.clean_profile(clean_repo.path, MOD_NAME)


def test_clean_profile_raises_mod_not_found_for_unknown_name(
    clean_repo: Repo,
) -> None:
    """clean_profile raises ModNotFoundError when the mod name does not exist.

    Calling clean_profile(path, "Ghost") raises ModNotFoundError because no
    mod by that name has been installed.
    """
    with pytest.raises(ModNotFoundError):
        services.clean_profile(clean_repo.path, "Ghost")


def test_clean_profile_raises_ambiguous_mod_error_for_case_variants(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile raises AmbiguousModError when multiple case variants exist.

    After installing both "CoolMod" and "coolmod", calling clean_profile(path,
    "COOLMOD") raises AmbiguousModError.
    """
    _install_mod(clean_repo, install_commit, name="CoolMod", minute=1)
    install_commit(clean_repo, "coolmod", minute=2)

    with pytest.raises(AmbiguousModError):
        services.clean_profile(clean_repo.path, "COOLMOD")


def test_clean_profile_write_failure_surfaces_as_sptmod_error(
    clean_repo: Repo, install_commit: InstallCommit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clean_profile raises SptmodError when a write fails and leaves the profile unchanged.

    When os.replace raises OSError, clean_profile wraps it in SptmodError
    naming the profile file and leaves the original bytes intact.
    """
    _install_mod(clean_repo, install_commit)
    profile_path = _write_profile(clean_repo, "abc", _full_profile())
    before_bytes = profile_path.read_bytes()

    def _raise_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("sptmod.core.profile.os.replace", _raise_replace)

    with pytest.raises(SptmodError) as exc_info:
        services.clean_profile(clean_repo.path, MOD_NAME)

    assert "abc.json" in str(exc_info.value)
    assert "Already cleaned" not in str(exc_info.value)
    assert profile_path.read_bytes() == before_bytes


def test_clean_profile_partial_failure_snapshots_prior_successes_and_names_them(
    clean_repo: Repo, install_commit: InstallCommit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clean_profile commits successfully cleaned profiles before raising on a later failure.

    When cleaning multiple profiles and one write fails, clean_profile
    creates a snapshot commit for the successful ones, then raises SptmodError
    naming the failed profile and listing already-cleaned ones.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())
    failing_path = _write_profile(clean_repo, "xyz", _full_profile())
    before_failing_bytes = failing_path.read_bytes()

    real_replace = os.replace

    def _selective_raise(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        if str(target).endswith("xyz.json"):
            raise OSError("disk full")
        real_replace(source, target)

    monkeypatch.setattr("sptmod.core.profile.os.replace", _selective_raise)

    with pytest.raises(SptmodError) as exc_info:
        services.clean_profile(clean_repo.path, MOD_NAME)

    message = str(exc_info.value)
    assert "xyz.json" in message
    assert "Already cleaned: abc" in message
    assert failing_path.read_bytes() == before_failing_bytes
    assert clean_repo.is_clean() is True
    snapshot_commit = clean_repo.log()[0]
    assert snapshot_commit.subject == f"snapshot: cleaned profiles for {MOD_NAME}"


# --------------------------------------------------------------------------
# remove_mod(clean_profiles=True).
# --------------------------------------------------------------------------


def test_remove_mod_default_clean_profiles_false_leaves_cleanup_none(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod with default clean_profiles=False returns result.cleanup == None.

    When remove_mod is called without clean_profiles=True, no profile
    cleanup occurs and the result carries no cleanup information.
    """
    install_commit(clean_repo, "ModA")

    result = services.remove_mod(clean_repo.path, "ModA")

    assert result.cleanup is None


def test_remove_mod_clean_profiles_true_cleans_before_reverting_in_history_order(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod with clean_profiles=True cleans profiles, then reverts in chronological order.

    The cleanup snapshot is committed before the revert commit (in git history
    order, cleanup's hash is later). The result includes result.cleanup with
    profiles list and result.cleanup.snapshot set to the cleanup commit hash.
    """
    _install_mod(clean_repo, install_commit)
    _write_profile(clean_repo, "abc", _full_profile())

    result = services.remove_mod(clean_repo.path, MOD_NAME, clean_profiles=True)

    assert result.cleanup is not None
    assert result.cleanup.profiles == ["abc"]

    commits = clean_repo.log()
    revert_commit = next(c for c in commits if c.hash == result.commit)
    assert revert_commit.trailers.get("Mod-State") == "removed"
    cleanup_commit = next(c for c in commits if c.hash == result.cleanup.snapshot)
    assert "Mod" not in cleanup_commit.trailers

    # Cleanup landed before the revert: the revert commit's parent chain
    # reaches the cleanup snapshot, i.e. the cleanup commit is the older one.
    hashes = [c.hash for c in commits]
    assert hashes.index(result.commit) < hashes.index(result.cleanup.snapshot)


def test_remove_mod_clean_profiles_true_skips_the_residue_guard_without_force(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod with clean_profiles=True skips the residue guard even with force=False.

    When a profile contains only string-fallback residues (IDs in text fields
    that cleanup cannot remove), clean_profiles=True succeeds with no revert
    because cleanup can proceed, whereas it would normally raise
    ProfileResidueError at the residue guard.
    """
    _install_mod(clean_repo, install_commit)
    profile = _full_profile()
    # An ID sptmod cannot structurally attribute: only the string fallback
    # would find it, and cleanup never removes a string-only residue. With
    # force=False and clean_profiles=False this would raise ProfileResidueError.
    profile["spt"] = {"note": STRING_ONLY_ITEM_ID}
    _write_profile(clean_repo, "abc", profile)

    result = services.remove_mod(clean_repo.path, MOD_NAME, force=False, clean_profiles=True)

    assert result.cleanup is not None
    assert any(u.residue.category == "string" for u in result.cleanup.untouched)
    active_names = {mod.name for mod in services.list_mods(clean_repo.path)}
    assert MOD_NAME not in active_names


def test_remove_mod_clean_profiles_true_merges_cleanup_warnings(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod with clean_profiles=True includes cleanup warnings in result.warnings.

    When a profile is unreadable, the cleanup warning is reported both in
    result.cleanup.warnings and result.warnings.
    """
    _install_mod(clean_repo, install_commit)
    broken_relpath = Path("SPT/user/profiles/broken.json")
    broken_path = clean_repo.path / broken_relpath
    broken_path.write_text("{not valid json", encoding="utf-8")
    clean_repo.commit("chore: broken profile", paths=[broken_relpath])

    result = services.remove_mod(clean_repo.path, MOD_NAME, clean_profiles=True)

    assert result.cleanup is not None
    assert result.warnings == result.cleanup.warnings
    assert "broken.json" in " ".join(result.warnings)


# --------------------------------------------------------------------------
# Robustness: malformed entries alongside real hits must not crash the
# removers, mirroring the tolerance ``find_residues`` already has to prove.
# --------------------------------------------------------------------------


def test_clean_profile_tolerates_malformed_entries_mixed_with_real_hits(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile removes mod hits and preserves non-dict entries without raising.

    Profiles with mixed valid and malformed container entries (e.g. strings
    in items arrays, non-dict repeatable quest groups) get cleaned of mod IDs
    while preserving the malformed entries intact.
    """
    _install_mod(clean_repo, install_commit)
    profile = {
        "characters": {
            "pmc": {
                "Inventory": {
                    "items": [
                        {"_id": "root", "_tpl": MOD_ITEM_ID, "slotId": "stash"},
                        "not-a-dict-item",
                    ],
                    "favoriteItems": [],
                    "fastPanel": "not-a-dict-panel",
                },
                "TradersInfo": {},
                "Quests": [],
                "RepeatableQuests": [
                    "not-a-dict-group",
                    {
                        "id": "rq",
                        "name": "Daily",
                        "activeQuests": [{"_id": MOD_QUEST_ID, "traderId": "x"}],
                        # inactiveQuests deliberately absent: _filter_list must
                        # tolerate a missing list key, not just an empty one.
                    },
                ],
            }
        },
        "insurance": [
            "not-a-dict-entry",
            {"traderId": MOD_TRADER_ID, "items": []},
        ],
        "dialogues": {
            MOD_TRADER_ID: {"_id": MOD_TRADER_ID, "messages": []},
            "malformed-dialogue-key": "not-a-dict-dialogue",
        },
    }
    profile_path = _write_profile(clean_repo, "abc", profile)

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    cleaned = json.loads(profile_path.read_text(encoding="utf-8"))
    pmc_items = cleaned["characters"]["pmc"]["Inventory"]["items"]
    assert pmc_items == ["not-a-dict-item"]
    assert cleaned["characters"]["pmc"]["Inventory"]["fastPanel"] == ("not-a-dict-panel")
    repeatable = cleaned["characters"]["pmc"]["RepeatableQuests"]
    assert repeatable[0] == "not-a-dict-group"
    assert repeatable[1]["activeQuests"] == []
    assert "inactiveQuests" not in repeatable[1]
    assert cleaned["insurance"] == ["not-a-dict-entry"]
    assert cleaned["dialogues"] == {"malformed-dialogue-key": "not-a-dict-dialogue"}
    assert {r.category for r in result.removed} >= {
        "item",
        "repeatable-quest",
        "insurance",
        "dialogue",
    }
