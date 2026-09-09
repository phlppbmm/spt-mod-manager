"""Tests for ``sptmod.core.services.install_from_archive``.

Covers server-only, client-only and hybrid installs (canonical file
placement, complete and correct commit trailers, the returned ``ModInfo``),
name/version overrides, rejecting a dirty tree or a running game before any
change is made, securing game files an SPT update changed, the ordering and
monotonicity of the progress callback, and temp-directory cleanup on both
success and failure.
"""

import json
import zipfile
from pathlib import Path

import pytest

from conftest import clean_repo_at, legacy_whitelist_repo
from fixture_types import InstallCommit, MakeArchive
from sptmod.core import game_dir, services, workspace
from sptmod.core.errors import (
    ArchiveError,
    DirtyTreeError,
    GameRunningError,
    GitError,
    ModFileWriteError,
)
from sptmod.core.game_dir import SPT_VERSION_CONFIG_KEY, detect_layout
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo


def _tree_listing(root: Path) -> set[Path]:
    """A snapshot of every file below ``root`` (git internals excluded)."""
    return {p.relative_to(root) for p in root.rglob("*") if ".git" not in p.parts}


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


def test_server_only_install_places_the_file_at_the_canonical_path(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """A server-only archive (SPT/user/mods/ only) is extracted to the canonical
    SPT/user/mods/ directory, never to an alternate layout like SPT_Runtime/."""
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.2.3", "author": "Acme"})},
        "zip",
    )

    services.install_from_archive(clean_repo.path, archive)

    installed = clean_repo.path / "SPT/user/mods/CoolMod/package.json"
    assert installed.is_file()


def test_server_only_install_returns_mod_info_from_package_json(clean_repo: Repo, make_archive: MakeArchive) -> None:
    """install_from_archive returns ModInfo with fields read from package.json.

    Installing an archive with SPT/user/mods/CoolMod/package.json (name, version,
    author, sptVersion) returns mod_type='server', files list, and source=archive.name.
    """
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
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """The install commit carries every mod trailer with its value and empty Forge ids.

    The commit subject is "mod: CoolMod 1.2.3"; trailers include Mod, Mod-Version,
    Mod-Type, Mod-Author, Mod-SPT, Mod-Source, Mod-State; Forge-Id and Guid empty.
    """
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


def test_client_only_dll_install_names_the_mod_after_the_dll(clean_repo: Repo, make_archive: MakeArchive) -> None:
    """A BepInEx-only archive has mod_type='client'; mod name is the DLL stem.

    Installing BepInEx/plugins/CoolPlugin.dll returns mod_type='client',
    name='CoolPlugin' (from the DLL filename, not parent directory).
    """
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    installed = clean_repo.path / "BepInEx/plugins/CoolPlugin.dll"
    assert installed.read_bytes() == b"dll-bytes"
    assert result.mod.name == "CoolPlugin"
    assert result.mod.mod_type == "client"
    assert result.mod.source == archive.name
    assert set(result.files) == {Path("BepInEx/plugins/CoolPlugin.dll")}


def test_name_and_version_override_win_over_extracted_metadata(clean_repo: Repo, make_archive: MakeArchive) -> None:
    """name_override and version_override replace extracted metadata and commit subject.

    With name_override='RenamedMod' and version_override='9.9.9', the install returns
    name='RenamedMod' and version='9.9.9'; commit subject is "mod: RenamedMod 9.9.9".
    """
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.2.3"})},
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


def test_hybrid_install_copies_both_trees_and_reads_package_json(clean_repo: Repo, make_archive: MakeArchive) -> None:
    """A hybrid archive (both SPT/user/mods/ and BepInEx/plugins/) copies both trees.

    Installing an archive with both server (SPT/user/mods/CoolMod/package.json) and
    client (BepInEx/plugins/CoolPlugin.dll) returns mod_type='hybrid'; both trees copied.
    """
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0"}),
            "BepInEx/plugins/CoolPlugin.dll": b"dll-bytes",
        },
        "zip",
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.mod_type == "hybrid"
    assert result.mod.name == "CoolMod"
    assert result.mod.version == "1.0.0"
    assert (clean_repo.path / "SPT/user/mods/CoolMod/package.json").is_file()
    assert (clean_repo.path / "BepInEx/plugins/CoolPlugin.dll").read_bytes() == (b"dll-bytes")


def test_hybrid_install_into_a_runtime_layout_translates_only_the_server_half(
    fake_game_dir_runtime: Path, make_archive: MakeArchive
) -> None:
    """``_for_game_layout``/``_to_game_layout`` must translate the canonical
    ``SPT/user/mods/...`` server target to the detected ``SPT_Runtime``
    directory, while leaving the client ``BepInEx/plugins/...`` target
    untouched -- it is never relative to the canonical server prefix. If
    ``_to_game_layout``'s early `return target` for non-server paths were
    removed, this fails: the client file would be miscopied under a
    game-layout-prefixed path instead of straight to BepInEx/plugins."""
    repo = Repo.init(detect_layout(fake_game_dir_runtime))
    repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    archive = make_archive(
        {
            "SPT/user/mods/HybridMod/package.json": json.dumps({"name": "HybridMod", "version": "1.0.0"}),
            "BepInEx/plugins/HybridMod.dll": b"client-dll-bytes",
        },
        "zip",
    )

    result = services.install_from_archive(fake_game_dir_runtime, archive)

    assert result.mod.mod_type == "hybrid"
    assert (fake_game_dir_runtime / "SPT_Runtime/user/mods/HybridMod/package.json").is_file()
    assert (fake_game_dir_runtime / "BepInEx/plugins/HybridMod.dll").read_bytes() == b"client-dll-bytes"


def test_install_on_dirty_tree_raises_before_any_change(initialized_repo: Repo, make_archive: MakeArchive) -> None:
    """install_from_archive raises DirtyTreeError before extracting files or committing.

    When the repo has untracked profile files, install_from_archive raises DirtyTreeError
    without creating BepInEx/plugins/CoolPlugin.dll or adding to the commit log.
    """
    # initialized_repo has no baseline commit yet, so the tracked profile is
    # still untracked -- the tree is dirty from the very start.
    assert initialized_repo.is_clean() is False
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    with pytest.raises(DirtyTreeError):
        services.install_from_archive(initialized_repo.path, archive)

    assert not (initialized_repo.path / "BepInEx/plugins/CoolPlugin.dll").exists()
    assert initialized_repo.log() == []


def test_install_with_game_running_raises_before_any_change(
    clean_repo: Repo, make_archive: MakeArchive, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install_from_archive raises GameRunningError before extracting files or committing.

    When running_game_processes() returns ['EscapeFromTarkov.exe'], install_from_archive
    raises GameRunningError without creating the mod file or adding to the commit log.
    """
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"])
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    commits_before = len(clean_repo.log())

    with pytest.raises(GameRunningError):
        services.install_from_archive(clean_repo.path, archive)

    assert not (clean_repo.path / "BepInEx/plugins/CoolPlugin.dll").exists()
    assert len(clean_repo.log()) == commits_before


def test_install_reports_progress_steps_in_order_with_monotonic_percent(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """install_from_archive progress callback reports steps in order and percent monotonic.

    The progress callback is invoked with all INSTALL_STEPS in order; percents
    are sorted and bounded [0, 100].
    """
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0"}),
            "BepInEx/plugins/CoolPlugin.dll": b"dll-bytes",
        },
        "zip",
    )
    calls: list[tuple[str, int, str]] = []

    services.install_from_archive(
        clean_repo.path,
        archive,
        progress=lambda step, percent, message: calls.append((step, percent, message)),
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
    clean_repo: Repo,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """install_from_archive cleans up the staging directory after a successful install.

    After a successful install, workspace.staging_dir() is empty (no leftover files).
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    services.install_from_archive(clean_repo.path, archive)

    assert not list(workspace.staging_dir().iterdir())


def test_install_rolls_back_copied_files_when_the_commit_is_rejected(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """install_from_archive deletes the copied files when Repo.commit raises GitError.

    The commit happens after copying files. If it fails (e.g., due to invalid
    metadata), the copied files must be removed and the tree left clean.
    """
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


def test_install_rolls_back_copied_files_when_source_trailer_is_rejected(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """Unlike the ``author``-newline test above -- caught by
    _reject_multiline_metadata inside _read_meta, before anything is copied
    -- ``source`` is a separate install_from_archive parameter that
    _reject_multiline_metadata never inspects, so a newline in it only
    surfaces once Repo.commit validates the built trailers, *after* the
    anchored files were already copied. If the `except Exception:
    _remove_copied(path, added, game_layout); raise` in install_from_archive
    were removed, this fails: the copied package.json would survive the
    raised GitError and the tree would be left dirty."""
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0", "author": "Acme"})},
        "zip",
    )

    with pytest.raises(GitError):
        services.install_from_archive(clean_repo.path, archive, source="evil\nsource")

    assert not (clean_repo.path / "SPT/user/mods/CoolMod").exists()
    assert clean_repo.is_clean() is True


def test_install_rejects_an_archive_whose_mod_folder_holds_no_file(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """A trusted anchor whose mod folder is an empty directory carries no file
    to install, so the anchor step rejects it in plain language before
    anything is copied: the game directory keeps no orphaned mod folder and
    the tree stays clean. ``name_override``/``version_override`` would
    otherwise carry such an archive past the metadata step."""
    archive = make_archive({"SPT/user/mods/EmptyMod/": b""}, "zip")

    with pytest.raises(ArchiveError) as error:
        services.install_from_archive(
            clean_repo.path,
            archive,
            name_override="EmptyMod",
            version_override="1.0.0",
        )

    assert str(error.value) == "The archive contains no mod files."
    assert not (clean_repo.path / "SPT/user/mods/EmptyMod").exists()
    assert clean_repo.is_clean() is True


def test_install_cleans_up_the_temp_dir_after_failure(
    clean_repo: Repo,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """install_from_archive cleans up the staging directory even when it raises ArchiveError.

    When anchor search fails (archive has no mod anchor), staging_dir() is empty and the
    game directory unchanged.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # No SPT/user/mods or BepInEx/plugins anchor in this archive: extraction
    # succeeds but the anchor-search step fails.
    archive = make_archive({"Readme.txt": "please read me"}, "zip")
    before = _tree_listing(clean_repo.path)

    with pytest.raises(ArchiveError):
        services.install_from_archive(clean_repo.path, archive)

    assert not list(workspace.staging_dir().iterdir())
    assert _tree_listing(clean_repo.path) == before
    assert clean_repo.is_clean() is True


def test_install_leaves_the_game_dir_untouched_when_extraction_fails_partway(
    clean_repo: Repo,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A traversal member fails inside ``extract()`` itself (before the
    anchor step even runs), proving staging -- not the game dir -- takes the
    partial write of a failing extraction.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = make_archive({"../evil.txt": "pwned"}, "zip")
    before = _tree_listing(clean_repo.path)

    with pytest.raises(ArchiveError):
        services.install_from_archive(clean_repo.path, archive)

    assert _tree_listing(clean_repo.path) == before
    assert clean_repo.is_clean() is True
    assert not list(workspace.staging_dir().iterdir())


# --- real-corpus regressions: SPT_Runtime prefix, hybrid-half, patchers, ----
# --- multi-DLL name heuristic (spt-mod-manager-r3a) -------------------------


def test_hybrid_real_corpus_shape_never_silently_drops_the_server_half(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """Real-corpus shape (SAIN, Tyfon-UIFixes, WTT-CommonLib): a server mod
    folder below ``SPT_Runtime/user/mods/`` (no ``package.json``) next to a
    ``BepInEx/plugins`` client tree. The install types the mod ``hybrid``
    and copies both trees; it must never silently install only the client
    half.
    """
    archive = make_archive(
        {
            "SPT_Runtime/user/mods/ServerPart/ServerPart.dll": b"server-dll",
            "BepInEx/plugins/ClientPart/ClientPart.dll": b"client-dll",
        },
        "zip",
    )

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.mod_type == "hybrid"
    assert (clean_repo.path / "SPT/user/mods/ServerPart/ServerPart.dll").is_file()
    assert (clean_repo.path / "BepInEx/plugins/ClientPart/ClientPart.dll").is_file()


def test_pure_server_real_corpus_shape_without_package_json_installs(
    clean_repo: Repo, make_archive: MakeArchive
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


def test_server_mod_without_package_json_or_dll_or_override_raises_invalid_metadata(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """Same package-json-less server shape as the betterkeys test above, but
    with no DLL either and no name/version override to fall back on: nothing
    can identify the mod. If the `if not (name_override or meta.name): raise
    InvalidMetadataError(...)` guard in _read_meta were removed, this fails:
    the mod would be installed with an empty name instead of being rejected."""
    archive = make_archive(
        {"SPT/user/mods/UnknownMod/readme.txt": "just a readme, no metadata"},
        "zip",
    )

    with pytest.raises(InvalidMetadataError):
        services.install_from_archive(clean_repo.path, archive)

    assert not (clean_repo.path / "SPT/user/mods/UnknownMod").exists()


@pytest.mark.parametrize(
    ("archive_name", "files", "expected_name"),
    [
        pytest.param(
            "SAIN.zip",
            {
                "BepInEx/plugins/SAIN/SAIN.dll": b"main-dll",
                "BepInEx/plugins/SAIN/SAIN.Preset.Shared.dll": b"preset-dll",
            },
            "SAIN",
            # Real-corpus shape (SAIN.4.5.0.zip): the derived name must be the
            # archive stem "SAIN", not the alphabetically first DLL
            # ("SAIN.Preset.Shared").
            id="sain-real-corpus",
        ),
        pytest.param(
            "Tyfon-UIFixes-6.0.2.zip",
            {
                "BepInEx/plugins/Tyfon.UIFixes.dll": b"main-dll",
                "BepInEx/plugins/Tyfon.UIFixes.Net.dll": b"net-dll",
            },
            "UIFixes",
            # Real-corpus shape (Tyfon-UIFixes-6.0.2.zip): the archive-stem
            # match must resolve to "UIFixes", not the alphabetically first
            # DLL ("Tyfon.UIFixes.Net").
            id="tyfon-real-corpus",
        ),
    ],
)
def test_multi_dll_client_install_prefers_the_dll_matching_the_archive_stem(
    archive_name: str,
    files: dict[str, str | bytes],
    expected_name: str,
    clean_repo: Repo,
    tmp_path: Path,
) -> None:
    """The mod name comes from the DLL matching the archive stem, e.g. "UIFixes".

    When an archive contains multiple client DLLs (e.g., SAIN.dll and
    SAIN.Preset.Shared.dll), the mod name must be derived from the DLL whose
    stem matches the archive filename, not from the alphabetically first DLL.
    """
    archive = _named_zip(tmp_path, archive_name, files)

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == expected_name


def test_install_auto_snapshots_changed_profiles_instead_of_failing(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """Played-with profiles never block an install; they are snapshotted."""
    profile = clean_repo.path / "SPT/user/profiles/abc.json"
    profile.write_text(profile.read_text() + "\n")
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "CoolPlugin"
    subjects = [commit.subject for commit in clean_repo.log()]
    assert subjects[0].startswith("mod: CoolPlugin")
    assert subjects[1].startswith("snapshot:")
    assert clean_repo.is_clean()


def test_install_blocks_on_unmanaged_changes_in_plain_language(
    clean_repo: Repo, install_commit: InstallCommit, make_archive: MakeArchive
) -> None:
    """A hand-edited ModA.dll makes install_from_archive raise DirtyTreeError.

    If a tracked file (part of an installed mod) has been manually edited and
    the tree is dirty, install_from_archive must reject it with a clear error
    message that explains the conflict in user-friendly language.
    """
    install_commit(clean_repo, "ModA", "1.0.0")
    (clean_repo.path / "BepInEx/plugins/ModA.dll").write_bytes(b"tampered")
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    with pytest.raises(DirtyTreeError) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    message = str(excinfo.value)
    assert "does not manage" in message
    assert "uncommitted" not in message


def _seed_game_file(repo: Repo, relpath: str) -> Path:
    """Commit ``relpath`` as part of the game baseline and return its full path.

    Mimics a file the SPT installer shipped and the init commit (or a later
    game-files snapshot) recorded: tracked, but carrying no ``Mod:`` trailer.
    """
    full = repo.path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"4.1.0-build")
    repo.commit("chore: seed SPT client files", paths=[Path(relpath)])
    return full


@pytest.mark.parametrize(
    "delete",
    [pytest.param(False, id="modified"), pytest.param(True, id="deleted")],
)
def test_install_secures_changed_game_files_before_the_mod_commit(
    clean_repo: Repo, make_archive: MakeArchive, delete: bool
) -> None:
    """An SPT update to a tracked game file never blocks an install; it is secured first.

    BepInEx/plugins/spt/spt-common.dll is part of the game baseline and is then overwritten
    or deleted; install_from_archive must succeed, put a 'snapshot: game files updated'
    commit right before the mod commit in history() and leave the tree clean. This is the
    bug where an SPT update made every install and update fail as unmanaged files.
    """
    spt_dll = _seed_game_file(clean_repo, "BepInEx/plugins/spt/spt-common.dll")
    if delete:
        spt_dll.unlink()
    else:
        spt_dll.write_bytes(b"4.2.0-build")
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.name == "CoolPlugin"
    subjects = [commit.subject for commit in services.history(clean_repo.path)]
    assert subjects[0].startswith("mod: CoolPlugin")
    assert subjects[1] == "snapshot: game files updated"
    assert not services.status(clean_repo.path).game_files_changed
    assert clean_repo.is_clean()


def test_install_secures_the_program_files_an_older_repo_never_tracked(
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """Untracked SPT program files never block an install; they are secured first.

    A repository created before the whitelist covered the program set has the
    server executable and the SPT_Data tree untracked; installing a mod must
    sweep them into a 'snapshot: game files updated' commit instead of
    rejecting them as files sptmod does not manage.
    """
    program_files = {Path("SPT/SPT.Server.exe"), Path("SPT/SPT_Data/database/locales/en.json")}
    for relative in program_files:
        (fake_game_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (fake_game_dir / relative).write_bytes(b"4.1.0-build")
    repo = legacy_whitelist_repo(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(fake_game_dir, archive)

    assert result.mod.name == "CoolPlugin"
    history = services.history(fake_game_dir)
    assert history[0].subject.startswith("mod: CoolPlugin")
    assert history[1].subject == "snapshot: game files updated"
    assert program_files <= {mod_file.path for mod_file in repo.show_files(history[1].hash)}
    assert repo.is_clean()


def test_install_stores_the_spt_version_the_updated_game_files_report(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """After securing changed game files, install stores the newly detected SPT version.

    The stored version says 4.1.0 while core.json now says 4.2.0, as after an SPT update;
    installing a mod on top of a changed game file must leave both the stored version and
    status().spt_version at 4.2.0. This keeps Forge queries from asking for the version the
    installation had before the update.
    """
    services.set_spt_version(clean_repo.path, "4.1.0")
    core_config = clean_repo.path / "SPT/SPT_Data/configs/core.json"
    core_config.write_text(json.dumps({"sptVersion": "4.2.0"}), encoding="utf-8")
    _seed_game_file(clean_repo, "BepInEx/plugins/spt/spt-common.dll").write_bytes(b"4.2.0-build")
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    services.install_from_archive(clean_repo.path, archive)

    assert clean_repo.config_get(SPT_VERSION_CONFIG_KEY) == "4.2.0"
    assert services.status(clean_repo.path).spt_version == "4.2.0"


def test_install_leaves_the_spt_version_unset_when_the_game_dir_reports_none(
    fake_game_dir_runtime: Path, make_archive: MakeArchive
) -> None:
    """Securing changed game files stores no version when none can be detected.

    fake_game_dir_runtime has neither a sptVersion in core.json nor a SPT.Server.exe nor a
    stored version, so nothing resolves; installing on top of a changed game file must still
    succeed and leave the stored version unset instead of writing an empty value.
    """
    repo = clean_repo_at(fake_game_dir_runtime)
    _seed_game_file(repo, "BepInEx/plugins/spt/spt-common.dll").write_bytes(b"newer-build")
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    result = services.install_from_archive(repo.path, archive)

    assert result.mod.name == "CoolPlugin"
    assert repo.config_get(SPT_VERSION_CONFIG_KEY) is None


def test_install_blocks_on_untracked_files_without_naming_changed_game_files(
    clean_repo: Repo, make_archive: MakeArchive
) -> None:
    """A hand-copied mod still blocks, and the message names only the untracked files.

    A changed game file and an untracked SPT/user/mods/HandCopied folder exist at the same
    time; install_from_archive must raise DirtyTreeError naming the hand-copied folder and
    the adopt hint, while the changed game file appears nowhere in the message. This keeps
    the plain-language error truthful: only untracked files are unmanaged.
    """
    _seed_game_file(clean_repo, "BepInEx/plugins/spt/spt-common.dll").write_bytes(b"4.2.0-build")
    manual = clean_repo.path / "SPT/user/mods/HandCopied"
    manual.mkdir(parents=True)
    (manual / "package.json").write_text('{"name": "HandCopied", "version": "1.0.0"}', encoding="utf-8")
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")

    with pytest.raises(DirtyTreeError) as excinfo:
        services.install_from_archive(clean_repo.path, archive)

    message = str(excinfo.value)
    assert "SPT/user/mods/HandCopied" in message
    assert "adopt" in message
    assert "spt-common.dll" not in message


@pytest.mark.parametrize(
    ("blocked", "expected_file"),
    [
        pytest.param(
            "SPT/user/mods",
            "SPT/user/mods/CoolMod/package.json",
            id="a mods folder that cannot take a new mod directory",
        ),
        pytest.param(
            "BepInEx/plugins",
            "BepInEx/plugins/CoolPlugin.dll",
            id="a plugins folder that cannot take a new file",
        ),
    ],
)
def test_install_says_which_file_it_could_not_write_and_where_the_previous_ones_are(
    clean_repo: Repo, make_archive: MakeArchive, blocked: str, expected_file: str
) -> None:
    """install_from_archive turns a refused write into a plain ModFileWriteError.

    The game directory is made unwritable at one place -- the folder that has to take the
    new mod directory and the folder that has to take a new plugin file -- so copying the
    hybrid archive stops there. The message has to name that file and what the system said
    instead of letting the raw operating system error reach the caller, and nothing may be
    committed.
    """
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.2.3"}),
            "BepInEx/plugins/CoolPlugin.dll": b"dll-bytes",
        },
        "zip",
    )
    obstacle = clean_repo.path / blocked
    obstacle.chmod(0o555)

    try:
        with pytest.raises(ModFileWriteError) as failure:
            services.install_from_archive(clean_repo.path, archive)
    finally:
        obstacle.chmod(0o755)

    assert str(failure.value) == (
        "The mod files could not be written: Permission denied. The change stopped at "
        f"{expected_file}; your previous files are one snapshot back."
    )
    assert [commit.subject for commit in clean_repo.log()] == ["chore: clean SPT 4.1.0"]
