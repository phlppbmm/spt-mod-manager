"""Tests for profile selection, error handling and ``remove_mod(clean_profiles=True)``.

Covers which profiles ``clean_profile`` touches and how it reports missing
mods, unwritable files and a running game, the ordering and skip-guard
semantics of ``remove_mod(clean_profiles=True)``, and the tolerance of the
removers towards malformed entries alongside real hits.
"""

import json
import os
from pathlib import Path

import pytest

from fixture_types import InstallCommit, JsonDict
from profile_cleanup_corpus import (
    MOD_ITEM_ID,
    MOD_NAME,
    MOD_QUEST_ID,
    MOD_TRADER_ID,
    STRING_ONLY_ITEM_ID,
    full_profile,
    install_mod,
    write_profile,
)
from sptmod.core import game_dir, services
from sptmod.core.errors import (
    AmbiguousModError,
    GameRunningError,
    InvalidPathError,
    ModFileWriteError,
    ModNotFoundError,
    SptmodError,
)
from sptmod.core.repo import Repo

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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())
    other_path = write_profile(clean_repo, "def", full_profile())
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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())

    with pytest.raises(InvalidPathError):
        services.clean_profile(clean_repo.path, MOD_NAME, profiles=["ghost"])


def test_clean_profile_skips_an_unreadable_profile_and_warns(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """clean_profile reports unreadable profiles in warnings and continues processing.

    When one profile has invalid JSON and another is valid, clean_profile
    includes the broken one in result.warnings, skips it, cleans the good one,
    and returns normally.
    """
    install_mod(clean_repo, install_commit)
    good_path = write_profile(clean_repo, "abc", full_profile())
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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())
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
    install_mod(clean_repo, install_commit, name="CoolMod", minute=1)
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
    install_mod(clean_repo, install_commit)
    profile_path = write_profile(clean_repo, "abc", full_profile())
    before_bytes = profile_path.read_bytes()

    def _raise_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("sptmod.core.profile.files.os.replace", _raise_replace)

    with pytest.raises(SptmodError) as exc_info:
        services.clean_profile(clean_repo.path, MOD_NAME)

    assert "abc.json" in str(exc_info.value)
    assert "Already cleaned" not in str(exc_info.value)
    assert profile_path.read_bytes() == before_bytes


def test_clean_profile_reports_a_backups_folder_that_refuses_a_copy(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile raises ModFileWriteError when the backup copy cannot be written.

    The game's own backups folder is read-only, so the copy that has to be made before a
    profile is written cannot be created. The failure must reach the caller as the typed
    write error naming the profile, and the profile itself must stay byte-identical.
    """
    install_mod(clean_repo, install_commit)
    profile_path = write_profile(clean_repo, "abc", full_profile())
    before_bytes = profile_path.read_bytes()
    obstacle = clean_repo.path / "SPT/user/profiles/backups"
    obstacle.chmod(0o555)

    try:
        with pytest.raises(ModFileWriteError) as exc_info:
            services.clean_profile(clean_repo.path, MOD_NAME)
    finally:
        obstacle.chmod(0o755)

    assert "abc.json" in str(exc_info.value)
    assert profile_path.read_bytes() == before_bytes


def test_clean_profile_partial_failure_snapshots_prior_successes_and_names_them(
    clean_repo: Repo, install_commit: InstallCommit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clean_profile commits successfully cleaned profiles before raising on a later failure.

    When cleaning multiple profiles and one write fails, clean_profile
    creates a snapshot commit for the successful ones, then raises SptmodError
    naming the failed profile and listing already-cleaned ones.
    """
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())
    failing_path = write_profile(clean_repo, "xyz", full_profile())
    before_failing_bytes = failing_path.read_bytes()

    real_replace = os.replace

    def _selective_raise(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        if str(target).endswith("xyz.json"):
            raise OSError("disk full")
        real_replace(source, target)

    monkeypatch.setattr("sptmod.core.profile.files.os.replace", _selective_raise)

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
    install_mod(clean_repo, install_commit)
    write_profile(clean_repo, "abc", full_profile())

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
    install_mod(clean_repo, install_commit)
    profile = full_profile()
    # An ID sptmod cannot structurally attribute: only the string fallback
    # would find it, and cleanup never removes a string-only residue. With
    # force=False and clean_profiles=False this would raise ProfileResidueError.
    profile["spt"] = {"note": STRING_ONLY_ITEM_ID}
    write_profile(clean_repo, "abc", profile)

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
    install_mod(clean_repo, install_commit)
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
    install_mod(clean_repo, install_commit)
    profile: JsonDict = {
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
    profile_path = write_profile(clean_repo, "abc", profile)

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
