"""Tests for ``sptmod.core.services.install_from_archive``.

Covers server-only, client-only and hybrid installs (canonical file
placement, complete and correct commit trailers, the returned ``ModInfo``),
name/version overrides, rejecting a dirty tree or a running game before any
change is made, the ordering and monotonicity of the progress callback, and
temp-directory cleanup on both success and failure.
"""

import json
import tempfile
from pathlib import Path

import pytest

from sptmod.core import game_dir, services
from sptmod.core.errors import (
    ArchiveError,
    DirtyTreeError,
    GameRunningError,
    GitError,
)
from sptmod.core.models import MOD_TRAILER_KEYS
from sptmod.core.repo import Repo


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _trailers(repo: Repo) -> dict[str, str]:
    return repo.log()[0].trailers


def test_server_only_install_places_the_file_at_the_canonical_path(
    clean_repo: Repo, make_archive
) -> None:
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.2.3", "author": "Acme"}
            )
        },
        "zip",
    )

    services.install_from_archive(clean_repo.path, archive)

    installed = clean_repo.path / "SPT/user/mods/CoolMod/package.json"
    assert installed.is_file()


def test_server_only_install_returns_mod_info_from_package_json(
    clean_repo: Repo, make_archive
) -> None:
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

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "CoolMod"
    assert result.mod.version == "1.2.3"
    assert result.mod.mod_type == "server"
    assert result.mod.author == "Acme"
    assert result.mod.spt_range == "~4.1.0"
    assert result.mod.state == "installed"
    assert result.mod.source == archive.name
    assert set(result.files) == {Path("SPT/user/mods/CoolMod/package.json")}


def test_server_only_install_commit_has_complete_and_correct_trailers(
    clean_repo: Repo, make_archive
) -> None:
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

    services.install_from_archive(clean_repo.path, archive)

    commit = clean_repo.log()[0]
    assert commit.subject == "mod: CoolMod 1.2.3"
    assert set(MOD_TRAILER_KEYS).issubset(commit.trailers.keys())
    assert commit.trailers["Mod"] == "CoolMod"
    assert commit.trailers["Mod-Version"] == "1.2.3"
    assert commit.trailers["Mod-Type"] == "server"
    assert commit.trailers["Mod-Author"] == "Acme"
    assert commit.trailers["Mod-SPT"] == "~4.1.0"
    assert commit.trailers["Mod-Source"] == archive.name
    assert commit.trailers["Mod-State"] == "installed"
    assert commit.trailers["Mod-Forge-Id"] == ""
    assert commit.trailers["Mod-Forge-Version-Id"] == ""
    assert commit.trailers["Mod-Guid"] == ""


def test_client_only_dll_install_names_the_mod_after_the_dll(
    clean_repo: Repo, make_archive
) -> None:
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    installed = clean_repo.path / "BepInEx/plugins/CoolPlugin.dll"
    assert installed.read_bytes() == b"dll-bytes"
    assert result.mod.name == "CoolPlugin"
    assert result.mod.mod_type == "client"
    assert result.mod.source == archive.name
    assert set(result.files) == {Path("BepInEx/plugins/CoolPlugin.dll")}


def test_name_and_version_override_win_over_extracted_metadata(
    clean_repo: Repo, make_archive
) -> None:
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.2.3"}
            )
        },
        "zip",
    )

    result = services.install_from_archive(
        clean_repo.path,
        archive,
        name_override="RenamedMod",
        version_override="9.9.9",
    )

    assert result.mod.name == "RenamedMod"
    assert result.mod.version == "9.9.9"
    commit = clean_repo.log()[0]
    assert commit.subject == "mod: RenamedMod 9.9.9"
    assert commit.trailers["Mod"] == "RenamedMod"
    assert commit.trailers["Mod-Version"] == "9.9.9"


def test_hybrid_install_copies_both_trees_and_reads_package_json(
    clean_repo: Repo, make_archive
) -> None:
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0"}
            ),
            "BepInEx/plugins/CoolPlugin.dll": b"dll-bytes",
        },
        "zip",
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.mod_type == "hybrid"
    assert result.mod.name == "CoolMod"
    assert result.mod.version == "1.0.0"
    assert (clean_repo.path / "SPT/user/mods/CoolMod/package.json").is_file()
    assert (clean_repo.path / "BepInEx/plugins/CoolPlugin.dll").read_bytes() == (
        b"dll-bytes"
    )
    assert set(result.files) == {
        Path("SPT/user/mods/CoolMod/package.json"),
        Path("BepInEx/plugins/CoolPlugin.dll"),
    }


def test_install_on_dirty_tree_raises_before_any_change(
    initialized_repo: Repo, make_archive
) -> None:
    # initialized_repo has no baseline commit yet, so the tracked profile is
    # still untracked -- the tree is dirty from the very start.
    assert initialized_repo.is_clean() is False
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    with pytest.raises(DirtyTreeError):
        services.install_from_archive(initialized_repo.path, archive)

    assert not (initialized_repo.path / "BepInEx/plugins/CoolPlugin.dll").exists()
    assert initialized_repo.log() == []


def test_install_with_game_running_raises_before_any_change(
    clean_repo: Repo, make_archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"]
    )
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    commits_before = len(clean_repo.log())

    with pytest.raises(GameRunningError):
        services.install_from_archive(clean_repo.path, archive)

    assert not (clean_repo.path / "BepInEx/plugins/CoolPlugin.dll").exists()
    assert len(clean_repo.log()) == commits_before


def test_install_reports_progress_steps_in_order_with_monotonic_percent(
    clean_repo: Repo, make_archive
) -> None:
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0"}
            ),
            "BepInEx/plugins/CoolPlugin.dll": b"dll-bytes",
        },
        "zip",
    )
    calls: list[tuple[str, int, str]] = []

    services.install_from_archive(
        clean_repo.path,
        archive,
        progress=lambda step, percent, message: calls.append(
            (step, percent, message)
        ),
    )

    assert calls, "progress callback was never invoked"
    steps_seen = [step for step, _percent, _message in calls]
    assert set(steps_seen).issubset(set(services.INSTALL_STEPS))
    first_occurrence_order = list(dict.fromkeys(steps_seen))
    assert first_occurrence_order == list(services.INSTALL_STEPS)

    percents = [percent for _step, percent, _message in calls]
    assert percents == sorted(percents)
    assert all(0 <= percent <= 100 for percent in percents)


def test_install_cleans_up_the_temp_dir_after_success(
    clean_repo: Repo, make_archive, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_system_temp = tmp_path / "system-temp"
    fake_system_temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_system_temp))
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    services.install_from_archive(clean_repo.path, archive)

    assert list(fake_system_temp.iterdir()) == []


def test_install_rolls_back_copied_files_when_the_commit_is_rejected(
    clean_repo: Repo, make_archive
) -> None:
    # A newline in a trailer value is rejected by Repo.commit itself, but
    # only *after* install_from_archive has already copied the anchored
    # files into the game directory (the commit happens as the last step).
    # A correct implementation validates trailers, or rolls the copy back,
    # before leaving the game directory in a half-installed, dirty state.
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "Evil\nAuthor"}
            )
        },
        "zip",
    )

    with pytest.raises(GitError):
        services.install_from_archive(clean_repo.path, archive)

    assert not (clean_repo.path / "SPT/user/mods/CoolMod/package.json").exists()
    assert clean_repo.is_clean() is True


def test_install_cleans_up_the_temp_dir_after_failure(
    clean_repo: Repo, make_archive, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_system_temp = tmp_path / "system-temp"
    fake_system_temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_system_temp))
    # No SPT/user/mods or BepInEx/plugins anchor in this archive: extraction
    # succeeds but the anchor-search step fails.
    archive = make_archive({"Readme.txt": "please read me"}, "zip")

    with pytest.raises(ArchiveError):
        services.install_from_archive(clean_repo.path, archive)

    assert list(fake_system_temp.iterdir()) == []
