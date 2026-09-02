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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_ignored_dummies_are_not_visible_as_untracked(initialized_repo: Repo) -> None:
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
    assert initialized_repo.untracked_under(Path("SPT/user/logs")) == []
    assert initialized_repo.untracked_under(Path("EscapeFromTarkov_Data")) == []
    assert initialized_repo.untracked_under(Path("SPT/user/profiles/backups")) == []


def test_untracked_under_shows_new_trackable_mod_file(initialized_repo: Repo) -> None:
    mod_file = initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll"
    _write(mod_file, "binary-ish")

    result = initialized_repo.untracked_under(Path("SPT/user/mods"))

    assert result == [Path("SPT/user/mods/TestMod/mod.dll")]


def test_untracked_under_shows_new_trackable_plugin_and_config(
    initialized_repo: Repo,
) -> None:
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
    _write(initialized_repo.path / "BepInEx" / "patchers" / "Fixer.dll", "patcher")

    commit_hash = initialized_repo.commit("mod: whitelist patchers", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    assert Path("BepInEx/patchers/Fixer.dll") in files


def test_status_untracked_lists_profile_json_before_first_commit(
    initialized_repo: Repo,
) -> None:
    status = initialized_repo.status()

    assert Path("SPT/user/profiles/abc.json") in status.untracked


def test_commit_with_paths_none_stages_only_whitelisted_files(
    initialized_repo: Repo,
) -> None:
    _write(
        initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll"
    )
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
    _write(
        initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll"
    )

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
    _write(
        initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll"
    )
    initialized_repo.commit("mod: whitelist roundtrip", paths=None)

    # The ignored dummy files from fake_game_dir are still on disk but must
    # not make the tree appear dirty.
    assert initialized_repo.is_clean() is True
