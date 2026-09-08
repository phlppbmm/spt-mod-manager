"""Tests for ``sptmod.core.services.modsets.apply_mod_set``: turning the mods
of a game directory into a mod set, one toggle at a time.

Runs against real ``clean_repo`` git history seeded with ``install_commit``,
exercising the real ``disable_mod``/``enable_mod`` calls apply_mod_set makes
(rather than stubbing them), so a genuine ``ConflictError`` and a genuine held
lock are reachable the same way a caller would hit them.
"""

from pathlib import Path

import pytest

from fixture_types import InstallCommit
from sptmod.core import game_dir, services, workspace
from sptmod.core.errors import ConflictError, GameRunningError, ModSetIncompleteError
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")


def _entry(name: str, *, version: str = "1.0.0", enabled: bool = True) -> services.ModSetEntry:
    return services.ModSetEntry(forge_id="", name=name, version=version, enabled=enabled)


class _Recorder:
    """Collects the ``(step, percent, message)`` tuples a Progress callback receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, step: str, percent: int, message: str) -> None:
        self.calls.append((step, percent, message))


def test_apply_toggles_exactly_the_planned_mods_and_leaves_no_lock_held(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: nothing would be enabled, disabled or
    locked at all, so this test's own assertions on the resulting mod states
    would fail."""
    install_commit(clean_repo, "ToDisable", "1.0.0", state="installed")
    install_commit(clean_repo, "ToEnable", "1.0.0", state="installed")
    services.disable_mod(clean_repo.path, "ToEnable")
    install_commit(clean_repo, "Untouched", "1.0.0", state="installed")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ToDisable", enabled=False), _entry("ToEnable"), _entry("Untouched")],
        store_path=mod_set_store,
    )

    result = services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    assert result.enabled == ["ToEnable"]
    assert result.disabled == ["ToDisable"]
    assert not result.missing
    assert not result.notices
    mods = {mod.name: mod.state for mod in services.list_mods(clean_repo.path, include_all=True)}
    assert mods["ToDisable"] == "disabled"
    assert mods["ToEnable"] == "enabled"
    assert mods["Untouched"] == "installed"
    assert workspace.is_locked(clean_repo.path) is False


def test_apply_disables_before_it_enables(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If disables and enables were interleaved or reordered, this fails: a
    'Turning on' message would precede a 'Turning off' one."""
    install_commit(clean_repo, "ToDisable", "1.0.0", state="installed")
    install_commit(clean_repo, "ToEnable", "1.0.0", state="installed")
    services.disable_mod(clean_repo.path, "ToEnable")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ToDisable", enabled=False), _entry("ToEnable")],
        store_path=mod_set_store,
    )
    recorder = _Recorder()

    services.apply_mod_set(clean_repo.path, "Fika night", progress=recorder, store_path=mod_set_store)

    messages = [message for _step, _percent, message in recorder.calls]
    assert messages.index("Turning off ToDisable") < messages.index("Turning on ToEnable")


def test_a_second_identical_apply_is_a_complete_noop(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the plan did not converge to 'nothing left to do', this fails:
    disabled/enabled would list ToDisable/ToEnable again instead of both
    coming back empty on the repeated apply."""
    install_commit(clean_repo, "ToDisable", "1.0.0", state="installed")
    install_commit(clean_repo, "ToEnable", "1.0.0", state="installed")
    services.disable_mod(clean_repo.path, "ToEnable")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ToDisable", enabled=False), _entry("ToEnable")],
        store_path=mod_set_store,
    )
    services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    second = services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    assert not second.enabled
    assert not second.disabled
    assert not second.notices


def test_a_mod_named_twice_by_the_same_set_reports_its_second_toggle_as_a_notice(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the ToggleResult.notice values were dropped instead of collected,
    this fails: notices would be empty and DupMod would appear twice in
    enabled instead of once, with one notice explaining the second toggle was
    already done."""
    install_commit(clean_repo, "DupMod", "1.0.0", state="installed")
    services.disable_mod(clean_repo.path, "DupMod")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("DupMod"), _entry("DupMod")],
        store_path=mod_set_store,
    )

    result = services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    assert result.enabled == ["DupMod"]
    assert len(result.notices) == 1
    assert "DupMod" in result.notices[0]


def test_missing_mods_raise_incomplete_error_carrying_the_names_and_toggle_nothing(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the pre-toggle missing check were dropped, this fails: ToDisable
    would end up disabled (partial apply) instead of nothing being touched and
    ModSetIncompleteError.missing naming GhostMod."""
    install_commit(clean_repo, "ToDisable", "1.0.0", state="installed")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ToDisable", enabled=False), _entry("GhostMod")],
        store_path=mod_set_store,
    )

    with pytest.raises(ModSetIncompleteError) as exc_info:
        services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    assert exc_info.value.name == "Fika night"
    assert exc_info.value.missing == ["GhostMod"]
    mods = {mod.name: mod.state for mod in services.list_mods(clean_repo.path, include_all=True)}
    assert mods["ToDisable"] == "installed"


def test_ignore_missing_applies_the_rest_of_the_set(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If ignore_missing were not threaded through, this fails: apply_mod_set
    would still raise ModSetIncompleteError instead of disabling ToDisable and
    reporting GhostMod as missing."""
    install_commit(clean_repo, "ToDisable", "1.0.0", state="installed")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ToDisable", enabled=False), _entry("GhostMod")],
        store_path=mod_set_store,
    )

    result = services.apply_mod_set(
        clean_repo.path, "Fika night", ignore_missing=True, store_path=mod_set_store
    )

    assert result.disabled == ["ToDisable"]
    assert result.missing == ["GhostMod"]
    mods = {mod.name: mod.state for mod in services.list_mods(clean_repo.path, include_all=True)}
    assert mods["ToDisable"] == "disabled"


def test_a_conflict_with_a_later_mod_propagates_and_leaves_the_tree_clean(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If apply_mod_set swallowed or wrapped the error, this fails: no
    ConflictError would propagate to the caller."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/shared.dll": "base"}, minute=1)
    install_commit(
        clean_repo, "ModB", files={"BepInEx/plugins/shared.dll": "overwritten-by-modb"}, minute=2
    )
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ModA", enabled=False), _entry("ModB")],
        store_path=mod_set_store,
    )

    with pytest.raises(ConflictError) as exc_info:
        services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    assert "ModB" in str(exc_info.value)
    assert clean_repo.is_clean() is True


def test_a_running_game_raises_before_any_toggle(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ensure_installable were skipped, this fails: ToDisable would end up
    disabled instead of GameRunningError being raised with nothing touched."""
    install_commit(clean_repo, "ToDisable", "1.0.0", state="installed")
    services.save_mod_set(
        clean_repo.path, "Fika night", entries=[_entry("ToDisable", enabled=False)], store_path=mod_set_store
    )
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"])

    with pytest.raises(GameRunningError):
        services.apply_mod_set(clean_repo.path, "Fika night", store_path=mod_set_store)

    mods = {mod.name: mod.state for mod in services.list_mods(clean_repo.path, include_all=True)}
    assert mods["ToDisable"] == "installed"


def test_progress_reports_plan_then_per_mod_steps_then_done_with_monotonic_percent(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If the phase reporting were dropped, this fails: recorder.calls would
    be empty instead of starting at plan/0 and ending at done/100."""
    install_commit(clean_repo, "ToDisableOne", "1.0.0", state="installed")
    install_commit(clean_repo, "ToDisableTwo", "1.0.0", state="installed")
    install_commit(clean_repo, "ToEnable", "1.0.0", state="installed")
    services.disable_mod(clean_repo.path, "ToEnable")
    services.save_mod_set(
        clean_repo.path,
        "Fika night",
        entries=[_entry("ToDisableOne", enabled=False), _entry("ToDisableTwo", enabled=False), _entry("ToEnable")],
        store_path=mod_set_store,
    )
    recorder = _Recorder()

    services.apply_mod_set(clean_repo.path, "Fika night", progress=recorder, store_path=mod_set_store)

    assert recorder.calls[0] == (services.ModSetStep.PLAN, 0, "Working out what to change")
    assert recorder.calls[-1] == (services.ModSetStep.DONE, 100, "The mod set is applied")
    percentages = [percent for _step, percent, _message in recorder.calls]
    assert percentages == sorted(percentages)
    middle_steps = [step for step, _percent, _message in recorder.calls[1:-1]]
    assert set(middle_steps) == {services.ModSetStep.DISABLE, services.ModSetStep.ENABLE}


def test_zero_total_changes_reports_plan_then_done_immediately(clean_repo: Repo, mod_set_store: Path) -> None:
    """If the loop guard for an empty plan were missing, this fails: recorder
    would either error out or report an extra step between plan and done."""
    services.save_mod_set(clean_repo.path, "Empty set", entries=[], store_path=mod_set_store)
    recorder = _Recorder()

    services.apply_mod_set(clean_repo.path, "Empty set", progress=recorder, store_path=mod_set_store)

    assert recorder.calls == [
        (services.ModSetStep.PLAN, 0, "Working out what to change"),
        (services.ModSetStep.DONE, 100, "The mod set is applied"),
    ]
