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

from conftest import clean_repo_at
from fixture_types import InstallCommit, JsonDict
from fixtures_profile import profile_with_item
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import ProfileResidueError
from sptmod.core.repo import Repo

runner = CliRunner()


def _commit_profile_with_item(repo: Repo, item_id: str) -> Path:
    """Write and commit a profile that references ``item_id``.

    Profiles are tracked content, so the change must be committed to keep
    the tree clean for the subsequent ``remove_mod``/CLI call.
    """
    profile_path = repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(json.dumps(profile_with_item(item_id)), encoding="utf-8")
    repo.commit("chore: profile snapshot", paths=[Path("SPT/user/profiles/abc.json")])
    return profile_path


def _install_server_mod_with_item(clean_repo: Repo, install_commit: InstallCommit, item_id: str) -> None:
    """Install a server mod with a database entry for the given item ID."""
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps({item_id: {"_id": item_id, "_name": "item"}})
        },
    )


# --------------------------------------------------------------------------
# services.list_profiles
# --------------------------------------------------------------------------


def _write_profile_file(profiles_dir: Path, name: str, document: JsonDict) -> Path:
    """Write a profile document to disk and return its path."""
    target = profiles_dir / name
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def _character(nickname: str, level: int, side: str) -> JsonDict:
    """Build a profile document with the given character metadata."""
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
    """list_profiles extracts and returns the PMC character's metadata from profile files.

    Reads the Info.Nickname, Info.Level, and Info.Side from a profile's pmc
    character, returning them as nickname, level, and side in each ProfileSummary.
    """
    profiles_dir = fake_game_dir / "SPT" / "user" / "profiles"
    target = _write_profile_file(profiles_dir, "abc.json", _character("Nikita", 42, "Usec"))

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
    assert datetime.fromisoformat(summary.modified) == datetime.fromtimestamp(target.stat().st_mtime, UTC)


def test_list_profiles_sorts_the_profiles_by_file_name(fake_game_dir: Path) -> None:
    """list_profiles returns profiles sorted by file name in ascending order.

    Given profiles abc.json, zulu.json, and alpha.json, they are returned
    sorted to [abc.json, alpha.json, zulu.json].
    """
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
    """list_profiles fills missing character metadata with default values.

    A profile with an empty Info object gets nickname='', level=0, side='',
    readable=True, and error=''.
    """
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
    """list_profiles sets readable=False and a standard error message for malformed JSON.

    A profile containing invalid JSON gets readable=False and error set to the
    constant message 'This file could not be read as a player profile.'
    """
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
    """list_profiles returns an empty list when the profiles directory is empty.

    After deleting the seeded abc.json from the profiles directory,
    list_profiles returns an empty list rather than raising.
    """
    (fake_game_dir / "SPT" / "user" / "profiles" / "abc.json").unlink()

    assert services.list_profiles(fake_game_dir) == []


def test_list_profiles_reads_the_profiles_of_the_spt_runtime_layout(
    fake_game_dir_runtime: Path,
) -> None:
    """list_profiles reads from SPT_Runtime/user/profiles for 4.1+ game layouts.

    A game directory with SPT_Runtime (4.1+) instead of SPT (4.0) has its
    profiles read from the runtime layout's profiles directory.
    """
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
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """check_profiles finds residues in scav character and top-level insurance sections.

    When a mod's item template appears in scav.Inventory.items and a mod's
    trader ID appears in the top-level insurance section, both residues are
    reported with the correct category, character, and path.
    """
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
    profile: JsonDict = {
        "characters": {
            "pmc": {"Inventory": {"items": []}, "TradersInfo": {}, "Quests": []},
            "scav": {
                "Inventory": {"items": [{"_id": "i1", "_tpl": item_id, "slotId": "main"}]},
                "TradersInfo": {},
                "Quests": [],
            },
        },
        "insurance": [{"traderId": trader_id, "items": []}],
    }
    (profiles_dir / "abc.json").write_text(json.dumps(profile), encoding="utf-8")

    result = services.check_profiles(clean_repo.path)

    residues = result.residues["ServerMod"]
    scav_item = next(r for r in residues if r.category == "item" and r.character == "scav")
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
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod raises ProfileResidueError before touching the tree when a profile holds residues.

    After installing ServerMod and committing a profile that references its
    item ID, remove_mod(path, 'ServerMod') raises ProfileResidueError
    without reverting or removing any files, and the mod is still listed as
    active afterwards.
    """
    item_id = "5c0d1b8b86f7744bb2683cf1"
    _install_server_mod_with_item(clean_repo, install_commit, item_id)
    _commit_profile_with_item(clean_repo, item_id)

    with pytest.raises(ProfileResidueError) as exc_info:
        services.remove_mod(clean_repo.path, "ServerMod")

    message = str(exc_info.value)
    assert "abc" in message
    assert "item" in message
    active_names = {mod.name for mod in services.list_mods(clean_repo.path)}
    assert "ServerMod" in active_names


def test_remove_mod_force_reverts_and_leaves_the_profile_file_byte_identical(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod(force=True) reverts the mod without touching profile files.

    With force=True, the mod's install commit is reverted (removing
    SPT/user/mods/ServerMod/db/templates/items.json) but the profile file
    remains exactly as it was, byte for byte, even though it still holds residues.
    """
    item_id = "5c0d1b8b86f7744bb2683cf3"
    _install_server_mod_with_item(clean_repo, install_commit, item_id)
    profile_path = _commit_profile_with_item(clean_repo, item_id)
    before = profile_path.read_bytes()

    result = services.remove_mod(clean_repo.path, "ServerMod", force=True)

    assert result.name == "ServerMod"
    assert profile_path.read_bytes() == before
    assert not (clean_repo.path / "SPT/user/mods/ServerMod/db/templates/items.json").exists()


def test_remove_mod_without_db_dir_proceeds_without_a_residue_error(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod succeeds without checking for residues when the mod has no db/ directory.

    A client-only mod (ClientMod with only BepInEx/plugins/ClientMod.dll) can
    be removed without raising ProfileResidueError, even without any profile
    check, because there is no db/ directory to contribute content IDs.
    """
    install_commit(clean_repo, "ClientMod", files={"BepInEx/plugins/ClientMod.dll": "content"})

    result = services.remove_mod(clean_repo.path, "ClientMod")

    assert result.name == "ClientMod"


def test_remove_mod_result_warnings_include_broken_db_json_entries(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """remove_mod includes malformed db files in result.warnings.

    A mod with one valid db file (traders/*/base.json) and one unparseable
    JSON file (templates/items.json with '{not valid json') returns a result
    with one warning naming items.json.
    """
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
    """The CLI check command exits with code 0 when no residues are found.

    Running 'sptmod check' on a clean repository with no mods installed
    results in exit code 0.
    """
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0


def test_cli_check_exits_zero_and_reports_residues_when_found(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The CLI check command exits with 0 and reports residues in stdout when found.

    After installing ServerMod and committing a profile referencing its item,
    'sptmod check' exits with code 0 (not an error, just a report) and
    includes 'ServerMod' and profile ID 'abc' in the output.
    """
    repo = clean_repo_at(fake_game_dir)
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
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The CLI remove command exits with code 8 and stderr when a profile holds residues.

    After installing ServerMod and committing a profile with its item,
    'sptmod remove ServerMod' exits with code 8 and prints 'error:' to stderr.
    """
    repo = clean_repo_at(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf5"
    _install_server_mod_with_item(repo, install_commit, item_id)
    _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--yes"])

    assert result.exit_code == 8
    assert result.stderr.startswith("error:")


def test_cli_remove_with_force_succeeds_despite_profile_residue(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The CLI remove --force flag succeeds with exit code 0 even with residues.

    After installing ServerMod and committing a profile with its item,
    'sptmod remove ServerMod --force' exits with code 0, includes 'ServerMod'
    in stdout, and removes the mod despite the residue.
    """
    repo = clean_repo_at(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf6"
    _install_server_mod_with_item(repo, install_commit, item_id)
    _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--force", "--yes"])

    assert result.exit_code == 0
    assert "ServerMod" in result.stdout


def test_cli_remove_clean_profiles_flag_actually_cleans_the_profile(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The CLI remove --clean-profiles flag removes residues from the profile before reverting.

    After installing ServerMod with an item and committing a profile
    containing that item, 'sptmod remove ServerMod --clean-profiles' exits 0,
    leaves the profile with an empty items array and names the cleaned
    profile ID 'abc' on stdout.
    """
    repo = clean_repo_at(fake_game_dir)
    item_id = "5c0d1b8b86f7744bb2683cf7"
    _install_server_mod_with_item(repo, install_commit, item_id)
    profile_path = _commit_profile_with_item(repo, item_id)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--clean-profiles", "--yes"])

    assert result.exit_code == 0
    cleaned = json.loads(profile_path.read_text(encoding="utf-8"))
    assert cleaned["characters"]["pmc"]["Inventory"]["items"] == []
    assert "abc" in result.stdout


def test_cli_remove_clean_profiles_reports_no_cleaning_needed_and_untouched_count(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The CLI remove --clean-profiles output distinguishes structured from unstructured residues.

    An item ID appearing only in a profile's info.note (string fallback, not
    a structured container) outputs 'no profile needed cleaning', 'removed 0
    profile entries', and '1 entries stayed untouched'.
    """
    # item_id sits only in raw text (e.g. a note field), never in a
    # structured container: sptmod can only find it via the string
    # fallback, so nothing is removed but something stays untouched.
    item_id = "5c0d1b8b86f7744bb2683cfb"
    repo = clean_repo_at(fake_game_dir)
    _install_server_mod_with_item(repo, install_commit, item_id)
    profile: JsonDict = {
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
    repo.commit("chore: profile snapshot", paths=[Path("SPT/user/profiles/abc.json")])
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["remove", "ServerMod", "--clean-profiles", "--yes"])

    assert result.exit_code == 0
    assert "no profile needed cleaning" in result.stdout
    assert "removed 0 profile entries" in result.stdout
    assert "1 entries stayed" in result.stdout


def test_cli_remove_prints_a_warning_line_for_broken_db_json(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """The CLI remove output includes warnings for malformed db files.

    A mod with unparseable JSON in its db (templates/items.json with '{not
    valid json') causes 'sptmod remove ServerMod' to print 'items.json' in
    stdout or stderr.
    """
    repo = clean_repo_at(fake_game_dir)
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

    result = runner.invoke(app, ["remove", "ServerMod", "--yes"])

    assert result.exit_code == 0
    assert "items.json" in result.stdout + result.stderr
