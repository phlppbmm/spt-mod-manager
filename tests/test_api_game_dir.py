"""Happy-path tests of the game-directory endpoints of the FastAPI adapter.

Drives a fake game directory purely through the HTTP API (init, install,
list, mod_files, history, status, snapshot, export/import, disable/enable,
remove, adopt, check_profiles), asserting the JSON contract of each endpoint
matches what the underlying service call actually produces.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sptmod.core import game_dir
from sptmod.server.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


@pytest.fixture
def game_dir_path(fake_game_dir: Path) -> Path:
    """``fake_game_dir`` turned into a tracked repository through the API."""
    response = client.post("/api/init_game_dir", json={"path": str(fake_game_dir)})
    assert response.status_code == 200
    return fake_game_dir


@pytest.fixture
def installed_mod(game_dir_path: Path, make_archive) -> dict:
    """``CoolMod`` 1.2.3 installed through the API; returns the response body."""
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {
                    "name": "CoolMod",
                    "version": "1.2.3",
                    "author": "Acme",
                    "sptVersion": "~4.1.0",
                }
            )
        },
        "zip",
    )
    response = client.post(
        "/api/install_from_archive",
        json={"path": str(game_dir_path), "archive": str(archive)},
    )
    assert response.status_code == 200
    return response.json()


# --------------------------------------------------------------------------
# init_game_dir
# --------------------------------------------------------------------------


def test_init_game_dir_reports_spt_version_and_no_pre_existing_mods(
    fake_game_dir: Path,
) -> None:
    response = client.post("/api/init_game_dir", json={"path": str(fake_game_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["game_dir"] == str(fake_game_dir)
    assert body["spt_version"] == "4.1.0"
    assert body["pre_existing_mods"] == []
    assert body["adopted"] == []
    assert body["commit"] != ""


# --------------------------------------------------------------------------
# install_from_archive
# --------------------------------------------------------------------------


def test_install_from_archive_returns_the_installed_mod_and_an_operation_id(
    installed_mod: dict,
) -> None:
    assert installed_mod["mod"]["name"] == "CoolMod"
    assert installed_mod["mod"]["version"] == "1.2.3"
    assert installed_mod["mod"]["mod_type"] == "server"
    assert installed_mod["mod"]["author"] == "Acme"
    assert installed_mod["mod"]["state"] == "installed"
    assert installed_mod["files"] == ["SPT/user/mods/CoolMod/package.json"]
    assert installed_mod["warnings"] == []
    assert installed_mod["operation_id"] != ""


def test_install_from_archive_echoes_back_a_caller_supplied_operation_id(
    game_dir_path: Path, make_archive
) -> None:
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "Acme"}
            )
        },
        "zip",
    )

    response = client.post(
        "/api/install_from_archive",
        json={
            "path": str(game_dir_path),
            "archive": str(archive),
            "operation_id": "caller-chosen-id",
        },
    )

    assert response.status_code == 200
    assert response.json()["operation_id"] == "caller-chosen-id"


# --------------------------------------------------------------------------
# list_mods / mod_files / history
# --------------------------------------------------------------------------


def test_list_mods_returns_the_installed_mod(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post("/api/list_mods", json={"path": str(game_dir_path)})

    assert response.status_code == 200
    assert [mod["name"] for mod in response.json()] == ["CoolMod"]


def test_mod_files_returns_the_files_of_the_install_commit(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post(
        "/api/mod_files", json={"path": str(game_dir_path), "name": "CoolMod"}
    )

    assert response.status_code == 200
    assert response.json() == [
        {"path": "SPT/user/mods/CoolMod/package.json", "change": "added"}
    ]


def test_history_without_name_returns_init_and_install_commits_newest_first(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post("/api/history", json={"path": str(game_dir_path)})

    assert response.status_code == 200
    subjects = [commit["subject"] for commit in response.json()]
    assert subjects == ["mod: CoolMod 1.2.3", "chore: clean SPT 4.1.0"]


def test_history_with_name_matches_case_insensitively(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post(
        "/api/history", json={"path": str(game_dir_path), "name": "coolmod"}
    )

    assert response.status_code == 200
    subjects = [commit["subject"] for commit in response.json()]
    assert subjects == ["mod: CoolMod 1.2.3"]


# --------------------------------------------------------------------------
# disable_mod / enable_mod / remove_mod / check_profiles
# --------------------------------------------------------------------------


def test_disable_mod_reverts_the_install_commit_and_removes_it_from_list_mods(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post(
        "/api/disable_mod", json={"path": str(game_dir_path), "name": "CoolMod"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "CoolMod"
    assert body["state"] == "disabled"
    assert body["commit"] != ""
    assert body["files"] == ["SPT/user/mods/CoolMod/package.json"]

    listed = client.post("/api/list_mods", json={"path": str(game_dir_path)})
    assert listed.json() == []


def test_enable_mod_restores_a_disabled_mod(
    game_dir_path: Path, installed_mod: dict
) -> None:
    client.post(
        "/api/disable_mod", json={"path": str(game_dir_path), "name": "CoolMod"}
    )

    response = client.post(
        "/api/enable_mod", json={"path": str(game_dir_path), "name": "CoolMod"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "CoolMod"
    assert body["state"] == "enabled"
    assert body["commit"] != ""

    listed = client.post("/api/list_mods", json={"path": str(game_dir_path)})
    assert [mod["name"] for mod in listed.json()] == ["CoolMod"]


def test_remove_mod_deletes_the_files_and_reports_them(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post(
        "/api/remove_mod", json={"path": str(game_dir_path), "name": "CoolMod"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "CoolMod"
    assert body["commit"] != ""
    assert body["files"] == ["SPT/user/mods/CoolMod/package.json"]
    assert not (game_dir_path / "SPT/user/mods/CoolMod/package.json").exists()


def test_check_profiles_reports_no_residue_for_a_db_free_mod(
    game_dir_path: Path, installed_mod: dict
) -> None:
    response = client.post("/api/check_profiles", json={"path": str(game_dir_path)})

    assert response.status_code == 200
    assert response.json() == {"residues": {}, "warnings": []}


# --------------------------------------------------------------------------
# status / snapshot
# --------------------------------------------------------------------------


def test_status_reports_a_modified_profile_and_snapshot_commits_it(
    game_dir_path: Path,
) -> None:
    profile_path = game_dir_path / "SPT/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    before = client.post("/api/status", json={"path": str(game_dir_path)})
    assert before.status_code == 200
    assert before.json()["modified_profiles"] == ["SPT/user/profiles/abc.json"]

    snapshot = client.post(
        "/api/snapshot",
        json={"path": str(game_dir_path), "message": "test snapshot"},
    )
    assert snapshot.status_code == 200
    snapshot_body = snapshot.json()
    assert snapshot_body["message"] == "test snapshot"
    assert snapshot_body["files"] == ["SPT/user/profiles/abc.json"]

    after = client.post("/api/status", json={"path": str(game_dir_path)})
    assert after.json()["modified_profiles"] == []


# --------------------------------------------------------------------------
# adopt_mod
# --------------------------------------------------------------------------


def test_adopt_mod_commits_an_untracked_mod_folder(game_dir_path: Path) -> None:
    mod_dir = game_dir_path / "SPT/user/mods/ManualMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        json.dumps({"name": "ManualMod", "version": "0.1.0", "author": "me"}),
        encoding="utf-8",
    )

    response = client.post(
        "/api/adopt_mod",
        json={"path": str(game_dir_path), "target": "SPT/user/mods/ManualMod"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mod"]["name"] == "ManualMod"
    assert body["mod"]["version"] == "0.1.0"
    assert body["mod"]["state"] == "installed"
    assert body["files"] == ["SPT/user/mods/ManualMod/package.json"]


# --------------------------------------------------------------------------
# export_mods / import_list
# --------------------------------------------------------------------------


def test_export_mods_text_format_lists_name_and_version(
    game_dir_path: Path, installed_mod: dict, tmp_path: Path
) -> None:
    response = client.post(
        "/api/export_mods",
        json={
            "path": str(game_dir_path),
            "format": "text",
            "db_path": str(tmp_path / "catalog.db"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"content": "CoolMod 1.2.3\n"}


def test_export_mods_json_format_lists_name_version_type_and_link(
    game_dir_path: Path, installed_mod: dict, tmp_path: Path
) -> None:
    response = client.post(
        "/api/export_mods",
        json={
            "path": str(game_dir_path),
            "format": "json",
            "db_path": str(tmp_path / "catalog.db"),
        },
    )

    assert response.status_code == 200
    payload = json.loads(response.json()["content"])
    assert payload == {
        "mods": [
            {"name": "CoolMod", "version": "1.2.3", "type": "server", "link": ""}
        ]
    }


def test_import_list_reports_no_diff_for_an_export_of_the_same_state(
    game_dir_path: Path, installed_mod: dict, tmp_path: Path
) -> None:
    exported = client.post(
        "/api/export_mods",
        json={
            "path": str(game_dir_path),
            "format": "text",
            "db_path": str(tmp_path / "catalog.db"),
        },
    )
    list_file = tmp_path / "mods.txt"
    list_file.write_text(exported.json()["content"], encoding="utf-8")

    response = client.post(
        "/api/import_list",
        json={"path": str(game_dir_path), "list_file": str(list_file)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "missing": [],
        "different": [],
        "extra": [],
        "installed": [],
    }


def test_import_list_reports_a_mod_only_the_list_has_as_missing(
    game_dir_path: Path, tmp_path: Path
) -> None:
    list_file = tmp_path / "mods.txt"
    list_file.write_text("GhostMod 2.0.0\n", encoding="utf-8")

    response = client.post(
        "/api/import_list",
        json={"path": str(game_dir_path), "list_file": str(list_file)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["missing"] == [
        {
            "name": "GhostMod",
            "wanted_version": "2.0.0",
            "installed_version": "",
            "link": "",
        }
    ]
    assert body["different"] == []
    assert body["extra"] == []
