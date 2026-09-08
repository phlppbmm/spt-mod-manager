"""Tests for ``sptmod.core.services.modsets.plan_mod_set``: what applying a set
would change, read-only.

Mods are seeded directly through the ``install_commit`` fixture (its ``state``
argument writes the youngest commit's ``Mod-State`` trailer directly, without
going through ``disable_mod``/``enable_mod``), since ``plan_mod_set`` only ever
reads ``list_mods``. Sets are built with ``services.save_mod_set(entries=...)``
against the ``mod_set_store`` fixture, never the developer's real store.
"""

from pathlib import Path

import pytest

from fixture_types import InstallCommit, TrailerInstallCommit
from sptmod.core import services
from sptmod.core.errors import ModSetNotFoundError
from sptmod.core.repo import Repo


def _entry(name: str, *, forge_id: str = "", version: str = "1.0.0", enabled: bool = True) -> services.ModSetEntry:
    return services.ModSetEntry(forge_id=forge_id, name=name, version=version, enabled=enabled)


def _plan(clean_repo: Repo, mod_set_store: Path, entries: list[services.ModSetEntry]) -> services.ModSetPlan:
    services.save_mod_set(clean_repo.path, "Fika night", entries=entries, store_path=mod_set_store)
    return services.plan_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)


# --- the matching table --------------------------------------------------


def test_active_mod_matching_an_enabled_entry_of_the_same_version_is_unchanged(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: TargetMod is missing from unchanged
    instead of appearing there with nothing to enable or disable."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="installed")

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", version="1.0.0")])

    assert plan.unchanged == ["TargetMod"]
    assert not plan.enable
    assert not plan.disable
    assert not plan.missing
    assert not plan.different


def test_disabled_mod_matching_an_enabled_entry_is_planned_to_enable(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: plan.enable is empty instead of
    holding a change for TargetMod."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="disabled")

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", version="1.0.0")])

    assert [change.name for change in plan.enable] == ["TargetMod"]
    assert not plan.disable
    assert not plan.missing
    assert not plan.unchanged


def test_an_entry_naming_a_mod_that_was_never_installed_is_planned_as_missing(
    clean_repo: Repo, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: plan.missing is empty instead of
    holding the entry for GhostMod."""
    plan = _plan(clean_repo, mod_set_store, [_entry("GhostMod", version="1.0.0")])

    assert [entry.name for entry in plan.missing] == ["GhostMod"]
    assert not plan.enable
    assert not plan.disable
    assert not plan.unchanged


def test_a_removed_mod_matching_an_enabled_entry_is_planned_as_missing(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the removed state were not treated like 'no mod', this fails:
    TargetMod would be reported as unchanged instead of missing."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="installed", minute=1)
    install_commit(
        clean_repo,
        "TargetMod",
        "1.0.0",
        state="removed",
        minute=2,
        files={"BepInEx/plugins/TargetMod.dll": "removed-marker"},
    )

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", version="1.0.0")])

    assert [entry.name for entry in plan.missing] == ["TargetMod"]
    assert not plan.unchanged
    assert not plan.enable
    assert not plan.disable


def test_active_mod_matching_a_disabled_entry_is_planned_to_disable_with_a_reason(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the reason text were dropped, this fails: reason would be empty
    instead of explaining the set turns the mod off."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="installed")

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", version="1.0.0", enabled=False)])

    assert len(plan.disable) == 1
    assert plan.disable[0].name == "TargetMod"
    assert plan.disable[0].reason == "the set turns this mod off"
    assert not plan.enable
    assert not plan.missing
    assert not plan.unchanged


def test_disabled_mod_matching_a_disabled_entry_is_unchanged(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: TargetMod would not appear in
    unchanged at all."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="disabled")

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", version="1.0.0", enabled=False)])

    assert plan.unchanged == ["TargetMod"]
    assert not plan.enable
    assert not plan.disable
    assert not plan.missing


def test_active_mod_not_named_by_the_set_is_planned_to_disable_with_a_reason(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the 'authoritative set' rule (unnamed active mods) were dropped,
    this fails: UnlistedMod would not appear in plan.disable at all."""
    install_commit(clean_repo, "UnlistedMod", "1.0.0", state="installed")

    plan = _plan(clean_repo, mod_set_store, [])

    assert len(plan.disable) == 1
    assert plan.disable[0].name == "UnlistedMod"
    assert plan.disable[0].reason == "the set does not include this mod"
    assert not plan.enable
    assert not plan.missing


# --- forge-id-before-name linkage -----------------------------------------


def test_a_mod_is_matched_by_forge_id_even_when_its_trailer_name_differs_from_the_set(
    clean_repo: Repo, install_commit: TrailerInstallCommit, mod_set_store: Path
) -> None:
    """If matching fell back to name first, this fails: the mod would be
    reported missing (no name match) instead of unchanged (forge id match)."""
    install_commit(clean_repo, "InstallTrailerName", "1.0.0", **{"Mod-Forge-Id": "999"})

    plan = _plan(
        clean_repo,
        mod_set_store,
        [_entry("ForgeDisplayName", forge_id="999", version="1.0.0")],
    )

    assert plan.unchanged == ["InstallTrailerName"]
    assert not plan.missing


def test_a_mod_without_a_forge_id_in_the_entry_is_matched_by_name_case_insensitively(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the name fallback were case-sensitive, this fails: the mod would be
    reported missing instead of unchanged."""
    install_commit(clean_repo, "MyMod", "1.0.0")

    plan = _plan(clean_repo, mod_set_store, [_entry("mymod", version="1.0.0")])

    assert plan.unchanged == ["MyMod"]
    assert not plan.missing


# --- version differences: reported, never applied -------------------------


def test_a_version_difference_on_an_unchanged_mod_is_reported_but_the_installed_version_stays(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If different() were dropped, this fails: plan.different would be empty
    instead of reporting the version gap; if the plan applied it, plan.enable
    would wrongly hold a change for TargetMod."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="installed")

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", version="2.0.0")])

    assert plan.unchanged == ["TargetMod"]
    assert not plan.enable
    assert len(plan.different) == 1
    change = plan.different[0]
    assert change.name == "TargetMod"
    assert change.version == "2.0.0"
    assert change.installed_version == "1.0.0"
    assert change.reason == "the set names another version"


# --- a disabled entry naming a mod that was never installed is unchanged ---


def test_a_disabled_entry_naming_a_mod_that_was_never_installed_is_unchanged(
    clean_repo: Repo, mod_set_store: Path
) -> None:
    """If the no-match branch treated every disabled entry as missing, this
    fails: plan.missing would hold GhostMod instead of it landing in
    unchanged (already off, nothing to do)."""
    plan = _plan(clean_repo, mod_set_store, [_entry("GhostMod", enabled=False)])

    assert plan.unchanged == ["GhostMod"]
    assert not plan.missing
    assert not plan.enable
    assert not plan.disable


# --- an entry's forge_id that matches no installed mod falls back to name --


def test_an_entry_forge_id_matching_no_installed_mod_falls_back_to_matching_by_name(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the forge-id loop's fall-through to name matching were dropped,
    this fails: TargetMod would be reported missing instead of unchanged,
    since the entry's forge_id 'no-such-id' matches no installed mod."""
    install_commit(clean_repo, "TargetMod", "1.0.0")

    plan = _plan(clean_repo, mod_set_store, [_entry("TargetMod", forge_id="no-such-id", version="1.0.0")])

    assert plan.unchanged == ["TargetMod"]
    assert not plan.missing


# --- unknown set -----------------------------------------------------------


def test_plan_mod_set_on_an_unknown_name_raises_mod_set_not_found_error(
    clean_repo: Repo, mod_set_store: Path
) -> None:
    """If the lookup raised nothing on a miss, this fails: no exception is
    raised for a set that was never saved."""
    with pytest.raises(ModSetNotFoundError):
        services.plan_mod_set(clean_repo.path, "No such set", store_path=mod_set_store)
