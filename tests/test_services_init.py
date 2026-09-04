"""Tests for ``sptmod.core.services.init_game_dir``.

Covers the initial commit content (whitelist + tracked profile, ignored
dummies absent), the ``Spt-Version`` trailer, refusing a second ``init`` on a
repo with commits, and reporting pre-existing mod content instead of
silently ignoring it.

CONTRACT CHANGE (spt-mod-manager init-deadlock fix): an undetectable SPT
version no longer falls back to ``UNKNOWN_SPT_VERSION``; ``init_game_dir``
now raises ``SptVersionUnknownError`` before writing anything, so the caller
can ask for a version and retry via the ``spt_version`` argument.
"""

from pathlib import Path

import pytest

from sptmod.core import services
from sptmod.core.errors import GitError, SptVersionUnknownError
from sptmod.core.game_dir import detect_layout
from sptmod.core.modmeta import InvalidMetadataError
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


def test_init_raises_spt_version_unknown_and_leaves_no_git_when_core_json_is_missing(
    fake_game_dir: Path,
) -> None:
    (fake_game_dir / "SPT" / "SPT_Data" / "configs" / "core.json").unlink()

    with pytest.raises(SptVersionUnknownError):
        services.init_game_dir(fake_game_dir)

    assert not (fake_game_dir / ".git").exists()


def test_init_with_an_explicit_spt_version_succeeds_when_core_json_is_missing(
    fake_game_dir: Path,
) -> None:
    (fake_game_dir / "SPT" / "SPT_Data" / "configs" / "core.json").unlink()

    result = services.init_game_dir(fake_game_dir, spt_version="4.1.2")

    assert result.spt_version == "4.1.2"
    commit = Repo(fake_game_dir).log()[0]
    assert commit.subject == "chore: clean SPT 4.1.2"
    assert commit.trailers.get("Spt-Version") == "4.1.2"


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


def test_init_adopt_true_rolls_back_the_created_repo_on_an_adoption_failure(
    fake_game_dir: Path,
) -> None:
    """A pre-existing mod with no usable metadata makes the initial commit's
    adopt step fail; init_game_dir must undo the .git it created rather than
    leaving a half-initialized repository behind. If the rollback in
    init_game_dir's `except Exception:` block were removed, this fails: the
    .git directory would still exist after the raised error."""
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / "BadMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text("{}", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        services.init_game_dir(fake_game_dir, adopt=True)

    assert not (fake_game_dir / ".git").exists()


def test_init_adopt_true_keeps_a_pre_existing_git_dir_it_did_not_create(
    fake_game_dir: Path,
) -> None:
    """A repository without any commit, left behind by an earlier interrupted
    init, is completed rather than refused (per _open_for_init); when that
    completion still fails, the rollback must not remove a .git it did not
    create itself. If `if created:` were replaced with an unconditional
    rmtree, this fails: the pre-existing .git would be deleted too."""
    Repo.init(detect_layout(fake_game_dir))
    assert (fake_game_dir / ".git").exists()
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / "BadMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text("{}", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        services.init_game_dir(fake_game_dir, adopt=True)

    assert (fake_game_dir / ".git").exists()
