"""Tests for ``sptmod.core.services.init_game_dir``.

Covers the initial commit content (whitelist + tracked profile, ignored
dummies absent), the ``Spt-Version`` trailer (and its ``unknown`` fallback),
refusing a second ``init``, and reporting pre-existing mod content instead
of silently ignoring it.
"""

from pathlib import Path

import pytest

from sptmod.core import services
from sptmod.core.errors import GitError
from sptmod.core.repo import Repo


def test_init_commits_whitelist_and_tracked_profile_only(fake_game_dir: Path) -> None:
    result = services.init_game_dir(fake_game_dir)

    committed = {f.path for f in Repo(fake_game_dir).show_files(result.commit)}
    assert committed == {
        Path(".gitignore"),
        Path("SPT/user/profiles/abc.json"),
    }


def test_init_ignored_dummies_are_absent_from_the_commit(fake_game_dir: Path) -> None:
    result = services.init_game_dir(fake_game_dir)

    committed = {f.path for f in Repo(fake_game_dir).show_files(result.commit)}
    ignored = {
        Path("SPT/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT/user/profiles/backups/old.json"),
        Path("SPT/SPT_Data/configs/core.json"),
    }
    assert committed.isdisjoint(ignored)


def test_init_reports_the_spt_version_read_from_core_json(fake_game_dir: Path) -> None:
    result = services.init_game_dir(fake_game_dir)

    assert result.spt_version == "4.1.0"


def test_init_commit_subject_and_trailer_carry_the_spt_version(
    fake_game_dir: Path,
) -> None:
    services.init_game_dir(fake_game_dir)

    commit = Repo(fake_game_dir).log()[0]
    assert commit.subject == "chore: clean SPT 4.1.0"
    assert commit.trailers.get("Spt-Version") == "4.1.0"


def test_init_falls_back_to_unknown_version_when_core_json_is_missing(
    fake_game_dir: Path,
) -> None:
    (fake_game_dir / "SPT" / "SPT_Data" / "configs" / "core.json").unlink()

    result = services.init_game_dir(fake_game_dir)

    assert result.spt_version == services.UNKNOWN_SPT_VERSION
    commit = Repo(fake_game_dir).log()[0]
    assert commit.subject == "chore: clean SPT unknown"
    assert commit.trailers.get("Spt-Version") == "unknown"


def test_init_returns_the_given_game_dir(fake_game_dir: Path) -> None:
    result = services.init_game_dir(fake_game_dir)

    assert result.game_dir == fake_game_dir


def test_init_on_clean_dir_reports_no_pre_existing_mods(fake_game_dir: Path) -> None:
    result = services.init_game_dir(fake_game_dir)

    assert result.pre_existing_mods == []


def test_init_twice_raises_git_error(fake_game_dir: Path) -> None:
    services.init_game_dir(fake_game_dir)

    with pytest.raises(GitError):
        services.init_game_dir(fake_game_dir)


def test_init_reports_pre_existing_mod_folder_and_loose_plugin(
    fake_game_dir: Path,
) -> None:
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / "OldServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "OldServerMod"}', encoding="utf-8")
    (mod_dir / "src.js").write_text("// old code", encoding="utf-8")
    (fake_game_dir / "BepInEx" / "plugins" / "OldPlugin.dll").write_bytes(b"old-dll")

    result = services.init_game_dir(fake_game_dir)

    assert set(result.pre_existing_mods) == {
        Path("SPT/user/mods/OldServerMod"),
        Path("BepInEx/plugins/OldPlugin.dll"),
    }


def test_init_with_pre_existing_mods_leaves_the_tree_dirty(
    fake_game_dir: Path,
) -> None:
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / "OldServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "OldServerMod"}', encoding="utf-8")

    services.init_game_dir(fake_game_dir)

    assert Repo(fake_game_dir).is_clean() is False


def test_init_with_pre_existing_mods_does_not_commit_them(
    fake_game_dir: Path,
) -> None:
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / "OldServerMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "OldServerMod"}', encoding="utf-8")

    result = services.init_game_dir(fake_game_dir)

    committed = {f.path for f in Repo(fake_game_dir).show_files(result.commit)}
    assert Path("SPT/user/mods/OldServerMod/package.json") not in committed
