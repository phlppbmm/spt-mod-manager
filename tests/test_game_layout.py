"""Tests for SPT 4.x layout support (spt-mod-manager-fyx).

Covers ``GameLayout``/``detect_layout`` (SPT_Runtime vs SPT, both-present
warning, the flat-3.x rejection, precise missing-directory naming), the
layout accessors, ``is_game_program_path``, ``read_client_build``, and an
end-to-end pass over the ``SPT_Runtime`` layout (init whitelist,
archive-prefix translation on install, status, list_mods, check_profiles).
The PE parser lives in :mod:`test_game_layout_pe`, the version chain in
:mod:`test_spt_version_chain`.
"""

import json
from pathlib import Path

import pytest

from fixture_types import InstallCommit, MakeArchive
from fixtures_profile import profile_with_item
from pe_builder import build_synthetic_exe_multi, write_server_exe
from sptmod.core import services
from sptmod.core.errors import NotAGameDirError
from sptmod.core.game_dir import (
    GameLayout,
    detect_layout,
    is_game_program_path,
    read_client_build,
)
from sptmod.core.repo import Repo

# ============================================================================
# detect_layout
# ============================================================================


@pytest.mark.parametrize(
    "runtime_dir_name",
    [
        pytest.param("SPT_Runtime", id="spt-runtime-4.1"),
        pytest.param("SPT", id="spt-4.0"),
    ],
)
def test_detect_layout_accepts_one_runtime_directory(tmp_path: Path, runtime_dir_name: str) -> None:
    """Either runtime directory alone is a valid, unambiguous game directory.

    Only SPT_Runtime (the 4.1+ layout) or only SPT (the 4.0 layout) exists next
    to BepInEx; detect_layout names that directory as the runtime and warns
    about nothing, because there is nothing to choose between.
    """
    root = tmp_path / "game"
    (root / runtime_dir_name).mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == runtime_dir_name
    assert layout.warning == ""


def test_detect_layout_prefers_runtime_over_spt_and_warns(tmp_path: Path) -> None:
    """When both SPT_Runtime and SPT exist, prefer SPT_Runtime with a warning.

    Creates directories for both runtime names; detect_layout() must return
    runtime_dir_name="SPT_Runtime" and set warning to a string mentioning
    both names.
    """
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT_Runtime"
    assert "SPT_Runtime" in layout.warning
    assert "SPT" in layout.warning


def test_detect_layout_raises_when_neither_runtime_dir_exists_and_names_both(
    tmp_path: Path,
) -> None:
    """Raise NotAGameDirError when both SPT_Runtime and SPT are missing.

    Creates only BepInEx; detect_layout() must raise NotAGameDirError with a
    message naming both missing runtime directory options.
    """
    root = tmp_path / "game"
    (root / "BepInEx").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    message = str(exc_info.value)
    assert "SPT_Runtime" in message
    assert "SPT" in message


def test_detect_layout_raises_when_bepinex_missing_and_names_it(
    tmp_path: Path,
) -> None:
    """Raise NotAGameDirError when BepInEx is missing, even with SPT_Runtime.

    Creates only SPT_Runtime; detect_layout() must raise NotAGameDirError with
    "BepInEx" in the error message.
    """
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    assert "BepInEx" in str(exc_info.value)


def test_detect_layout_rejects_a_flat_spt_3x_installation(tmp_path: Path) -> None:
    """SPT 3.x flat layout (SPT_Data/Server + user/mods) is explicitly rejected."""
    root = tmp_path / "game"
    (root / "BepInEx").mkdir(parents=True)
    (root / "SPT_Data" / "Server").mkdir(parents=True)
    (root / "user" / "mods").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    message = str(exc_info.value)
    assert "3.x" in message
    assert "SPT_Runtime" in message
    assert "SPT" in message


# ============================================================================
# GameLayout accessors
# ============================================================================


@pytest.mark.parametrize("runtime_dir_name", ["SPT_Runtime", "SPT"])
def test_game_layout_accessors_are_relative_to_the_runtime_dir(
    runtime_dir_name: str,
) -> None:
    """All GameLayout path properties resolve relative to the runtime directory.

    Parametrized over both "SPT_Runtime" and "SPT"; verifies that .runtime_dir,
    .mods_dir, .profiles_dir, .core_config_file, and .server_exe all use the
    runtime_dir_name, not hardcoded paths.
    """
    layout = GameLayout(root=Path("/game"), runtime_dir_name=runtime_dir_name)

    assert layout.runtime_dir == Path(runtime_dir_name)
    assert layout.mods_dir == Path(runtime_dir_name) / "user" / "mods"
    assert layout.profiles_dir == Path(runtime_dir_name) / "user" / "profiles"
    assert layout.profile_backups_dir == Path(runtime_dir_name) / "user" / "profiles" / "backups"
    assert layout.core_config_file == Path(runtime_dir_name) / "SPT_Data" / "configs" / "core.json"
    assert layout.server_exe == Path(runtime_dir_name) / "SPT.Server.exe"


# ============================================================================
# is_game_program_path
# ============================================================================

_PROGRAM_PATH_LAYOUT = GameLayout(root=Path("/game"), runtime_dir_name="SPT_Runtime")


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param("SPT_Runtime/SPT.Server.exe", True, id="runtime-dir-file"),
        pytest.param("SPT_Runtime/SPT_Data/checks.dat", True, id="spt-data-tree"),
        pytest.param("BepInEx/plugins/spt/x.dll", True, id="spt-own-plugin"),
        pytest.param("BepInEx/patchers/spt-prepatch.dll", True, id="spt-own-patcher"),
        pytest.param("BepInEx/core/BepInEx.dll", True, id="bepinex-core"),
        pytest.param("winhttp.dll", True, id="loader-winhttp"),
        pytest.param("doorstop_config.ini", True, id="loader-doorstop-config"),
        pytest.param(".doorstop_version", True, id="loader-doorstop-version"),
        pytest.param("LICENSE-BepInEx.txt", True, id="loader-license-glob"),
        pytest.param("WINHTTP.DLL", False, id="loader-file-other-spelling"),
        pytest.param("BepInEx/plugins/SomeMod.dll", False, id="ordinary-plugin"),
        pytest.param("SPT_Runtime/user/mods/Mod/package.json", False, id="mod-file"),
        pytest.param("nested/winhttp.dll", False, id="loader-file-not-in-root"),
        pytest.param("LICENSE.txt", False, id="license-without-version-suffix"),
        pytest.param("BepInEx/config/SomeMod.cfg", False, id="bepinex-config"),
    ],
)
def test_is_game_program_path_classifies_the_spt_program_set(entry: str, expected: bool) -> None:
    """is_game_program_path() distinguishes files an SPT release owns from mod files.

    Covers the runtime directory, SPT_Data, SPT's own BepInEx client files,
    the BepInEx runtime and the root-anchored loader files (case-insensitively),
    against ordinary mod paths and non-root-anchored or suffix-less lookalikes.
    """
    assert is_game_program_path(Path(entry), _PROGRAM_PATH_LAYOUT) is expected


# ============================================================================
# read_client_build
# ============================================================================


def test_read_client_build_prefers_product_version_over_file_version(
    tmp_path: Path,
) -> None:
    """When both version strings are present, ProductVersion wins for the
    client build -- the opposite preference of read_pe_file_version, which
    prefers FileVersion. FileVersion here would parse to a different build
    ('3'), proving the function reads the right key rather than falling
    through to the first one found.
    """
    exe = tmp_path / "EscapeFromTarkov.exe"
    exe.write_bytes(
        build_synthetic_exe_multi(
            [("FileVersion", "1.2.3.4"), ("ProductVersion", "0.16.9.5-40743.36018")]
        )
    )

    assert read_client_build(exe) == "40743"


@pytest.mark.parametrize(
    "version_key",
    [
        pytest.param("ProductVersion", id="product-version"),
        pytest.param("FileVersion", id="falls-back-to-file-version"),
    ],
)
def test_read_client_build_returns_the_second_to_last_dash_and_dot_component(
    tmp_path: Path, version_key: str
) -> None:
    """read_client_build() splits the version on both '.' and '-' and returns the
    second-to-last component: '0.16.9.5-40743.36018' is build '40743', the value
    SPT compares an installation's client build against. It reads ProductVersion,
    and FileVersion when the executable carries no ProductVersion.
    """
    exe = tmp_path / "EscapeFromTarkov.exe"
    exe.write_bytes(build_synthetic_exe_multi([(version_key, "0.16.9.5-40743.36018")]))

    assert read_client_build(exe) == "40743"


@pytest.mark.parametrize(
    "version",
    [
        pytest.param("40743", id="single-component"),
        pytest.param("0.16.9.5-abcd.36018", id="non-numeric-component"),
    ],
)
def test_read_client_build_empty_when_the_component_is_not_a_number(
    tmp_path: Path, version: str
) -> None:
    """read_client_build() returns "" when the version has fewer than two
    '.'/'-' separated components, or the second-to-last one is not a number.
    """
    exe = tmp_path / "EscapeFromTarkov.exe"
    exe.write_bytes(build_synthetic_exe_multi([("ProductVersion", version)]))

    assert read_client_build(exe) == ""


def test_read_client_build_empty_for_a_missing_file(tmp_path: Path) -> None:
    """read_client_build() returns "" (not raise) for a nonexistent file."""
    assert read_client_build(tmp_path / "does-not-exist.exe") == ""


def test_read_client_build_empty_for_a_non_pe_file(tmp_path: Path) -> None:
    """read_client_build() returns "" for a file with no PE version resource."""
    exe = tmp_path / "EscapeFromTarkov.exe"
    exe.write_bytes(b"not a PE file at all, just some junk bytes" * 4)

    assert read_client_build(exe) == ""


# ============================================================================
# End-to-end on the SPT_Runtime layout
# ============================================================================


def test_init_on_runtime_layout_tracks_the_whitelist_profile_and_program_files(
    fake_game_dir_runtime: Path,
) -> None:
    """Init on the SPT_Runtime layout commits .gitignore, the profile and the program files.

    The server executable and SPT_Data/configs/core.json belong to the SPT
    program set, so the whitelist of the 4.1+ layout must track them the same
    way the SPT layout does.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = services.init_game_dir(fake_game_dir_runtime)

    assert result.spt_version == "4.1.2"
    committed = {f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)}
    assert committed == {
        Path(".gitignore"),
        Path("SPT_Runtime/user/profiles/abc.json"),
        Path("SPT_Runtime/SPT.Server.exe"),
        Path("SPT_Runtime/SPT_Data/configs/core.json"),
    }


def test_install_from_archive_translates_the_spt_prefix_to_the_runtime_dir(
    fake_game_dir_runtime: Path, make_archive: MakeArchive
) -> None:
    """Archive paths with "SPT/" are rewritten to the detected runtime directory.

    Creates an archive with "SPT/user/mods/CoolMod/..." and installs it into a
    repo with SPT_Runtime detected layout; the files must be extracted to
    SPT_Runtime, not SPT, and the result must reflect the mapped paths.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    services.init_game_dir(fake_game_dir_runtime)
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.0.0", "author": "me"})},
        "zip",
    )

    result = services.install_from_archive(fake_game_dir_runtime, archive)

    assert result.files == [Path("SPT_Runtime/user/mods/CoolMod/package.json")]
    assert (fake_game_dir_runtime / "SPT_Runtime/user/mods/CoolMod/package.json").exists()
    assert not (fake_game_dir_runtime / "SPT/user/mods/CoolMod/package.json").exists()


def test_list_mods_and_status_work_on_the_runtime_layout(
    fake_game_dir_runtime: Path, make_archive: MakeArchive
) -> None:
    """list_mods() and status() return runtime-prefixed paths correctly.

    Initializes an SPT_Runtime layout, installs a mod, and modifies a profile;
    list_mods() must return the mod, and status() must report the profile
    under SPT_Runtime/.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    services.init_game_dir(fake_game_dir_runtime)
    archive = make_archive(
        {
            "SPT_Runtime/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "me"}
            )
        },
        "zip",
    )
    services.install_from_archive(fake_game_dir_runtime, archive)

    assert [mod.name for mod in services.list_mods(fake_game_dir_runtime)] == ["CoolMod"]

    profile_path = fake_game_dir_runtime / "SPT_Runtime/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    report = services.status(fake_game_dir_runtime)

    assert report.modified_profiles == [Path("SPT_Runtime/user/profiles/abc.json")]


def test_check_profiles_finds_residue_on_the_runtime_layout(
    fake_game_dir_runtime: Path, install_commit: InstallCommit
) -> None:
    """check_profiles detects mod residue (orphaned IDs) in SPT_Runtime layout."""
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    repo = Repo.init(detect_layout(fake_game_dir_runtime))
    repo.commit("chore: clean SPT 4.1.2", trailers={"Spt-Version": "4.1.2"}, paths=None)
    item_id = "5c0d1b8b86f7744bb2683cf1"
    install_commit(
        repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT_Runtime/user/mods/ServerMod/db/templates/items.json": json.dumps(
                {item_id: {"_id": item_id, "_name": "item"}}
            )
        },
    )
    profile_path = fake_game_dir_runtime / "SPT_Runtime/user/profiles/abc.json"
    profile_path.write_text(json.dumps(profile_with_item(item_id)), encoding="utf-8")
    repo.commit(
        "chore: profile snapshot",
        paths=[Path("SPT_Runtime/user/profiles/abc.json")],
    )

    result = services.check_profiles(fake_game_dir_runtime)

    assert set(result.residues.keys()) == {"ServerMod"}
