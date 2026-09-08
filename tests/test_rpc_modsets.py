"""In-process ``sptmod rpc <name>`` tests for the nine mod set functions and
their error envelopes (``MOD_SET_NOT_FOUND``, ``MOD_SET_EXISTS``,
``MOD_SET_INCOMPLETE``), plus ``launch_profile`` accepting ``mod_set`` and
``store_path``. Behaviour of the underlying service functions themselves is
covered exhaustively in ``tests/test_modsets_store.py``,
``tests/test_modsets_plan.py``, ``tests/test_modsets_apply.py`` and
``tests/test_modsets_transfer.py``; this file only pins the RPC contract.
"""

import json
from pathlib import Path

import pytest
import respx

from conftest import invoke_rpc
from fixture_types import InstallCommit
from fixtures_launch import LaunchableGameDir
from sptmod.core import game_dir, services
from sptmod.core.game_dir import GameProcess
from sptmod.core.repo import Repo
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH
from sptmod.server import schemas


def _pong(router: respx.MockRouter) -> None:
    router.get(PING_PATH).respond(200, json={"Response": "Pong!"})


def _login(router: respx.MockRouter) -> None:
    router.put(LOGIN_PATH).respond(200, json={"Response": True})


def _no_game_processes() -> list[GameProcess]:
    """Stand in for ``game_dir.game_processes`` when nothing runs."""
    return []


# --- one round trip per new function ----------------------------------------


def test_list_mod_sets_rpc_wraps_the_overview_of_an_untouched_directory(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: the response would be None instead
    of an overview with empty sets and bindings."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()

    exit_code, lines = invoke_rpc(
        "list_mod_sets", {"path": str(game_dir_path), "store_path": str(mod_set_store)}
    )

    assert exit_code == 0
    assert lines == [{"event": "result", "data": {"sets": [], "bindings": []}}]


def test_save_mod_set_rpc_stores_the_given_entries(tmp_path: Path, mod_set_store: Path) -> None:
    """If the entries were dropped instead of converted, this fails: the
    result's entries list would be empty instead of holding SAIN."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()

    exit_code, lines = invoke_rpc(
        "save_mod_set",
        {
            "path": str(game_dir_path),
            "name": "Fika night",
            "entries": [{"forge_id": "1234", "name": "SAIN", "version": "3.2.1", "enabled": True}],
            "store_path": str(mod_set_store),
        },
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "Fika night"
    assert data["entries"] == [{"forge_id": "1234", "name": "SAIN", "version": "3.2.1", "enabled": True}]


def test_save_mod_set_rpc_without_entries_captures_the_installed_mods(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If a request without 'entries' were rejected or defaulted to an empty
    list, this fails: the response's entries would be empty instead of
    holding the one installed mod."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="installed")

    exit_code, lines = invoke_rpc(
        "save_mod_set",
        {"path": str(clean_repo.path), "name": "Current mods", "store_path": str(mod_set_store)},
    )

    assert exit_code == 0
    assert [entry["name"] for entry in lines[0]["data"]["entries"]] == ["TargetMod"]


def test_delete_mod_set_rpc_removes_it_from_a_later_list(tmp_path: Path, mod_set_store: Path) -> None:
    """If the body is `pass`, this fails: the later list_mod_sets call would
    still report the deleted set."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()
    invoke_rpc(
        "save_mod_set",
        {"path": str(game_dir_path), "name": "Fika night", "entries": [], "store_path": str(mod_set_store)},
    )

    exit_code, lines = invoke_rpc(
        "delete_mod_set", {"path": str(game_dir_path), "name": "Fika night", "store_path": str(mod_set_store)}
    )

    assert exit_code == 0
    assert lines[0]["data"]["sets"] == []


def test_rename_mod_set_rpc_changes_the_stored_name(tmp_path: Path, mod_set_store: Path) -> None:
    """If the body is `pass`, this fails: the response name would still be
    'Fika night' instead of 'Squad night'."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()
    invoke_rpc(
        "save_mod_set",
        {"path": str(game_dir_path), "name": "Fika night", "entries": [], "store_path": str(mod_set_store)},
    )

    exit_code, lines = invoke_rpc(
        "rename_mod_set",
        {
            "path": str(game_dir_path),
            "name": "Fika night",
            "new_name": "Squad night",
            "store_path": str(mod_set_store),
        },
    )

    assert exit_code == 0
    assert lines[0]["data"]["name"] == "Squad night"


def test_export_mod_set_rpc_returns_content_and_a_share_code(tmp_path: Path, mod_set_store: Path) -> None:
    """If the export were empty, this fails: content would not round-trip
    into the 'mod_set' JSON shape via json.loads."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()
    invoke_rpc(
        "save_mod_set",
        {"path": str(game_dir_path), "name": "Fika night", "entries": [], "store_path": str(mod_set_store)},
    )

    exit_code, lines = invoke_rpc(
        "export_mod_set", {"path": str(game_dir_path), "name": "Fika night", "store_path": str(mod_set_store)}
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "Fika night"
    assert json.loads(data["content"])["mod_set"]["name"] == "Fika night"
    assert data["code"].startswith("SPTMODSET1-")


def test_import_mod_set_rpc_stores_the_given_content(tmp_path: Path, mod_set_store: Path) -> None:
    """If content were ignored, this fails: the later list_mod_sets call
    would not report the imported set."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()
    content = json.dumps({"mod_set": {"name": "Fika night", "entries": []}})

    exit_code, lines = invoke_rpc(
        "import_mod_set", {"path": str(game_dir_path), "content": content, "store_path": str(mod_set_store)}
    )

    assert exit_code == 0
    assert lines[0]["data"]["name"] == "Fika night"


def test_bind_profile_set_rpc_records_the_binding(tmp_path: Path, mod_set_store: Path) -> None:
    """If the body is `pass`, this fails: the response's bindings list would
    be empty instead of naming profile-1."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()
    invoke_rpc(
        "save_mod_set",
        {"path": str(game_dir_path), "name": "Fika night", "entries": [], "store_path": str(mod_set_store)},
    )

    exit_code, lines = invoke_rpc(
        "bind_profile_set",
        {
            "path": str(game_dir_path),
            "profile_id": "profile-1",
            "set_name": "Fika night",
            "store_path": str(mod_set_store),
        },
    )

    assert exit_code == 0
    assert lines[0]["data"]["bindings"] == [{"profile_id": "profile-1", "set_name": "Fika night"}]


def test_plan_mod_set_rpc_reports_a_mod_ready_to_enable(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If plan_mod_set were not called, this fails: the response's enable
    list would be empty instead of holding TargetMod."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="disabled")
    invoke_rpc(
        "save_mod_set",
        {
            "path": str(clean_repo.path),
            "name": "Fika night",
            "entries": [{"forge_id": "", "name": "TargetMod", "version": "1.0.0", "enabled": True}],
            "store_path": str(mod_set_store),
        },
    )

    exit_code, lines = invoke_rpc(
        "plan_mod_set", {"path": str(clean_repo.path), "name": "Fika night", "store_path": str(mod_set_store)}
    )

    assert exit_code == 0
    assert [change["name"] for change in lines[0]["data"]["enable"]] == ["TargetMod"]


def test_apply_mod_set_rpc_echoes_operation_id_and_emits_progress(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If operation_id were not echoed, this fails: result['operation_id']
    would differ from the caller-supplied 'apply-op-id'."""
    install_commit(clean_repo, "TargetMod", "1.0.0", state="installed")
    services.disable_mod(clean_repo.path, "TargetMod")
    invoke_rpc(
        "save_mod_set",
        {
            "path": str(clean_repo.path),
            "name": "Fika night",
            "entries": [{"forge_id": "", "name": "TargetMod", "version": "1.0.0", "enabled": True}],
            "store_path": str(mod_set_store),
        },
    )

    exit_code, lines = invoke_rpc(
        "apply_mod_set",
        {
            "path": str(clean_repo.path),
            "name": "Fika night",
            "store_path": str(mod_set_store),
            "operation_id": "apply-op-id",
        },
    )

    assert exit_code == 0
    events = [line["event"] for line in lines]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
    assert len(lines) > 1
    assert all(line["operation_id"] == "apply-op-id" for line in lines[:-1])
    result = lines[-1]["data"]
    assert result["operation_id"] == "apply-op-id"
    assert result["enabled"] == ["TargetMod"]


# --- error envelopes ---------------------------------------------------------


def test_plan_mod_set_rpc_maps_an_unknown_name_to_mod_set_not_found(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the error mapping were missing, this fails: code would default to
    SPTMOD_ERROR/INTERNAL_ERROR instead of MOD_SET_NOT_FOUND."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()

    exit_code, lines = invoke_rpc(
        "plan_mod_set", {"path": str(game_dir_path), "name": "No such set", "store_path": str(mod_set_store)}
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "MOD_SET_NOT_FOUND"


def test_rename_mod_set_rpc_maps_a_taken_name_to_mod_set_exists(tmp_path: Path, mod_set_store: Path) -> None:
    """If the error mapping were missing, this fails: code would default to
    SPTMOD_ERROR/INTERNAL_ERROR instead of MOD_SET_EXISTS."""
    game_dir_path = tmp_path / "game"
    game_dir_path.mkdir()
    invoke_rpc(
        "save_mod_set",
        {"path": str(game_dir_path), "name": "Fika night", "entries": [], "store_path": str(mod_set_store)},
    )
    invoke_rpc(
        "save_mod_set",
        {"path": str(game_dir_path), "name": "Squad night", "entries": [], "store_path": str(mod_set_store)},
    )

    exit_code, lines = invoke_rpc(
        "rename_mod_set",
        {
            "path": str(game_dir_path),
            "name": "Fika night",
            "new_name": "Squad night",
            "store_path": str(mod_set_store),
        },
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "MOD_SET_EXISTS"


def test_apply_mod_set_rpc_maps_missing_mods_to_mod_set_incomplete_with_details(
    clean_repo: Repo, mod_set_store: Path
) -> None:
    """If error_details(ModSetIncompleteError) were missing, this fails:
    details.missing would be empty instead of naming GhostMod."""
    invoke_rpc(
        "save_mod_set",
        {
            "path": str(clean_repo.path),
            "name": "Fika night",
            "entries": [{"forge_id": "", "name": "GhostMod", "version": "1.0.0", "enabled": True}],
            "store_path": str(mod_set_store),
        },
    )

    exit_code, lines = invoke_rpc(
        "apply_mod_set", {"path": str(clean_repo.path), "name": "Fika night", "store_path": str(mod_set_store)}
    )

    assert exit_code == 1
    data = lines[-1]["data"]
    assert data["code"] == "MOD_SET_INCOMPLETE"
    assert data["details"]["missing"] == ["GhostMod"]
    assert data["details"]["name"] == "Fika night"


# --- ErrorDetails.missing default -------------------------------------------


def test_error_details_missing_defaults_to_an_empty_list() -> None:
    """If the field lost its default, this fails: constructing ErrorDetails()
    without arguments would raise a pydantic ValidationError instead of
    yielding missing == []."""
    assert not schemas.ErrorDetails().missing


# --- launch_profile accepting mod_set and store_path ------------------------


def test_launch_profile_rpc_accepts_mod_set_and_store_path(
    fake_launchable_dir: LaunchableGameDir,
    mod_set_store: Path,
    fika_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If mod_set/store_path were not accepted request fields, this fails:
    the request would be rejected with a validation error (exit code 2)
    instead of completing the launch."""
    monkeypatch.setattr(game_dir, "game_processes", _no_game_processes)
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _pong(router)
        _login(router)

        exit_code, lines = invoke_rpc(
            "launch_profile",
            {
                "path": str(d.root),
                "profile_id": d.profile_id,
                "mod_set": "skip",
                "store_path": str(mod_set_store),
                "fika_path": str(fika_store),
            },
        )

    assert exit_code == 0
    steps = [line["step"] for line in lines[:-1]]
    assert "modset" not in steps
    assert lines[-1]["data"]["profile_id"] == d.profile_id
