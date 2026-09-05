"""Tests for the whitelist semantics implied by the .gitignore written by Repo.init.

Trackable: .gitignore, SPT/user/mods/**, SPT/user/profiles/*.json,
BepInEx/plugins/**, BepInEx/config/**.
Must stay invisible (ignored): SPT/user/logs/**, SPT/user/cache/**,
EscapeFromTarkov_Data/**, SPT/user/profiles/backups/**, and anything else
outside the whitelist (e.g. SPT/SPT_Data/**).
"""

from pathlib import Path

from sptmod.core.repo import Repo


def _write(path: Path, content: str = "content") -> None:
    """Create file with content, creating parent directories as needed.

    Calls path.parent.mkdir(parents=True, exist_ok=True) then writes content
    to path as UTF-8 text (default content is "content").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_ignored_dummies_are_not_visible_as_untracked(initialized_repo: Repo) -> None:
    """Files in ignored directories (logs, cache, backups) never appear in status."""
    status = initialized_repo.status()

    ignored = {
        Path("SPT/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT/user/profiles/backups/old.json"),
        Path("SPT/SPT_Data/configs/core.json"),
    }
    assert ignored.isdisjoint(set(status.untracked))
    assert ignored.isdisjoint(set(status.modified))


def test_untracked_under_hides_ignored_prefixes(initialized_repo: Repo) -> None:
    """Repo.untracked_under returns [] for ignored directory prefixes.

    Calls untracked_under on ignored prefixes (SPT/user/logs, EscapeFromTarkov_Data,
    SPT/user/profiles/backups). All must return [] (empty), as these are ignored.
    """
    assert initialized_repo.untracked_under(Path("SPT/user/logs")) == []
    assert initialized_repo.untracked_under(Path("EscapeFromTarkov_Data")) == []
    assert initialized_repo.untracked_under(Path("SPT/user/profiles/backups")) == []


def test_untracked_under_shows_new_trackable_mod_file(initialized_repo: Repo) -> None:
    """Repo.untracked_under shows new mod files in SPT/user/mods.

    Creates SPT/user/mods/TestMod/mod.dll. Calls untracked_under(Path("SPT/user/mods")).
    Returns [Path("SPT/user/mods/TestMod/mod.dll")].
    """
    mod_file = initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll"
    _write(mod_file, "binary-ish")

    result = initialized_repo.untracked_under(Path("SPT/user/mods"))

    assert result == [Path("SPT/user/mods/TestMod/mod.dll")]


def test_untracked_under_shows_new_trackable_plugin_and_config(
    initialized_repo: Repo,
) -> None:
    """Repo.untracked_under shows plugins and config in BepInEx.

    Creates BepInEx/plugins/TestPlugin.dll and BepInEx/config/TestPlugin.cfg.
    Calls untracked_under(Path("BepInEx")). Result set must contain both paths.
    """
    _write(initialized_repo.path / "BepInEx" / "plugins" / "TestPlugin.dll", "plugin")
    _write(initialized_repo.path / "BepInEx" / "config" / "TestPlugin.cfg", "cfg")

    result = set(initialized_repo.untracked_under(Path("BepInEx")))

    assert result == {
        Path("BepInEx/plugins/TestPlugin.dll"),
        Path("BepInEx/config/TestPlugin.cfg"),
    }


def test_untracked_under_shows_new_trackable_patcher(
    initialized_repo: Repo,
) -> None:
    """``BepInEx/patchers/`` (e.g. WTT-CommonLib's FixPluginTypesSerialization.dll)
    must be trackable like ``BepInEx/plugins/`` and ``BepInEx/config/``.
    """
    _write(initialized_repo.path / "BepInEx" / "patchers" / "Fixer.dll", "patcher")

    result = initialized_repo.untracked_under(Path("BepInEx"))

    assert Path("BepInEx/patchers/Fixer.dll") in result


def test_commit_with_paths_none_stages_bepinex_patchers(
    initialized_repo: Repo,
) -> None:
    """Repo.commit(paths=None) stages BepInEx/patchers files (e.g. WTT-CommonLib).

    Creates BepInEx/patchers/Fixer.dll. Commits with paths=None. commit_hash
    show_files must include Path("BepInEx/patchers/Fixer.dll").
    """
    _write(initialized_repo.path / "BepInEx" / "patchers" / "Fixer.dll", "patcher")

    commit_hash = initialized_repo.commit("mod: whitelist patchers", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    assert Path("BepInEx/patchers/Fixer.dll") in files


def test_status_untracked_lists_profile_json_before_first_commit(
    initialized_repo: Repo,
) -> None:
    """Repo.status lists default profile fixture JSON as untracked initially.

    Before first commit, repo.status().untracked must include
    Path("SPT/user/profiles/abc.json") (from initialized_repo fixture).
    """
    status = initialized_repo.status()

    assert Path("SPT/user/profiles/abc.json") in status.untracked


def test_commit_with_paths_none_stages_only_whitelisted_files(
    initialized_repo: Repo,
) -> None:
    """Repo.commit(paths=None) stages all whitelisted files (mods, plugins, config, etc)."""
    _write(initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll")
    _write(initialized_repo.path / "BepInEx" / "plugins" / "TestPlugin.dll", "plugin")
    _write(initialized_repo.path / "BepInEx" / "config" / "TestPlugin.cfg", "cfg")

    commit_hash = initialized_repo.commit("mod: whitelist roundtrip", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    expected = {
        Path(".gitignore"),
        Path("SPT/user/profiles/abc.json"),
        Path("SPT/user/mods/TestMod/mod.dll"),
        Path("BepInEx/plugins/TestPlugin.dll"),
        Path("BepInEx/config/TestPlugin.cfg"),
    }
    assert files == expected


def test_commit_with_paths_none_never_includes_ignored_dummies(
    initialized_repo: Repo,
) -> None:
    """Repo.commit(paths=None) excludes ignored files (logs, cache, backups, etc)."""
    _write(initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll")

    commit_hash = initialized_repo.commit("mod: whitelist roundtrip", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    forbidden = {
        Path("SPT/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT/user/profiles/backups/old.json"),
        Path("SPT/SPT_Data/configs/core.json"),
    }
    assert files.isdisjoint(forbidden)


def test_is_clean_after_full_whitelist_commit_ignores_leftover_dummies(
    initialized_repo: Repo,
) -> None:
    """Repo.is_clean ignores leftover ignored files after whitelist commit.

    Creates SPT/user/mods/TestMod/mod.dll and commits with paths=None.
    Ignored dummy files from fake_game_dir still on disk. repo.is_clean()
    must return True (ignores the dummies, they're not tracked).
    """
    _write(initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll")
    initialized_repo.commit("mod: whitelist roundtrip", paths=None)

    # The ignored dummy files from fake_game_dir are still on disk but must
    # not make the tree appear dirty.
    assert initialized_repo.is_clean() is True
