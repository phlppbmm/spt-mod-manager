"""Tests for the mod set integration of ``services.launch_profile`` (the
``sptmod.core.services.gamedir`` wrapper around ``sptmod.core.launch``).

Uses ``launchable_repo`` (a ``fake_launchable_dir`` initialized as a real git
repository) so a bound mod set can genuinely enable/disable mods before the
existing launch sequence takes over. Server readiness and login are simulated
through respx, exactly like ``tests/test_launch.py``; nothing binds a real
port.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from conftest import LaunchableGameDir
from fixture_types import InstallCommit
from sptmod.core import game_dir, services
from sptmod.core.errors import ModSetIncompleteError
from sptmod.core.game_dir import GameProcess
from sptmod.core.launch import LAUNCH_STATE_FILE_NAME
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH

pytestmark = pytest.mark.timeout(5)


class _Recorder:
    """Collects the ``(step, percent, message)`` tuples a Progress callback receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, step: str, percent: int, message: str) -> None:
        self.calls.append((step, percent, message))


def _entry(name: str, *, version: str = "1.0.0", enabled: bool = True) -> services.ModSetEntry:
    return services.ModSetEntry(forge_id="", name=name, version=version, enabled=enabled)


def _no_game_processes() -> list[GameProcess]:
    """Stand in for ``game_dir.game_processes`` when nothing runs."""
    return []


def _no_running_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub game_dir.game_processes() to report nothing running.

    Covers both launch.py's own running-client check and ensure_installable's
    (which calls running_game_processes(), itself built on game_processes()).
    """
    monkeypatch.setattr(game_dir, "game_processes", _no_game_processes)


def _ready(router: respx.MockRouter) -> None:
    """The initial port check answers "free" once (spawning a fresh server);
    every later poll answers "spt", so the server-start progress step fires."""
    calls = {"n": 0}

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    router.get(PING_PATH).mock(side_effect=_side_effect)


def _login(router: respx.MockRouter) -> None:
    router.put(LOGIN_PATH).respond(200, json={"Response": True})


def test_a_bound_set_is_applied_before_the_server_starts_within_its_percent_span(
    launchable_repo: tuple[LaunchableGameDir, Repo],
    install_commit: InstallCommit,
    mod_set_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If mod_set application were skipped, this fails: ToDisable/ToEnable
    would keep their original states and no 'modset' progress step would ever
    appear before 'server'."""
    d, repo = launchable_repo
    _no_running_processes(monkeypatch)
    install_commit(repo, "ToDisable", "1.0.0", state="installed")
    install_commit(repo, "ToEnable", "1.0.0", state="installed")
    services.disable_mod(d.root, "ToEnable")
    services.save_mod_set(
        d.root,
        "Fika night",
        entries=[_entry("ToDisable", enabled=False), _entry("ToEnable")],
        store_path=mod_set_store,
    )
    services.bind_profile_set(d.root, d.profile_id, "Fika night", store_path=mod_set_store)
    recorder = _Recorder()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        services.launch_profile(d.root, d.profile_id, progress=recorder, store_path=mod_set_store)

    assert recorder.calls[0] == ("modset", 0, f"Applying the mod set for {d.nickname}")
    server_index = next(i for i, call in enumerate(recorder.calls) if call[0] == "server")
    modset_calls = recorder.calls[:server_index]
    assert modset_calls
    assert all(step == "modset" for step, _percent, _message in modset_calls)
    assert all(0 <= percent <= 4 for _step, percent, _message in modset_calls)
    assert recorder.calls[server_index][1] == 5
    mods = {mod.name: mod.state for mod in services.list_mods(d.root, include_all=True)}
    assert mods["ToDisable"] == "disabled"
    assert mods["ToEnable"] == "enabled"


def test_no_binding_leaves_the_progress_sequence_unchanged_server_first(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a phantom mod set phase were always announced, this fails: a
    'modset' step would appear even without any binding, and the first step
    would not be 'server'."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    recorder = _Recorder()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        services.launch_profile(d.root, d.profile_id, progress=recorder, store_path=mod_set_store)

    assert recorder.calls[0][0] == "server"
    assert all(step != "modset" for step, _percent, _message in recorder.calls)


def test_a_binding_for_another_profile_does_not_affect_this_launch(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the binding lookup matched the first entry regardless of profile
    id, this fails: a 'modset' step would appear even though the store's only
    binding names a different profile."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    services.save_mod_set(d.root, "Fika night", entries=[], store_path=mod_set_store)
    services.bind_profile_set(d.root, "some-other-profile", "Fika night", store_path=mod_set_store)
    recorder = _Recorder()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        services.launch_profile(d.root, d.profile_id, progress=recorder, store_path=mod_set_store)

    assert recorder.calls[0][0] == "server"
    assert all(step != "modset" for step, _percent, _message in recorder.calls)


def test_skip_mode_touches_no_mod_even_with_a_binding(
    launchable_repo: tuple[LaunchableGameDir, Repo],
    install_commit: InstallCommit,
    mod_set_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If 'skip' still applied the bound set, this fails: ToDisable would end
    up disabled instead of keeping its original 'installed' state."""
    d, repo = launchable_repo
    _no_running_processes(monkeypatch)
    install_commit(repo, "ToDisable", "1.0.0", state="installed")
    services.save_mod_set(
        d.root, "Fika night", entries=[_entry("ToDisable", enabled=False)], store_path=mod_set_store
    )
    services.bind_profile_set(d.root, d.profile_id, "Fika night", store_path=mod_set_store)
    recorder = _Recorder()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        services.launch_profile(d.root, d.profile_id, mod_set="skip", progress=recorder, store_path=mod_set_store)

    assert all(step != "modset" for step, _percent, _message in recorder.calls)
    mods = {mod.name: mod.state for mod in services.list_mods(d.root, include_all=True)}
    assert mods["ToDisable"] == "installed"


def test_missing_mods_raise_incomplete_error_and_start_no_process(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the launch proceeded despite missing mods, this fails: the launch
    state file or the stub server's dump file would exist afterward."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    services.save_mod_set(d.root, "Fika night", entries=[_entry("GhostMod")], store_path=mod_set_store)
    services.bind_profile_set(d.root, d.profile_id, "Fika night", store_path=mod_set_store)

    with pytest.raises(ModSetIncompleteError) as exc_info:
        services.launch_profile(d.root, d.profile_id, store_path=mod_set_store)

    assert exc_info.value.missing == ["GhostMod"]
    assert not (d.root / LAUNCH_STATE_FILE_NAME).exists()
    assert not Path(f"{d.server_binary}.dump.json").exists()


def test_ignore_missing_mode_launches_despite_missing_mods(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ignore_missing were not threaded through launch_profile, this
    fails: ModSetIncompleteError would still be raised instead of the launch
    completing."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    services.save_mod_set(d.root, "Fika night", entries=[_entry("GhostMod")], store_path=mod_set_store)
    services.bind_profile_set(d.root, d.profile_id, "Fika night", store_path=mod_set_store)

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        result = services.launch_profile(
            d.root, d.profile_id, mod_set="ignore_missing", store_path=mod_set_store
        )

    assert result.profile_id == d.profile_id
    assert result.server_started is True


def test_nickname_falls_back_to_the_profile_id_when_the_profile_has_none(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the nickname lookup did not fall back, this fails: the message
    would read 'Applying the mod set for ' (empty nickname) instead of naming
    the profile id."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    profile_file = d.root / "SPT_Runtime" / "user" / "profiles" / f"{d.profile_id}.json"
    profile = json.loads(profile_file.read_text(encoding="utf-8"))
    profile["characters"]["pmc"]["Info"]["Nickname"] = ""
    profile_file.write_text(json.dumps(profile), encoding="utf-8")
    services.save_mod_set(d.root, "Fika night", entries=[], store_path=mod_set_store)
    services.bind_profile_set(d.root, d.profile_id, "Fika night", store_path=mod_set_store)
    recorder = _Recorder()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        services.launch_profile(d.root, d.profile_id, progress=recorder, store_path=mod_set_store)

    assert recorder.calls[0] == ("modset", 0, f"Applying the mod set for {d.profile_id}")


def test_a_damaged_mod_set_store_refuses_the_launch_and_starts_no_process(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If an unreadable store were treated as "no sets", this fails: the launch
    would go through, writing the launch state file and the stub server's dump
    file, instead of raising InvalidMetadataError."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    mod_set_store.write_text("{", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        services.launch_profile(d.root, d.profile_id, store_path=mod_set_store)

    assert not (d.root / LAUNCH_STATE_FILE_NAME).exists()
    assert not Path(f"{d.server_binary}.dump.json").exists()


def test_skip_mode_launches_even_though_the_mod_set_store_is_damaged(
    launchable_repo: tuple[LaunchableGameDir, Repo], mod_set_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If 'skip' still read the stored sets, this fails: the unreadable store
    raises InvalidMetadataError instead of the launch completing."""
    d, _repo = launchable_repo
    _no_running_processes(monkeypatch)
    mod_set_store.write_text("{", encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _ready(router)
        _login(router)
        result = services.launch_profile(d.root, d.profile_id, mod_set="skip", store_path=mod_set_store)

    assert result.profile_id == d.profile_id
    assert result.server_started is True
