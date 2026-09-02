"""Error-mapping tests of the FastAPI adapter.

Each core exception must turn into the structured ``{error, code, details}``
body at the status code fixed by ``sptmod.server.app.ERROR_MAPPING``. Covers
the exceptions named in the task: ``NotAGameDirError`` (400),
``ModNotFoundError`` (404), ``ConflictError`` (409, with populated
``details.conflicts``), ``AmbiguousModError`` (409, with ``details.name`` and
``details.candidates``), ``NotImplementedYetError`` (501, forced via a
synthetic raiser since every service function is now implemented) and
``GitError`` (500).

The final test covers ``ProfileResidueError`` (409, with structured
``details.residues`` listing which profiles/categories/IDs are blocking a
``remove_mod`` call).
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sptmod.core import game_dir, services
from sptmod.core.errors import NotImplementedYetError
from sptmod.core.repo import Repo
from sptmod.server.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


@pytest.fixture
def game_dir_path(fake_game_dir: Path) -> Path:
    response = client.post("/api/init_game_dir", json={"path": str(fake_game_dir)})
    assert response.status_code == 200
    return fake_game_dir


def test_not_a_game_dir_error_returns_400(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-game-dir"
    empty.mkdir()

    response = client.post("/api/init_game_dir", json={"path": str(empty)})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "NOT_A_GAME_DIR"
    assert body["error"] != ""


def test_mod_not_found_error_returns_404(game_dir_path: Path) -> None:
    response = client.post(
        "/api/mod_files", json={"path": str(game_dir_path), "name": "Ghost"}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "MOD_NOT_FOUND"


def test_conflict_error_returns_409_with_populated_conflicts(
    game_dir_path: Path, install_commit, make_archive
) -> None:
    repo = Repo(game_dir_path)
    install_commit(
        repo,
        "ModA",
        version="1.0.0",
        files={"BepInEx/plugins/ModA.dll": "original-content"},
    )
    archive = make_archive({"BepInEx/plugins/ModA.dll": "new-content"}, "zip")

    response = client.post(
        "/api/install_from_archive",
        json={"path": str(game_dir_path), "archive": str(archive)},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CONFLICT"
    conflicts = body["details"]["conflicts"]
    assert conflicts != []
    assert any(finding["mod"] == "ModA" for finding in conflicts)
    # The install must not have proceeded: the original file is untouched.
    assert (
        game_dir_path / "BepInEx/plugins/ModA.dll"
    ).read_text() == "original-content"


def test_ambiguous_mod_error_returns_409_with_name_and_candidates(
    game_dir_path: Path, install_commit
) -> None:
    repo = Repo(game_dir_path)
    install_commit(repo, "CoolMod", minute=1)
    install_commit(repo, "coolmod", minute=2)

    response = client.post(
        "/api/history", json={"path": str(game_dir_path), "name": "COOLMOD"}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "AMBIGUOUS_MOD"
    assert body["details"]["name"] == "COOLMOD"
    assert set(body["details"]["candidates"]) == {"CoolMod", "coolmod"}


def test_not_implemented_yet_error_returns_501(
    game_dir_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every service function is implemented now, so no real call raises
    # NotImplementedYetError on its own; force one to keep the 501 mapping
    # of that exception type covered.
    def _raise(*args: object, **kwargs: object) -> None:
        raise NotImplementedYetError("synthetic: forced for mapping coverage")

    monkeypatch.setattr(services, "import_list", _raise)
    list_file = tmp_path / "mods.txt"
    list_file.write_text("SomeMod 1.0.0\n", encoding="utf-8")

    response = client.post(
        "/api/import_list",
        json={"path": str(game_dir_path), "list_file": str(list_file)},
    )

    assert response.status_code == 501
    assert response.json()["code"] == "NOT_IMPLEMENTED_YET"


def test_git_error_returns_500_for_a_snapshot_with_nothing_to_commit(
    game_dir_path: Path,
) -> None:
    # Straight after init the tree is clean: no profile/config changes exist
    # for snapshot() to commit, which is specified to raise GitError.
    response = client.post("/api/snapshot", json={"path": str(game_dir_path)})

    assert response.status_code == 500
    assert response.json()["code"] == "GIT_ERROR"


# --------------------------------------------------------------------------
# ProfileResidueError: structured details.residues payload.
# --------------------------------------------------------------------------


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


def test_remove_mod_profile_residue_error_returns_structured_residue_details(
    game_dir_path: Path, install_commit
) -> None:
    repo = Repo(game_dir_path)
    item_id = "5c0d1b8b86f7744bb2683cf1"
    install_commit(
        repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps(
                {item_id: {"_id": item_id, "_name": "item"}}
            )
        },
    )
    profile_path = repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(json.dumps(_profile_with_item(item_id)), encoding="utf-8")
    repo.commit(
        "chore: profile snapshot", paths=[Path("SPT/user/profiles/abc.json")]
    )

    response = client.post(
        "/api/remove_mod", json={"path": str(game_dir_path), "name": "ServerMod"}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "PROFILE_RESIDUE"
    assert body["details"]["residues"] == [
        {"profile": "abc", "category": "item", "ids": [item_id], "count": 1}
    ]
