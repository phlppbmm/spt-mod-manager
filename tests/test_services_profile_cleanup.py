"""Tests for ``sptmod.core.services.plan_profile_cleanup`` and ``clean_profile``.

Covers the plan/apply dry-run guarantee, structural removal of every residue
category on both characters and the shared top-level sections (including the
``parentId`` item closure, dangling ``favoriteItems``/``fastPanel``
references, and the hideout-area slot reset), byte-level formatting of an
untouched profile, and the backup-and-snapshot safety net.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fixture_types import InstallCommit
from fixtures_profile import empty_pmc_profile
from profile_cleanup_corpus import (
    MOD_NAME,
    MOD_TRADER_ID,
    OTHER_ACHIEVEMENT_ID,
    OTHER_ITEM_ID,
    OTHER_QUEST_ID,
    OTHER_RECIPE_ID,
    OTHER_TRADER_ID,
    full_profile,
    install_mod,
    write_profile,
)
from sptmod.core import services
from sptmod.core.errors import AmbiguousModError, ModNotFoundError
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

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
    install_mod(clean_repo, install_commit)
    profile_path = write_profile(clean_repo, "abc", full_profile())
    before_bytes = profile_path.read_bytes()
    before_head = clean_repo.log()[0].hash
    layout = detect_layout(clean_repo.path)
    backups_dir = layout.root / layout.profile_backups_dir
    # fake_game_dir seeds a dummy backups/old.json; only new entries count.
    before_backup_entries = set(backups_dir.iterdir())

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)

    assert plan.removals
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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())

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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())
    services.remove_mod(clean_repo.path, MOD_NAME, force=True)

    plan = services.plan_profile_cleanup(clean_repo.path, MOD_NAME)

    assert plan.removals
    assert plan.mod == MOD_NAME


def test_plan_profile_cleanup_skips_an_unreadable_profile_and_warns(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """plan_profile_cleanup reports unreadable profiles in warnings and continues.

    A profile with invalid JSON ('{not valid json') is included in
    plan.warnings with its filename but skipped from plan.profiles, and
    planning proceeds without raising.
    """
    install_mod(clean_repo, install_commit)
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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "hit", full_profile())
    write_profile(
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
    install_mod(clean_repo, install_commit, name="CoolMod", minute=1)
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
    install_mod(clean_repo, install_commit)
    original = full_profile()
    profile_path = write_profile(clean_repo, "abc", original)
    before_bytes = profile_path.read_bytes()

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert result.profiles == ["abc"]
    assert not result.untouched
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
    install_mod(clean_repo, install_commit)
    untouched_profile = empty_pmc_profile()
    profile_path = write_profile(clean_repo, "abc", untouched_profile)
    before_bytes = profile_path.read_bytes()
    before_commits = len(clean_repo.log())
    layout = detect_layout(clean_repo.path)
    backups_dir = layout.root / layout.profile_backups_dir
    before_backup_entries = set(backups_dir.iterdir())

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    assert not result.profiles
    assert not result.backups
    assert result.snapshot == ""
    assert profile_path.read_bytes() == before_bytes
    assert len(clean_repo.log()) == before_commits
    assert set(backups_dir.iterdir()) == before_backup_entries


def test_clean_profile_writes_compact_json_and_preserves_key_order_and_non_ascii(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile writes JSON in compact format matching SPT's own format."""
    install_mod(clean_repo, install_commit)
    original = full_profile()
    profile_path = write_profile(clean_repo, "abc", original)

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
    install_mod(clean_repo, install_commit)
    profile_path = write_profile(clean_repo, "abc", full_profile())
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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())

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
    install_mod(clean_repo, install_commit)
    profile_path = clean_repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(json.dumps(full_profile()), encoding="utf-8")
    before_commits = len(clean_repo.log())

    result = services.clean_profile(clean_repo.path, MOD_NAME)

    after = clean_repo.log()
    assert len(after) == before_commits + 2
    assert after[0].subject == f"snapshot: cleaned profiles for {MOD_NAME}"
    assert after[1].subject == "snapshot: automatic snapshot before changing mods"
    assert result.snapshot == after[0].hash
