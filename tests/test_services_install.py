"""Tests for ``sptmod.core.services.install_from_archive``.

Covers server-only, client-only and hybrid installs (canonical file
placement, complete and correct commit trailers, the returned ``ModInfo``),
name/version overrides, rejecting a dirty tree or a running game before any
change is made, the ordering and monotonicity of the progress callback, and
temp-directory cleanup on both success and failure.
"""

import json
import tempfile
import zipfile
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


def _named_zip(tmp_path: Path, name: str, files: dict[str, str | bytes]) -> Path:
    """Build a real zip archive with a caller-chosen file name.

    Unlike the ``make_archive`` fixture (fixed ``archive-N.<fmt>`` naming),
    this lets a test control the archive's stem, which the multi-DLL name
    heuristic must key off.
    """
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w") as zf:
        for relpath, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(relpath, data)
    return archive_path


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


# --- real-corpus regressions: SPT_Runtime prefix, hybrid-half, patchers, ----
# --- multi-DLL name heuristic (spt-mod-manager-r3a) -------------------------


def test_hybrid_real_corpus_shape_never_silently_drops_the_server_half(
    clean_repo: Repo, make_archive
) -> None:
    """Real-corpus shape (SAIN, Tyfon-UIFixes, WTT-CommonLib): a server mod
    folder below ``SPT_Runtime/user/mods/`` (no ``package.json``) next to a
    ``BepInEx/plugins`` client tree. The install must either type the mod
    ``hybrid`` and copy both trees, or fail outright -- it must never silently
    install only the client half.
    """
    archive = make_archive(
        {
            "SPT_Runtime/user/mods/ServerPart/ServerPart.dll": b"server-dll",
            "BepInEx/plugins/ClientPart/ClientPart.dll": b"client-dll",
        },
        "zip",
    )

    try:
        result = services.install_from_archive(clean_repo.path, archive)
    except ArchiveError:
        return

    assert result.mod.mod_type == "hybrid"
    assert (clean_repo.path / "SPT/user/mods/ServerPart/ServerPart.dll").is_file()
    assert (clean_repo.path / "BepInEx/plugins/ClientPart/ClientPart.dll").is_file()


def test_pure_server_real_corpus_shape_without_package_json_installs(
    clean_repo: Repo, make_archive
) -> None:
    """Real-corpus shape (betterkeys, EasyAmmoNames): a server mod archive
    prefixed ``SPT_Runtime/user/mods/`` whose mod folder ships no
    ``package.json`` at all -- just a DLL and data files. ``--name``/
    ``--version`` (``name_override``/``version_override``) supply what the
    metadata cannot.

    This is a second, independent blocker behind the anchor bug: even once
    ``find_anchor`` recognizes the ``SPT_Runtime`` shape, ``_read_meta``
    still routes every non-client mod through ``read_package_json``, which
    raises when no child directory carries a ``package.json`` -- there is no
    fallback to the overrides for a package-json-less server mod.
    """
    archive = make_archive(
        {
            "SPT_Runtime/user/mods/betterkeys/betterkeys.dll": b"server-dll",
            "SPT_Runtime/user/mods/betterkeys/config/config.jsonc": "{}",
        },
        "zip",
    )

    result = services.install_from_archive(
        clean_repo.path,
        archive,
        name_override="betterkeys",
        version_override="1.8.0",
    )

    assert result.mod.mod_type == "server"
    assert result.mod.name == "betterkeys"
    assert (clean_repo.path / "SPT/user/mods/betterkeys/betterkeys.dll").is_file()


def test_multi_dll_client_install_prefers_the_dll_matching_the_archive_stem(
    clean_repo: Repo, tmp_path: Path
) -> None:
    """Real-corpus shape (SAIN.4.5.0.zip): two DLLs ship in the plugin folder,
    ``SAIN.dll`` and ``SAIN.Preset.Shared.dll``. The archive stem is ``SAIN``,
    so the derived mod name must be ``SAIN`` -- not whichever DLL happens to
    sort first alphabetically (currently ``SAIN.Preset.Shared``).
    """
    archive = _named_zip(
        tmp_path,
        "SAIN.zip",
        {
            "BepInEx/plugins/SAIN/SAIN.dll": b"main-dll",
            "BepInEx/plugins/SAIN/SAIN.Preset.Shared.dll": b"preset-dll",
        },
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "SAIN"


def test_multi_dll_client_install_prefers_the_dll_matching_the_archive_stem_tyfon(
    clean_repo: Repo, tmp_path: Path
) -> None:
    """Real-corpus shape (Tyfon-UIFixes-6.0.2.zip): ``Tyfon.UIFixes.dll`` and
    ``Tyfon.UIFixes.Net.dll`` ship side by side. Per the task's acceptance
    criteria the archive-stem match must resolve the mod name to
    ``UIFixes`` (currently ``Tyfon.UIFixes.Net``, the alphabetically-first
    DLL).
    """
    archive = _named_zip(
        tmp_path,
        "Tyfon-UIFixes-6.0.2.zip",
        {
            "BepInEx/plugins/Tyfon.UIFixes.dll": b"main-dll",
            "BepInEx/plugins/Tyfon.UIFixes.Net.dll": b"net-dll",
        },
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "UIFixes"
