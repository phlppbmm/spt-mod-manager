"""Tests for ``sptmod.core.services`` disable/enable/adopt/snapshot and CLI wiring.

Covers the toggle lifecycle (disable/enable roundtrip, no-op notices, unknown
mod errors), ``adopt_mod`` (folder/DLL adoption, dirty-tree tolerance, and its
typed errors), ``init_game_dir``'s auto-adopt wiring through ``adopt_mod``, and
``snapshot`` (profile/config-only commits, mod files left untouched). CLI
cases exercise the same paths through Typer's ``CliRunner``.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import clean_repo_at
from fixture_types import InstallCommit
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import (
    ConflictError,
    GitError,
    ModNotFoundError,
    NotAGameDirError,
)
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")

runner = CliRunner()


# --- disable_mod -----------------------------------------------------------


def test_disable_mod_removes_the_mods_files(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """disable_mod reverts the mod's install commit, deleting all tracked files.

    Disabling ModA removes BepInEx/plugins/ModA.dll (added by the install commit).
    """
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})

    services.disable_mod(clean_repo.path, "ModA")

    assert not (clean_repo.path / "BepInEx/plugins/ModA.dll").exists()


def test_disable_mod_revert_commit_carries_disabled_trailers(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """The revert commit carries Mod and Mod-State trailers marking it disabled."""
    install_commit(clean_repo, "ModA")

    result = services.disable_mod(clean_repo.path, "ModA")

    commit = next(c for c in clean_repo.log() if c.hash == result.commit)
    assert commit.trailers.get("Mod") == "ModA"
    assert commit.trailers.get("Mod-State") == "disabled"


def test_disable_mod_removes_mod_from_default_list_but_keeps_it_in_all(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """disable_mod omits the mod from default list; include_all=True shows it as 'disabled'.

    After disabling ModA, list_mods() returns no ModA; list_mods(include_all=True)
    returns ModA with state='disabled'.
    """
    install_commit(clean_repo, "ModA")

    services.disable_mod(clean_repo.path, "ModA")

    assert "ModA" not in {mod.name for mod in services.list_mods(clean_repo.path)}
    all_mods = {mod.name: mod.state for mod in services.list_mods(clean_repo.path, include_all=True)}
    assert all_mods["ModA"] == "disabled"


def test_disabling_twice_is_a_no_op_with_a_notice_and_no_new_commit(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Disabling an already-disabled mod returns notice without adding a commit.

    After disabling ModA once, calling disable_mod again returns notice != '', commit='',
    and no new commit in the log.
    """
    install_commit(clean_repo, "ModA")
    services.disable_mod(clean_repo.path, "ModA")
    commits_before = [c.hash for c in clean_repo.log()]

    result = services.disable_mod(clean_repo.path, "ModA")

    assert result.notice != ""
    assert result.commit == ""
    assert not result.files
    assert result.state == "disabled"
    assert [c.hash for c in clean_repo.log()] == commits_before


def test_disable_mod_raises_mod_not_found_for_unknown_name(clean_repo: Repo) -> None:
    """disable_mod on a name never installed raises ModNotFoundError without changes.

    Calling disable_mod(path, 'Ghost') raises ModNotFoundError.
    """
    with pytest.raises(ModNotFoundError):
        services.disable_mod(clean_repo.path, "Ghost")


def test_disable_mod_conflict_with_an_unowned_later_change_names_no_mod(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """A conflicting path whose last-touching commit carries no ``Mod``
    trailer (a manual/foreign commit, unlike another mod's install commit)
    cannot be blamed on a mod name; the message falls back to naming just
    the files. If `if name:` (skipping the empty owner) or the final `if not
    blockers: return ConflictError(...)` branch were removed, this fails:
    the message would wrongly claim the file 'is now provided by' nothing,
    or KeyError on an empty-named blocker group."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/shared.dll": "base"}, minute=1)
    (clean_repo.path / "BepInEx/plugins/shared.dll").write_text("manually-edited", encoding="utf-8")
    clean_repo.commit("chore: manual edit", paths=[Path("BepInEx/plugins/shared.dll")])

    with pytest.raises(ConflictError) as exc_info:
        services.disable_mod(clean_repo.path, "ModA")

    message = str(exc_info.value)
    assert "later changes to" in message
    assert "BepInEx/plugins/shared.dll" in message
    assert "is now provided by" not in message


def test_disable_mod_conflict_names_the_later_mod_and_leaves_tree_clean(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Disabling a mod whose revert conflicts with a later mod's file raises
    ConflictError naming the blocking mod and leaves the tree clean.
    """
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/shared.dll": "base"}, minute=1)
    install_commit(
        clean_repo,
        "ModB",
        files={"BepInEx/plugins/shared.dll": "overwritten-by-modb"},
        minute=2,
    )

    with pytest.raises(ConflictError) as exc_info:
        services.disable_mod(clean_repo.path, "ModA")

    assert "ModB" in str(exc_info.value)
    assert clean_repo.is_clean() is True
    assert (clean_repo.path / "BepInEx/plugins/shared.dll").read_text(encoding="utf-8") == "overwritten-by-modb"


# --- enable_mod --------------------------------------------------------------


def test_enable_mod_restores_the_files_with_original_content(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """enable_mod cherry-picks the install commit, restoring the mod's files.

    After disabling ModA, enable_mod restores BepInEx/plugins/ModA.dll with original-bytes.
    """
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "original-bytes"})
    services.disable_mod(clean_repo.path, "ModA")

    services.enable_mod(clean_repo.path, "ModA")

    assert (clean_repo.path / "BepInEx/plugins/ModA.dll").read_text(encoding="utf-8") == "original-bytes"


def test_enable_mod_commit_carries_enabled_trailers_and_mod_is_active_again(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Enabling a mod creates a commit with Mod and Mod-State trailers, listing it active."""
    install_commit(clean_repo, "ModA")
    services.disable_mod(clean_repo.path, "ModA")

    result = services.enable_mod(clean_repo.path, "ModA")

    commit = next(c for c in clean_repo.log() if c.hash == result.commit)
    assert commit.trailers.get("Mod") == "ModA"
    assert commit.trailers.get("Mod-State") == "enabled"
    active = {mod.name: mod.state for mod in services.list_mods(clean_repo.path)}
    assert active["ModA"] == "enabled"


def test_enable_mod_raises_mod_not_found_for_unknown_name(clean_repo: Repo) -> None:
    """enable_mod on a name never installed raises ModNotFoundError.

    Calling enable_mod(path, 'Ghost') raises ModNotFoundError.
    """
    with pytest.raises(ModNotFoundError):
        services.enable_mod(clean_repo.path, "Ghost")


def test_enabling_an_active_mod_is_a_no_op_with_a_notice(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """Enabling an already-enabled mod returns notice without adding a commit.

    Calling enable_mod on ModA when it is already enabled returns notice != '',
    commit='', and no new commit in the log.
    """
    install_commit(clean_repo, "ModA")
    commits_before = [c.hash for c in clean_repo.log()]

    result = services.enable_mod(clean_repo.path, "ModA")

    assert result.notice != ""
    assert result.commit == ""
    assert not result.files
    assert result.state == "installed"
    assert [c.hash for c in clean_repo.log()] == commits_before


def test_enable_mod_conflict_names_the_later_mod_and_leaves_tree_clean(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Mirrors the disable_mod/remove_mod conflict tests: enabling a mod
    whose install commit's cherry-pick collides with a file a later mod now
    provides must raise ConflictError instead of leaving a mid-cherry-pick
    tree. If the `except GitError: raise _sequencer_conflict(...)` in
    enable_mod were removed, this fails: the underlying GitError would
    propagate uncaught, or a conflicted tree would be left behind."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/shared.dll": "base"}, minute=1)
    services.disable_mod(clean_repo.path, "ModA")
    install_commit(
        clean_repo,
        "ModB",
        files={"BepInEx/plugins/shared.dll": "overwritten-by-modb"},
        minute=2,
    )

    with pytest.raises(ConflictError) as exc_info:
        services.enable_mod(clean_repo.path, "ModA")

    assert "ModB" in str(exc_info.value)
    assert clean_repo.is_clean() is True
    assert (clean_repo.path / "BepInEx/plugins/shared.dll").read_text(encoding="utf-8") == "overwritten-by-modb"


def test_enable_mod_conflict_with_no_conflicting_paths_still_raises_plainly(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """A cherry-pick can fail with *no* merge conflict at all: here the
    working tree already has byte-identical content (restored by an
    unrelated commit), so the cherry-pick's diff is empty and git refuses it
    outright -- `error.conflicting_paths` is then empty, distinct from the
    'later mod' test above. `_sequencer_conflict`'s `if not
    error.conflicting_paths: return ConflictError(...)` branch must still
    turn this into a plain ConflictError. If that guard were removed, this
    fails: `blockers` would stay empty and the function would fall through
    to the 'later changes ... conflict' message with an empty path list, or
    the raw GitError would need to be handled some other way."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})
    services.disable_mod(clean_repo.path, "ModA")
    dll = clean_repo.path / "BepInEx/plugins/ModA.dll"
    dll.parent.mkdir(parents=True, exist_ok=True)
    dll.write_text("content", encoding="utf-8")
    clean_repo.commit("chore: manual restore", paths=[Path("BepInEx/plugins/ModA.dll")])

    with pytest.raises(ConflictError) as exc_info:
        services.enable_mod(clean_repo.path, "ModA")

    assert "cannot enable ModA" in str(exc_info.value)
    assert clean_repo.is_clean() is True


# --- adopt_mod ---------------------------------------------------------------


def test_adopt_mod_folder_reads_metadata_from_package_json(clean_repo: Repo) -> None:
    """adopt_mod reads name and version from package.json of the target folder.

    Adopting SPT/user/mods/ServerMod with package.json returns result.mod with name,
    version, state='installed', and source set.
    """
    mod_dir = clean_repo.path / "SPT/user/mods/ServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "ServerMod", "version": "1.2.3"}', encoding="utf-8")

    result = services.adopt_mod(clean_repo.path, Path("SPT/user/mods/ServerMod"))

    assert result.mod.name == "ServerMod"
    assert result.mod.version == "1.2.3"
    assert result.mod.state == "installed"
    assert result.mod.source == "SPT/user/mods/ServerMod"


def test_adopt_mod_folder_makes_the_mod_visible_in_list_and_files(
    clean_repo: Repo,
) -> None:
    """adopt_mod makes the folder visible in list_mods and mod_files.

    After adopting SPT/user/mods/ServerMod, it appears in list_mods and its
    package.json appears in mod_files.
    """
    mod_dir = clean_repo.path / "SPT/user/mods/ServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "ServerMod", "version": "1.2.3"}', encoding="utf-8")

    services.adopt_mod(clean_repo.path, Path("SPT/user/mods/ServerMod"))

    assert "ServerMod" in {mod.name for mod in services.list_mods(clean_repo.path)}
    files = {f.path for f in services.mod_files(clean_repo.path, "ServerMod")}
    assert Path("SPT/user/mods/ServerMod/package.json") in files


def test_adopt_bare_dll_needs_name_and_version_overrides(clean_repo: Repo) -> None:
    """Adopting a bare DLL requires name_override and version_override parameters.

    Without overrides, adopting BepInEx/plugins/LooseMod.dll via name/version lookup only
    succeeds when both overrides are supplied.
    """
    (clean_repo.path / "BepInEx/plugins/LooseMod.dll").write_bytes(b"dll-bytes")

    result = services.adopt_mod(
        clean_repo.path,
        Path("BepInEx/plugins/LooseMod.dll"),
        name_override="LooseMod",
        version_override="0.9.0",
    )

    assert result.mod.name == "LooseMod"
    assert result.mod.version == "0.9.0"
    assert result.mod.mod_type == "client"
    assert "LooseMod" in {mod.name for mod in services.list_mods(clean_repo.path)}


def test_adopt_bare_dll_without_version_override_raises_invalid_metadata(
    clean_repo: Repo,
) -> None:
    """adopt_mod on a bare DLL without version_override raises InvalidMetadataError.

    Adopting BepInEx/plugins/LooseMod.dll without version_override raises InvalidMetadataError.
    """
    (clean_repo.path / "BepInEx/plugins/LooseMod.dll").write_bytes(b"dll-bytes")

    with pytest.raises(InvalidMetadataError):
        services.adopt_mod(clean_repo.path, Path("BepInEx/plugins/LooseMod.dll"))


def test_adopt_mod_works_on_a_dirty_tree(clean_repo: Repo) -> None:
    """Adoption stages only the target files, leaving other dirty changes untouched."""
    mod_dir = clean_repo.path / "SPT/user/mods/ServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "ServerMod", "version": "1.0.0"}', encoding="utf-8")
    # An unrelated dirty change must stay untouched by adopt.
    (clean_repo.path / "SPT/user/profiles/abc.json").write_text('{"changed": true}', encoding="utf-8")

    result = services.adopt_mod(clean_repo.path, Path("SPT/user/mods/ServerMod"))

    assert result.mod.name == "ServerMod"
    assert clean_repo.status().modified == [Path("SPT/user/profiles/abc.json")]


def test_adopt_mod_raises_conflict_error_for_an_already_tracked_folder(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """adopt_mod on an active mod's folder raises ConflictError.

    Adopting a folder already owned by ModA raises ConflictError.
    """
    install_commit(
        clean_repo,
        "ModA",
        mod_type="server",
        files={"SPT/user/mods/ModA/package.json": ('{"name": "ModA", "version": "1.0.0"}')},
    )

    with pytest.raises(ConflictError):
        services.adopt_mod(clean_repo.path, Path("SPT/user/mods/ModA"))


def test_adopt_mod_raises_conflict_error_for_a_path_that_is_part_of_spt_itself(
    clean_repo: Repo,
) -> None:
    """adopt_mod on SPT's own path raises ConflictError naming "part of SPT itself".

    Adopting BepInEx/plugins/spt raises ConflictError with that message.
    """
    with pytest.raises(ConflictError) as exc_info:
        services.adopt_mod(clean_repo.path, Path("BepInEx/plugins/spt"))

    assert "part of SPT itself" in str(exc_info.value)


def test_adopt_mod_raises_conflict_error_when_the_target_holds_no_untracked_files(
    clean_repo: Repo,
) -> None:
    """A target that is already fully tracked but owned by no active mod
    (a plain commit carrying no ``Mod`` trailer, unlike an install commit)
    must be reported through the 'holds no untracked files' ConflictError,
    distinct from the 'already provided by <mod>' one above."""
    mod_dir = clean_repo.path / "BepInEx" / "plugins" / "TrackedMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "TrackedMod.dll").write_bytes(b"already-tracked-bytes")
    clean_repo.commit(
        "chore: track TrackedMod manually",
        paths=[Path("BepInEx/plugins/TrackedMod/TrackedMod.dll")],
    )

    with pytest.raises(ConflictError) as exc_info:
        services.adopt_mod(clean_repo.path, mod_dir)

    assert "no untracked files" in str(exc_info.value)


def test_adopt_mod_raises_not_a_game_dir_error_for_a_target_outside_mod_paths(
    clean_repo: Repo,
) -> None:
    """adopt_mod on a path outside mod dirs raises NotAGameDirError.

    Adopting BepInEx/config/loose.cfg raises NotAGameDirError.
    """
    (clean_repo.path / "BepInEx/config/loose.cfg").write_text("x", encoding="utf-8")

    with pytest.raises(NotAGameDirError):
        services.adopt_mod(clean_repo.path, Path("BepInEx/config/loose.cfg"))


def test_adopt_mod_raises_mod_not_found_for_a_missing_target(clean_repo: Repo) -> None:
    """adopt_mod on a non-existent path raises ModNotFoundError.

    Adopting SPT/user/mods/DoesNotExist raises ModNotFoundError.
    """
    with pytest.raises(ModNotFoundError):
        services.adopt_mod(clean_repo.path, Path("SPT/user/mods/DoesNotExist"))


def test_adopt_mod_raises_not_a_game_dir_error_for_a_target_outside_the_game_dir(
    clean_repo: Repo, tmp_path: Path
) -> None:
    """adopt_mod on a path outside game dir raises NotAGameDirError.

    Adopting a path outside clean_repo.path raises NotAGameDirError with message.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    with pytest.raises(NotAGameDirError) as exc_info:
        services.adopt_mod(clean_repo.path, outside)

    assert "lies outside the game directory" in str(exc_info.value)


def test_adopt_mod_of_a_partially_tracked_folder_raises_conflict(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """adopt_mod on a partially-tracked folder raises ConflictError.

    Adopting a folder with both tracked and untracked files raises ConflictError.
    """
    install_commit(
        clean_repo,
        "ModA",
        mod_type="server",
        files={"SPT/user/mods/ModA/package.json": ('{"name": "ModA", "version": "1.0.0"}')},
    )
    (clean_repo.path / "SPT/user/mods/ModA/stray.txt").write_text("manually dropped file", encoding="utf-8")

    with pytest.raises(ConflictError):
        services.adopt_mod(clean_repo.path, Path("SPT/user/mods/ModA"))


# --- init auto-adopt -----------------------------------------------------


def test_init_adopt_true_commits_every_pre_existing_mod(fake_game_dir: Path) -> None:
    """When init is given adopt=True, it automatically adopts all pre-existing mods."""
    mod_dir = fake_game_dir / "SPT/user/mods/OldMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "OldMod", "version": "1.0.0"}', encoding="utf-8")

    result = services.init_game_dir(fake_game_dir, adopt=True)

    assert [mod.name for mod in result.adopted] == ["OldMod"]
    assert "OldMod" in {mod.name for mod in services.list_mods(fake_game_dir)}


def test_init_adopt_explicit_sequence_adopts_only_the_selected_mods(
    fake_game_dir: Path,
) -> None:
    """init_game_dir with adopt=[path] adopts only the given paths, not all pre-existing mods.

    After creating both SPT/user/mods/OldMod and SPT/user/mods/OtherMod with package.json,
    calling init_game_dir(adopt=[Path('SPT/user/mods/OldMod')]) yields result.adopted containing
    only OldMod, and list_mods() shows OldMod but not OtherMod. This guards selective adoption
    that lets users skip unwanted pre-existing mods during init.
    """
    for name in ("OldMod", "OtherMod"):
        mod_dir = fake_game_dir / "SPT/user/mods" / name
        mod_dir.mkdir(parents=True)
        (mod_dir / "package.json").write_text(f'{{"name": "{name}", "version": "1.0.0"}}', encoding="utf-8")

    result = services.init_game_dir(fake_game_dir, adopt=[Path("SPT/user/mods/OldMod")])

    assert [mod.name for mod in result.adopted] == ["OldMod"]
    names = {mod.name for mod in services.list_mods(fake_game_dir)}
    assert "OldMod" in names
    assert "OtherMod" not in names


# --- snapshot ----------------------------------------------------------------


def test_snapshot_commits_only_profile_and_config_changes(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """snapshot commits only profile and config changes, leaving mod files untracked.

    After installing ModA (BepInEx/plugins/ModA.dll), modifying both it and
    SPT/user/profiles/abc.json and BepInEx/config/mod.cfg, calling snapshot() commits only
    the profile and config, leaving ModA.dll in status.modified (dirty). This preserves mod
    files for install/disable operations while allowing profile-snapshot-only commits.
    """
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "v1"})
    config_file = clean_repo.path / "BepInEx/config/mod.cfg"
    config_file.write_text("original config", encoding="utf-8")
    clean_repo.commit("chore: seed config", paths=[Path("BepInEx/config/mod.cfg")])

    (clean_repo.path / "SPT/user/profiles/abc.json").write_text('{"changed": true}', encoding="utf-8")
    config_file.write_text("changed config", encoding="utf-8")
    (clean_repo.path / "BepInEx/plugins/ModA.dll").write_text("v2", encoding="utf-8")

    result = services.snapshot(clean_repo.path, "manual test")

    committed = {f.path for f in clean_repo.show_files(result.commit)}
    assert committed == {
        Path("SPT/user/profiles/abc.json"),
        Path("BepInEx/config/mod.cfg"),
    }
    status_after = clean_repo.status()
    assert Path("BepInEx/plugins/ModA.dll") in status_after.modified


def test_snapshot_commit_subject_and_no_mod_trailer(clean_repo: Repo) -> None:
    """snapshot commits with subject='snapshot: <message>' and no Mod trailer.

    After modifying SPT/user/profiles/abc.json and calling snapshot('manual test'),
    the commit subject == 'snapshot: manual test' and commit.trailers has no 'Mod' key.
    This distinguishes snapshots from mod-install commits for history filtering.
    """
    (clean_repo.path / "SPT/user/profiles/abc.json").write_text('{"changed": true}', encoding="utf-8")

    result = services.snapshot(clean_repo.path, "manual test")

    commit = next(c for c in clean_repo.log() if c.hash == result.commit)
    assert commit.subject == "snapshot: manual test"
    assert "Mod" not in commit.trailers


def test_snapshot_raises_git_error_when_nothing_changed(clean_repo: Repo) -> None:
    """snapshot raises GitError when tree is clean with no profile or config changes.

    Calling snapshot() on a clean repo with no modifications raises GitError. This guards
    the precondition that snapshot requires at least one profile or config change to commit.
    """
    with pytest.raises(GitError):
        services.snapshot(clean_repo.path)


# --- CLI -----------------------------------------------------------------


def test_cli_disable_then_enable_roundtrip(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """CLI 'sptmod disable' then 'sptmod enable' round-trip with exit codes 0 and output.

    After installing CoolMod and running 'sptmod disable CoolMod' then 'sptmod enable CoolMod'
    from the game dir, both commands return exit_code == 0 and enable output contains 'CoolMod'.
    This verifies that the CLI wiring for disable/enable matches the service behavior.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    disable_result = runner.invoke(app, ["disable", "CoolMod"])
    enable_result = runner.invoke(app, ["enable", "CoolMod"])

    assert disable_result.exit_code == 0
    assert enable_result.exit_code == 0
    assert "CoolMod" in enable_result.stdout


def test_cli_disable_unknown_mod_exits_with_code_11(fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI disable of unknown mod exits with code 11 (MOD_NOT_FOUND)."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["disable", "Ghost"])

    assert result.exit_code == 11
    assert result.stderr.startswith("error:")


def test_cli_adopt_reports_the_adopted_mod(fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 'sptmod adopt <path>' outputs the adopted mod name with exit code 0.

    After creating SPT/user/mods/ServerMod with package.json and running 'sptmod adopt SPT/user/mods/ServerMod'
    from the game dir, result.exit_code == 0 and 'ServerMod' appears in result.stdout. This confirms
    successful adoption and provides user feedback.
    """
    clean_repo_at(fake_game_dir)
    mod_dir = fake_game_dir / "SPT/user/mods/ServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "ServerMod", "version": "1.0.0"}', encoding="utf-8")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["adopt", "SPT/user/mods/ServerMod"])

    assert result.exit_code == 0
    assert "ServerMod" in result.stdout
