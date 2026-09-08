"""In-process tests for the mod-lifecycle side of ``sptmod rpc <name>``: the
directory-browsing and init routes plus ``list_mods``, ``mod_files``,
``history``, ``disable_mod``, ``enable_mod``, ``remove_mod``,
``check_profiles``, ``adopt_mod`` and ``snapshot``. Split out of
:mod:`test_rpc`, which keeps the envelope and error-mapping cases; these
routes run against the real service functions on a temporary game directory.
"""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import invoke_rpc
from fixture_types import InstallCommit, JsonDict
from sptmod.core import services
from sptmod.core.errors import InvalidPathError
from sptmod.core.repo import Repo
from sptmod.server import rpc

# --------------------------------------------------------------------------
# Directory browsing / init: locate_directory, init_game_dir.
# --------------------------------------------------------------------------


def test_locate_directory_serialises_every_matched_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route renders the service's result list as JSON objects.

    Which directories the search finds is pinned at the service level; here
    the dispatched function is swapped for a stub returning known matches so
    that only the transport is under test.
    """
    matches = [
        services.LocatedDirectory(path=tmp_path / "games" / "SPT", is_game_dir=True),
        services.LocatedDirectory(path=tmp_path / "backup" / "SPT", is_game_dir=False),
    ]
    original = rpc.RPC_FUNCTIONS["locate_directory"]

    def locate(**_kwargs: object) -> list[services.LocatedDirectory]:
        return matches

    monkeypatch.setitem(
        rpc.RPC_FUNCTIONS, "locate_directory", dataclasses.replace(original, function=locate)
    )

    exit_code, lines = invoke_rpc("locate_directory", {"name": "SPT", "fingerprint": ["BepInEx", "cache"]})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": [
                {"path": str(tmp_path / "games" / "SPT"), "is_game_dir": True},
                {"path": str(tmp_path / "backup" / "SPT"), "is_game_dir": False},
            ],
        }
    ]


def test_init_game_dir_reports_spt_version_and_no_pre_existing_mods(
    fake_game_dir: Path,
) -> None:
    """init_game_dir detects SPT 4.1.0 and returns pre_existing_mods=[], adopted=[].

    The test directory has no mods installed, so init_game_dir reports empty
    lists and a non-empty commit hash for the initial commit.
    """
    exit_code, lines = invoke_rpc("init_game_dir", {"path": str(fake_game_dir)})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["game_dir"] == str(fake_game_dir)
    assert data["spt_version"] == "4.1.0"
    assert data["pre_existing_mods"] == []
    assert data["adopted"] == []
    assert data["commit"] != ""


# --------------------------------------------------------------------------
# Mod lifecycle: list_mods, mod_files, history, disable_mod, enable_mod,
# remove_mod, check_profiles, adopt_mod, snapshot.
# --------------------------------------------------------------------------


def test_list_mods_returns_the_installed_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """list_mods returns all installed mods with their version and state."""
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("list_mods", {"path": str(clean_repo.path)})

    assert exit_code == 0
    data = lines[0]["data"]
    assert [mod["name"] for mod in data] == ["CoolMod"]
    assert data[0]["version"] == "1.2.3"
    assert data[0]["state"] == "installed"


def test_mod_files_returns_the_files_of_the_install_commit(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """mod_files returns the files added by a mod's install commit with change
    annotations."""
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("mod_files", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    assert lines[0]["data"] == [{"path": "BepInEx/plugins/CoolMod.dll", "change": "added"}]


def test_history_without_name_returns_commits_newest_first(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """history without name filter returns all commits with subjects in order.

    After install_commit adds \"mod: CoolMod 1.2.3\", history returns both it
    and the baseline \"chore: clean SPT 4.1.0\" with newest first.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("history", {"path": str(clean_repo.path)})

    assert exit_code == 0
    subjects = [commit["subject"] for commit in lines[0]["data"]]
    assert subjects == ["mod: CoolMod 1.2.3", "chore: clean SPT 4.1.0"]


def test_history_with_name_matches_case_insensitively(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """history(name=\"coolmod\") filters to the matching mod ignoring case.

    After install_commit adds CoolMod, history with name=\"coolmod\" returns
    only the install commit, not the baseline.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("history", {"path": str(clean_repo.path), "name": "coolmod"})

    assert exit_code == 0
    subjects = [commit["subject"] for commit in lines[0]["data"]]
    assert subjects == ["mod: CoolMod 1.2.3"]


def test_disable_mod_reverts_the_install_commit_and_removes_it_from_list_mods(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """disable_mod(\"CoolMod\") reverts the install commit and state becomes disabled.

    After disabling, list_mods returns empty list; the revert commit hash and
    deleted file list appear in the response.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("disable_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "CoolMod"
    assert data["state"] == "disabled"
    assert data["commit"] != ""
    assert data["files"] == ["BepInEx/plugins/CoolMod.dll"]

    _, listed_lines = invoke_rpc("list_mods", {"path": str(clean_repo.path)})
    assert listed_lines[0]["data"] == []


def test_enable_mod_restores_a_disabled_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """enable_mod restores a previously disabled mod and it reappears in
    list_mods."""
    install_commit(clean_repo, "CoolMod", "1.2.3")
    invoke_rpc("disable_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    exit_code, lines = invoke_rpc("enable_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "CoolMod"
    assert data["state"] == "enabled"
    assert data["commit"] != ""

    _, listed_lines = invoke_rpc("list_mods", {"path": str(clean_repo.path)})
    assert [mod["name"] for mod in listed_lines[0]["data"]] == ["CoolMod"]


def test_remove_mod_deletes_the_files_and_reports_them(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """remove_mod(\"CoolMod\") deletes BepInEx/plugins/CoolMod.dll from disk.

    The result reports the file path in the files list and the new commit hash;
    the actual file no longer exists after the call.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("remove_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "CoolMod"
    assert data["commit"] != ""
    assert data["files"] == ["BepInEx/plugins/CoolMod.dll"]
    assert not (clean_repo.path / "BepInEx/plugins/CoolMod.dll").exists()


def test_check_profiles_reports_no_residue_for_a_db_free_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """check_profiles reports no residue for mods without database
    templates."""
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("check_profiles", {"path": str(clean_repo.path)})

    assert exit_code == 0
    assert lines[0]["data"] == {"residues": {}, "warnings": []}


def test_plan_profile_cleanup_reports_removals_through_rpc(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """plan_profile_cleanup reports items that would be removed from profiles
    if the mod is uninstalled."""
    item_id = "5c0d1b8b86f7744bb2683cf7"
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps({item_id: {"_id": item_id, "_name": "item"}})
        },
    )
    profile: JsonDict = {
        "characters": {
            "pmc": {
                "Inventory": {"items": [{"_id": "i1", "_tpl": item_id, "slotId": "main"}]},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
    profile_relpath = Path("SPT/user/profiles/abc.json")
    (clean_repo.path / profile_relpath).write_text(json.dumps(profile), encoding="utf-8")
    clean_repo.commit("chore: profile snapshot", paths=[profile_relpath])

    exit_code, lines = invoke_rpc(
        "plan_profile_cleanup",
        {"path": str(clean_repo.path), "mod_name": "ServerMod"},
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["mod"] == "ServerMod"
    assert data["profiles"] == ["abc"]
    assert [r["category"] for r in data["removals"]] == ["item"]


def test_clean_profile_writes_the_profile_and_reports_a_snapshot_through_rpc(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile removes residual database entries from profiles and
    reports a snapshot commit."""
    item_id = "5c0d1b8b86f7744bb2683cf8"
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps({item_id: {"_id": item_id, "_name": "item"}})
        },
    )
    profile: JsonDict = {
        "characters": {
            "pmc": {
                "Inventory": {"items": [{"_id": "i1", "_tpl": item_id, "slotId": "main"}]},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
    profile_relpath = Path("SPT/user/profiles/abc.json")
    profile_path = clean_repo.path / profile_relpath
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    clean_repo.commit("chore: profile snapshot", paths=[profile_relpath])

    exit_code, lines = invoke_rpc("clean_profile", {"path": str(clean_repo.path), "mod_name": "ServerMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["profiles"] == ["abc"]
    assert data["snapshot"] != ""
    cleaned = json.loads(profile_path.read_text(encoding="utf-8"))
    assert cleaned["characters"]["pmc"]["Inventory"]["items"] == []


def test_clean_profile_maps_invalid_path_error_to_the_invalid_path_code(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """clean_profile raises InvalidPathError(\"no such profile\") and error code becomes INVALID_PATH.

    The ERROR_MAPPING at errors.py maps InvalidPathError to \"INVALID_PATH\" code
    for the RPC envelope.
    """
    inject_error("clean_profile", InvalidPathError("no such profile"))

    exit_code, lines = invoke_rpc("clean_profile", {"path": "/whatever", "mod_name": "ServerMod"})

    assert exit_code == 1
    assert lines[0]["data"]["code"] == "INVALID_PATH"


def test_adopt_mod_commits_an_untracked_mod_folder(clean_repo: Repo) -> None:
    """adopt_mod(target=\"SPT/user/mods/ManualMod\") commits the directory.

    The mod's package.json is read for metadata; the response includes the mod
    info (name, version, state=installed) and the committed files list.
    """
    mod_dir = clean_repo.path / "SPT" / "user" / "mods" / "ManualMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        json.dumps({"name": "ManualMod", "version": "0.1.0", "author": "me"}),
        encoding="utf-8",
    )

    exit_code, lines = invoke_rpc(
        "adopt_mod",
        {"path": str(clean_repo.path), "target": "SPT/user/mods/ManualMod"},
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["mod"]["name"] == "ManualMod"
    assert data["mod"]["version"] == "0.1.0"
    assert data["mod"]["state"] == "installed"
    assert data["files"] == ["SPT/user/mods/ManualMod/package.json"]


def test_snapshot_commits_a_modified_profile(clean_repo: Repo) -> None:
    """snapshot commits a profile modification with a user-supplied message."""
    profile_path = clean_repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    exit_code, lines = invoke_rpc("snapshot", {"path": str(clean_repo.path), "message": "test snapshot"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["message"] == "test snapshot"
    assert data["files"] == ["SPT/user/profiles/abc.json"]
    assert data["commit"] != ""
