"""Integration tests for profile residue handling wired into the services
layer and the CLI: ``remove_mod`` aborting on residue, ``--force`` reverting
without touching profiles, a mod without ``db/`` proceeding, and the CLI
``check``/``remove`` commands.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sptmod.cli import app
from sptmod.core import game_dir, services
from sptmod.core.errors import ProfileResidueError
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(detect_layout(path))
    repo.commit(
        "chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None
    )
    return repo


def _profile_with_item(item_id: str) -> dict:
    return {
        "characters": {
            "pmc": {
                "Inventory": {
                    "items": [
                        {
                            "_id": "i1",
                            "_tpl": item_id,
                            "parentId": "5c0d1b8b86f7744bb2683c99",
                            "slotId": "hideout",
                        }
                    ]
                },
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
# services.list_profiles
# --------------------------------------------------------------------------


def _write_profile_file(profiles_dir: Path, name: str, document: dict) -> Path:
    target = profiles_dir / name
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def _character(nickname: str, level: int, side: str) -> dict:
    return {
        "characters": {
            "pmc": {
                "Info": {
                    "Nickname": nickname,
                    "Level": level,
                    "Side": side,
                }
            }
        }
    }


def test_list_profiles_describes_the_pmc_character_of_a_profile_file(
    fake_game_dir: Path,
) -> None:
    profiles_dir = fake_game_dir / "SPT" / "user" / "profiles"
    target = _write_profile_file(
        profiles_dir, "abc.json", _character("Nikita", 42, "Usec")
    )

    summaries = services.list_profiles(fake_game_dir)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.file == "abc.json"
    assert summary.profile_id == "abc"
    assert summary.nickname == "Nikita"
    assert summary.level == 42
    assert summary.side == "Usec"
    assert summary.readable is True
    assert summary.error == ""
    assert datetime.fromisoformat(summary.modified) == datetime.fromtimestamp(
        target.stat().st_mtime, UTC
    )


def test_list_profiles_sorts_the_profiles_by_file_name(fake_game_dir: Path) -> None:
    profiles_dir = fake_game_dir / "SPT" / "user" / "profiles"
    _write_profile_file(profiles_dir, "zulu.json", _character("Zulu", 3, "Bear"))
    _write_profile_file(profiles_dir, "alpha.json", _character("Alpha", 1, "Usec"))

    summaries = services.list_profiles(fake_game_dir)

    assert [summary.file for summary in summaries] == [
        "abc.json",
        "alpha.json",
        "zulu.json",
    ]


def test_list_profiles_defaults_the_character_data_a_profile_does_not_carry(
    fake_game_dir: Path,
) -> None:
    profiles_dir = fake_game_dir / "SPT" / "user" / "profiles"
    _write_profile_file(profiles_dir, "abc.json", {"characters": {"pmc": {"Info": {}}}})

    summary = services.list_profiles(fake_game_dir)[0]

    assert summary.readable is True
    assert summary.error == ""
    assert summary.nickname == ""
    assert summary.level == 0
    assert summary.side == ""


def test_list_profiles_reports_a_broken_file_as_unreadable_with_a_plain_reason(
    fake_game_dir: Path,
) -> None:
    profiles_dir = fake_game_dir / "SPT" / "user" / "profiles"
    (profiles_dir / "abc.json").write_text("{not json", encoding="utf-8")

    summary = services.list_profiles(fake_game_dir)[0]

    assert summary.file == "abc.json"
    assert summary.profile_id == "abc"
    assert summary.readable is False
    assert summary.error == "This file could not be read as a player profile."
    assert summary.nickname == ""
    assert summary.level == 0
    assert summary.side == ""
    assert summary.modified != ""


def test_list_profiles_returns_nothing_when_there_is_no_profile_file(
    fake_game_dir: Path,
) -> None:
    (fake_game_dir / "SPT" / "user" / "profiles" / "abc.json").unlink()

    assert services.list_profiles(fake_game_dir) == []


def test_list_profiles_reads_the_profiles_of_the_spt_runtime_layout(
    fake_game_dir_runtime: Path,
) -> None:
    profiles_dir = fake_game_dir_runtime / "SPT_Runtime" / "user" / "profiles"
    _write_profile_file(profiles_dir, "abc.json", _character("Runtime", 7, "Bear"))

    summaries = services.list_profiles(fake_game_dir_runtime)

    assert [summary.file for summary in summaries] == ["abc.json"]
    assert summaries[0].nickname == "Runtime"
    assert summaries[0].side == "Bear"


# --------------------------------------------------------------------------
# services.check_profiles
# --------------------------------------------------------------------------


def test_check_profiles_reports_scav_and_top_level_residues_with_character_and_path(
    clean_repo: Repo, install_commit
) -> None:
    item_id = "5c0d1b8b86f7744bb2683cf9"
    trader_id = "5ac3b934136fb568fa78b1e9"
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps(
                {item_id: {"_id": item_id, "_name": "item"}}
            ),
            f"SPT/user/mods/ServerMod/db/traders/{trader_id}/base.json": json.dumps(
                {"_id": trader_id, "nickname": "Trader"}
            ),
        },
    )

    profiles_dir = clean_repo.path / "SPT" / "user" / "profiles"
    profile = {
        "characters": {
            "pmc": {"Inventory": {"items": []}, "TradersInfo": {}, "Quests": []},
            "scav": {
                "Inventory": {
                    "items": [{"_id": "i1", "_tpl": item_id, "slotId": "main"}]
                },
                "TradersInfo": {},
                "Quests": [],
            },
        },
        "insurance": [{"traderId": trader_id, "items": []}],
    }
    (profiles_dir / "abc.json").write_text(json.dumps(profile), encoding="utf-8")

    result = services.check_profiles(clean_repo.path)

    residues = result.residues["ServerMod"]
    scav_item = next(
        r for r in residues if r.category == "item" and r.character == "scav"
    )
    assert scav_item.ids == [item_id]
    assert scav_item.path == "/characters/scav/Inventory/items"

    insurance = next(r for r in residues if r.category == "insurance")
    assert insurance.character == ""
    assert insurance.path == "/insurance"
    assert insurance.ids == [trader_id]


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


def test_cli_remove_clean_profiles_flag_actually_cleans_the_profile(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf7"
    _install_server_mod_with_item(repo, install_commit, item_id)
    profile_path = _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--clean-profiles"])

    assert result.exit_code == 0
    cleaned = json.loads(profile_path.read_text(encoding="utf-8"))
    assert cleaned["characters"]["pmc"]["Inventory"]["items"] == []


def test_cli_remove_clean_profiles_output_mentions_cleaned_profiles(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    repo = _clean_repo(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf9"
    _install_server_mod_with_item(repo, install_commit, item_id)
    _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--clean-profiles"])

    assert result.exit_code == 0
    assert "abc" in result.stdout


def test_cli_remove_clean_profiles_reports_no_cleaning_needed_and_untouched_count(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    # item_id sits only in raw text (e.g. a note field), never in a
    # structured container: sptmod can only find it via the string
    # fallback, so nothing is removed but something stays untouched.
    item_id = "5c0d1b8b86f7744bb2683cfb"
    repo = _clean_repo(fake_game_dir)
    _install_server_mod_with_item(repo, install_commit, item_id)
    profile = {
        "characters": {
            "pmc": {
                "Inventory": {"items": []},
                "TradersInfo": {},
                "Quests": [],
            }
        },
        "info": {"note": item_id},
    }
    profile_path = repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    repo.commit(
        "chore: profile snapshot", paths=[Path("SPT/user/profiles/abc.json")]
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--clean-profiles"])

    assert result.exit_code == 0
    assert "no profile needed cleaning" in result.stdout
    assert "removed 0 profile entries" in result.stdout
    assert "1 entries stayed" in result.stdout


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
