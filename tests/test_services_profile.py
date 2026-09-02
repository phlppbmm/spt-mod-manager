"""Integration tests for profile residue handling wired into the services
layer and the CLI: ``remove_mod`` aborting on residue, ``--force`` reverting
without touching profiles, a mod without ``db/`` proceeding, and the CLI
``check``/``remove`` commands.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sptmod.cli import app
from sptmod.core import game_dir, services
from sptmod.core.errors import ProfileResidueError
from sptmod.core.repo import Repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(path)
    repo.commit(
        "chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None
    )
    return repo


def _profile_with_item(item_id: str) -> dict:
    return {
        "characters": {
            "pmc": {
                "Inventory": {"items": [{"_id": "i1", "tpl": item_id}]},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }


def _commit_profile_with_item(repo: Repo, item_id: str) -> Path:
    """Write and commit a profile that references ``item_id``.

    Profiles are tracked content, so the change must be committed to keep
    the tree clean for the subsequent ``remove_mod``/CLI call.
    """
    profile_path = repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps(_profile_with_item(item_id)), encoding="utf-8"
    )
    repo.commit(
        "chore: profile snapshot", paths=[Path("SPT/user/profiles/abc.json")]
    )
    return profile_path


def _install_server_mod_with_item(
    clean_repo: Repo, install_commit, item_id: str
) -> None:
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps(
                {item_id: {"_id": item_id, "_name": "item"}}
            )
        },
    )


# --------------------------------------------------------------------------
# services.remove_mod
# --------------------------------------------------------------------------


def test_remove_mod_raises_profile_residue_error_when_a_profile_still_references_it(
    clean_repo: Repo, install_commit
) -> None:
    item_id = "5c0d1b8b86f7744bb2683cf1"
    _install_server_mod_with_item(clean_repo, install_commit, item_id)
    _commit_profile_with_item(clean_repo, item_id)

    with pytest.raises(ProfileResidueError) as exc_info:
        services.remove_mod(clean_repo.path, "ServerMod")

    message = str(exc_info.value)
    assert "abc" in message
    assert "item" in message


def test_remove_mod_leaves_the_mod_installed_when_it_raises_profile_residue_error(
    clean_repo: Repo, install_commit
) -> None:
    item_id = "5c0d1b8b86f7744bb2683cf2"
    _install_server_mod_with_item(clean_repo, install_commit, item_id)
    _commit_profile_with_item(clean_repo, item_id)

    with pytest.raises(ProfileResidueError):
        services.remove_mod(clean_repo.path, "ServerMod")

    active_names = {mod.name for mod in services.list_mods(clean_repo.path)}
    assert "ServerMod" in active_names


def test_remove_mod_force_reverts_and_leaves_the_profile_file_byte_identical(
    clean_repo: Repo, install_commit
) -> None:
    item_id = "5c0d1b8b86f7744bb2683cf3"
    _install_server_mod_with_item(clean_repo, install_commit, item_id)
    profile_path = _commit_profile_with_item(clean_repo, item_id)
    before = profile_path.read_bytes()

    result = services.remove_mod(clean_repo.path, "ServerMod", force=True)

    assert result.name == "ServerMod"
    assert profile_path.read_bytes() == before
    assert not (
        clean_repo.path / "SPT/user/mods/ServerMod/db/templates/items.json"
    ).exists()


def test_remove_mod_without_db_dir_proceeds_without_a_residue_error(
    clean_repo: Repo, install_commit
) -> None:
    install_commit(
        clean_repo, "ClientMod", files={"BepInEx/plugins/ClientMod.dll": "content"}
    )

    result = services.remove_mod(clean_repo.path, "ClientMod")

    assert result.name == "ClientMod"


def test_remove_mod_result_warnings_include_broken_db_json_entries(
    clean_repo: Repo, install_commit
) -> None:
    trader_id = "5ac3b934136fb568fa78b1e2"
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": "{not valid json",
            f"SPT/user/mods/ServerMod/db/traders/{trader_id}/base.json": json.dumps(
                {"_id": trader_id, "nickname": "Test Trader"}
            ),
        },
    )

    result = services.remove_mod(clean_repo.path, "ServerMod")

    assert result.name == "ServerMod"
    assert len(result.warnings) == 1
    assert "items.json" in result.warnings[0]


# --------------------------------------------------------------------------
# CLI: check
# --------------------------------------------------------------------------


def test_cli_check_exits_zero_when_no_profile_has_residue(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0


def test_cli_check_exits_zero_and_reports_residues_when_found(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf4"
    _install_server_mod_with_item(repo, install_commit, item_id)
    _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["check"])

    # check is a report, not an error: it exits 0 even when residues are found.
    assert result.exit_code == 0
    assert "ServerMod" in result.stdout
    assert "abc" in result.stdout


# --------------------------------------------------------------------------
# CLI: remove
# --------------------------------------------------------------------------


def test_cli_remove_maps_profile_residue_error_to_exit_code_8(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf5"
    _install_server_mod_with_item(repo, install_commit, item_id)
    _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod"])

    assert result.exit_code == 8
    assert result.stderr.startswith("error:")


def test_cli_remove_with_force_succeeds_despite_profile_residue(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf6"
    _install_server_mod_with_item(repo, install_commit, item_id)
    _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--force"])

    assert result.exit_code == 0
    assert "ServerMod" in result.stdout


def test_cli_remove_prints_a_warning_line_for_broken_db_json(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    trader_id = "5ac3b934136fb568fa78b1e2"
    install_commit(
        repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": "{not valid json",
            f"SPT/user/mods/ServerMod/db/traders/{trader_id}/base.json": json.dumps(
                {"_id": trader_id, "nickname": "Test Trader"}
            ),
        },
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod"])

    assert result.exit_code == 0
    assert "items.json" in result.stdout + result.stderr
