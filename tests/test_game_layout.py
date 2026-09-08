"""Tests for SPT 4.x layout support (spt-mod-manager-fyx).

Covers ``GameLayout``/``detect_layout`` (SPT_Runtime vs SPT, both-present
warning, the flat-3.x rejection, precise missing-directory naming), the SPT
version chain (``read_core_json_version``, ``read_pe_file_version`` against a
synthetic PE built in-test, ``store_spt_version``/``read_stored_version``,
``resolve_spt_version`` fallback order), ``Repo.config_get``/``config_set``,
the ``set_spt_version`` service and API endpoint, the ``sptmod init
--spt-version`` CLI pin, and an end-to-end pass over the ``SPT_Runtime``
layout (init whitelist, archive-prefix translation on install, status,
list_mods, check_profiles).
"""

import json
import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import profile_with_item
from fixture_types import InstallCommit, MakeArchive
from pe_builder import (
    DATA_ENTRY_FILE_OFFSET,
    LEVEL1_FILE_OFFSET,
    LEVEL3_FILE_OFFSET,
    LEVEL_SIZE,
    LEVEL_TARGET_LOCAL_OFFSET,
    OPTIONAL_FILE_OFFSET,
    RESOURCE_DIRECTORY_RVA_FILE_OFFSET,
    SIZE_OF_OPTIONAL_HEADER_FILE_OFFSET,
    VERSION_DATA_FILE_OFFSET,
    build_pe_around_version_data,
    build_synthetic_exe,
    build_synthetic_exe_multi,
    pad4,
    utf16z,
    version_block,
    write_server_exe,
)
from sptmod.cli import app as cli_app
from sptmod.core import services
from sptmod.core.errors import GitError, NotAGameDirError
from sptmod.core.game_dir import (
    SPT_VERSION_CONFIG_KEY,
    GameLayout,
    detect_layout,
    is_game_program_path,
    read_client_build,
    read_core_json_version,
    read_pe_file_version,
    read_stored_version,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.repo import Repo

runner = CliRunner()


def _patched(data: bytes, offset: int, replacement: bytes) -> bytes:
    """Return ``data`` with ``replacement`` written at byte ``offset``."""
    patched = bytearray(data)
    patched[offset : offset + len(replacement)] = replacement
    return bytes(patched)


class TestSyntheticExeBuilderSanityCheck:
    """Sanity checks that the synthetic PE builder produces valid, readable files."""

    def test_builder_produces_a_pe_read_pe_file_version_can_parse(self, tmp_path: Path) -> None:
        """The synthetic PE produced by build_synthetic_exe() is parseable.

        Calls build_synthetic_exe("4.1.2"), writes it to disk, and verifies
        read_pe_file_version() reads back "4.1.2", confirming the builder
        creates structurally valid PE images.
        """
        exe = tmp_path / "SPT.Server.exe"
        exe.write_bytes(build_synthetic_exe("4.1.2"))

        assert read_pe_file_version(exe) == "4.1.2"


# ============================================================================
# read_pe_file_version: malformed/truncated PE and resource-directory data
#
# Each corruption below starts from a structurally valid synthetic exe and
# flips (or truncates) exactly the bytes needed to trigger one specific
# defensive guard inside the best-effort PE/VS_VERSIONINFO parser; every one
# of them must degrade to ``None`` rather than raise or read garbage.
# ============================================================================


def _corrupt_coff_header_truncated(data: bytes) -> bytes:
    """Cuts the file inside the COFF header itself (a short read)."""
    return data[:70]


def _corrupt_optional_header_truncated(data: bytes) -> bytes:
    """Cuts the file inside the optional header (a short read)."""
    return data[:138]


def _corrupt_optional_header_bad_magic(data: bytes) -> bytes:
    """Neither the PE32 (0x10B) nor the PE32+ (0x20B) magic."""
    return _patched(data, OPTIONAL_FILE_OFFSET, struct.pack("<H", 0x0107))


def _corrupt_optional_header_too_small_for_resource_entry(data: bytes) -> bytes:
    """A declared ``SizeOfOptionalHeader`` too small to reach data-directory
    entry 2 (the resource directory)."""
    return _patched(data, SIZE_OF_OPTIONAL_HEADER_FILE_OFFSET, struct.pack("<H", 120))


def _corrupt_resource_directory_rva_absent(data: bytes) -> bytes:
    """No resource directory at all (RVA zeroed)."""
    return _patched(data, RESOURCE_DIRECTORY_RVA_FILE_OFFSET, struct.pack("<I", 0))


def _corrupt_resource_directory_rva_unmatched_by_any_section(data: bytes) -> bytes:
    """A resource RVA no section's address range actually covers."""
    return _patched(data, RESOURCE_DIRECTORY_RVA_FILE_OFFSET, struct.pack("<I", 0xFFFF000))


def _corrupt_section_headers_truncated(data: bytes) -> bytes:
    """Cuts the file mid-way through the (single) section header."""
    return data[:338]


def _corrupt_data_entry_rva_out_of_section_bounds(data: bytes) -> bytes:
    """A resource data entry whose ``data_rva`` resolves before the section
    start, so the sliced-out version bytes would start at a negative offset."""
    return _patched(data, DATA_ENTRY_FILE_OFFSET, struct.pack("<I", 0))


def _corrupt_level1_target_not_subdirectory_flagged(data: bytes) -> bytes:
    """The RT_VERSION entry points straight at data instead of the expected
    name-level subdirectory."""
    return _patched(
        data,
        LEVEL1_FILE_OFFSET + LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", LEVEL_SIZE),
    )


def _corrupt_level3_target_still_subdirectory_flagged(data: bytes) -> bytes:
    """The language-level entry is itself flagged as another subdirectory,
    so the fixed 3-level walk never finds a leaf."""
    return _patched(
        data,
        LEVEL3_FILE_OFFSET + LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", 0x80000000 | (3 * LEVEL_SIZE)),
    )


def _corrupt_leaf_offset_out_of_bounds(data: bytes) -> bytes:
    """The located leaf offset points past the end of the resource data."""
    return _patched(
        data,
        LEVEL3_FILE_OFFSET + LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", 0x7FFFFFF0),
    )


def _corrupt_resource_directory_offset_out_of_bounds(data: bytes) -> bytes:
    """The name-level subdirectory offset itself is past the end of the
    resource data, so even its own header cannot be read."""
    return _patched(
        data,
        LEVEL1_FILE_OFFSET + LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", 0x80000000 | 500),
    )


def _corrupt_resource_entry_scan_runs_past_the_buffer(data: bytes) -> bytes:
    """A directory that claims far more entries than actually fit, none of
    which match the wanted RT_VERSION id, so the scan itself runs off the
    end of the resource data."""
    patched = _patched(data, LEVEL1_FILE_OFFSET + 14, struct.pack("<H", 100))
    return _patched(patched, LEVEL1_FILE_OFFSET + 16, struct.pack("<I", 999))


def _corrupt_resource_entry_scan_finds_no_match_within_bounds(data: bytes) -> bytes:
    """A directory with a couple of extra, non-matching but in-bounds
    entries: the scan exhausts every entry without ever going out of
    bounds, unlike :func:`_corrupt_resource_entry_scan_runs_past_the_buffer`."""
    patched = _patched(data, LEVEL1_FILE_OFFSET + 14, struct.pack("<H", 3))
    return _patched(patched, LEVEL1_FILE_OFFSET + 16, struct.pack("<I", 999))


def _corrupt_string_file_info_table_child_is_unparseable(data: bytes) -> bytes:
    """The ``StringFileInfo`` node itself parses fine, but its only child
    (the language/codepage table) declares an impossible length, so walking
    its children stops immediately with none found."""
    return _patched(data, VERSION_DATA_FILE_OFFSET + 128, struct.pack("<H", 0xFFFF))


def _corrupt_version_block_too_short_to_have_a_header(data: bytes) -> bytes:
    """The data entry declares a size too small for even the 6-byte
    VS_VERSIONINFO node header."""
    return _patched(data, DATA_ENTRY_FILE_OFFSET + 4, struct.pack("<I", 2))


def _corrupt_version_block_declares_an_impossible_length(data: bytes) -> bytes:
    """The VS_VERSIONINFO node's own ``length`` field claims far more bytes
    than the (correctly-sized) sliced-out block actually has."""
    return _patched(data, VERSION_DATA_FILE_OFFSET, struct.pack("<H", 0xFFFF))


@pytest.mark.parametrize(
    "corrupt",
    [
        _corrupt_coff_header_truncated,
        _corrupt_optional_header_truncated,
        _corrupt_optional_header_bad_magic,
        _corrupt_optional_header_too_small_for_resource_entry,
        _corrupt_resource_directory_rva_absent,
        _corrupt_resource_directory_rva_unmatched_by_any_section,
        _corrupt_section_headers_truncated,
        _corrupt_data_entry_rva_out_of_section_bounds,
        _corrupt_level1_target_not_subdirectory_flagged,
        _corrupt_level3_target_still_subdirectory_flagged,
        _corrupt_leaf_offset_out_of_bounds,
        _corrupt_resource_directory_offset_out_of_bounds,
        _corrupt_resource_entry_scan_runs_past_the_buffer,
        _corrupt_resource_entry_scan_finds_no_match_within_bounds,
        _corrupt_string_file_info_table_child_is_unparseable,
        _corrupt_version_block_too_short_to_have_a_header,
        _corrupt_version_block_declares_an_impossible_length,
    ],
    ids=lambda fn: fn.__name__.removeprefix("_corrupt_"),
)
def test_read_pe_file_version_none_for_a_malformed_resource_section(
    tmp_path: Path, corrupt: Callable[[bytes], bytes]
) -> None:
    """Each of 17 PE/resource corruptions degrades gracefully to None.

    Parametrized over corruption functions; the parser must handle truncated
    headers, missing directories, out-of-bounds offsets, and malformed
    version blocks without raising, always returning None.
    """
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(corrupt(build_synthetic_exe("1.2.3")))

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_when_neither_file_nor_product_version_present(
    tmp_path: Path,
) -> None:
    """When the version block has no FileVersion or ProductVersion, return None.

    Builds a PE with a VS_VERSION_INFO string block named "Comments" (not
    "FileVersion" or "ProductVersion"); the fallback chain must fail and
    read_pe_file_version() must return None.
    """
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(build_synthetic_exe("1.2.3", key_name="Comments"))

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_when_string_file_info_is_absent(
    tmp_path: Path,
) -> None:
    """A VS_VERSION_INFO whose only child is ``VarFileInfo`` (a real, common
    sibling of ``StringFileInfo``, just never both together here): the
    string-table walk must skip it and end up with nothing, not crash."""
    fixed_file_info = struct.pack("<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0)
    var_table = version_block("Translation", 2, struct.pack("<HH", 0x0409, 0x04B0))
    var_file_info = version_block("VarFileInfo", 0, b"", var_table)
    version_data = version_block("VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, var_file_info)
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(build_pe_around_version_data(version_data))

    assert read_pe_file_version(exe) is None


def _version_block_typed(
    key: str,
    value_word_count: int,
    value: bytes,
    value_type: int,
    children: bytes = b"",
) -> bytes:
    """Like ``version_block``, but with an explicit ``wType`` field (0 =
    binary, 1 = text) instead of always claiming text."""
    key_bytes = utf16z(key)
    head = struct.pack("<HHH", 0, value_word_count, value_type) + key_bytes
    body = pad4(head) + pad4(value) if value else pad4(head)
    total = body + children
    return struct.pack("<HHH", len(total), value_word_count, value_type) + key_bytes + total[len(head) :]


def test_read_pe_file_version_skips_a_binary_typed_string_entry_and_finds_the_next(
    tmp_path: Path,
) -> None:
    """A ``StringFileInfo`` table entry declared as binary (``wType=0``) must
    not be decoded as UTF-16 text (it stays empty), and must not stop the
    walk from finding a normal, correctly text-typed ``FileVersion`` sibling."""
    fixed_file_info = struct.pack("<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0)
    binary_entry = _version_block_typed("BinaryData", 2, b"\x01\x02\x03\x04", value_type=0)
    text_entry = version_block("FileVersion", 6, utf16z("1.2.3"))
    string_table = version_block("040904B0", 0, b"", binary_entry + text_entry)
    string_file_info = version_block("StringFileInfo", 0, b"", string_table)
    version_data = version_block("VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, string_file_info)
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(build_pe_around_version_data(version_data))

    assert read_pe_file_version(exe) == "1.2.3"


# ============================================================================
# detect_layout
# ============================================================================


def test_detect_layout_accepts_runtime_only(tmp_path: Path) -> None:
    """SPT_Runtime alone (4.1+ layout) is a valid, unambiguous game directory."""
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT_Runtime"
    assert layout.warning == ""


def test_detect_layout_accepts_spt_only(tmp_path: Path) -> None:
    """SPT alone (4.0 layout) is a valid, unambiguous game directory."""
    root = tmp_path / "game"
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT"
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


def test_read_client_build_returns_the_second_to_last_dash_and_dot_component(
    tmp_path: Path,
) -> None:
    """read_client_build() splits ProductVersion on both '.' and '-' and
    returns the second-to-last component: '0.16.9.5-40743.36018' is build
    '40743', the value SPT compares an installation's client build against.
    """
    exe = tmp_path / "EscapeFromTarkov.exe"
    exe.write_bytes(build_synthetic_exe_multi([("ProductVersion", "0.16.9.5-40743.36018")]))

    assert read_client_build(exe) == "40743"


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


def test_read_client_build_falls_back_to_file_version_when_no_product_version(
    tmp_path: Path,
) -> None:
    """Without a ProductVersion string, read_client_build() reads FileVersion instead."""
    exe = tmp_path / "EscapeFromTarkov.exe"
    exe.write_bytes(build_synthetic_exe_multi([("FileVersion", "0.16.9.5-40743.36018")]))

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
# read_core_json_version
# ============================================================================


def _layout(tmp_path: Path, runtime_dir_name: str = "SPT_Runtime") -> GameLayout:
    """Create a GameLayout with a minimal core.json directory structure.

    Used by core.json version-reading tests; creates the directory hierarchy
    and returns a GameLayout instance pointing to it.
    """
    (tmp_path / "game" / runtime_dir_name / "SPT_Data" / "configs").mkdir(parents=True, exist_ok=True)
    return GameLayout(root=tmp_path / "game", runtime_dir_name=runtime_dir_name)


def _write_core_json(layout: GameLayout, content: str) -> None:
    """Write a JSON string to core.json at the layout's expected location."""
    (layout.root / layout.core_config_file).write_text(content, encoding="utf-8")


def test_read_core_json_version_returns_the_field_when_present(
    tmp_path: Path,
) -> None:
    """Extract sptVersion from well-formed core.json."""
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "4.1.0"}))

    assert read_core_json_version(layout) == "4.1.0"


def test_read_core_json_version_none_for_a_4x_style_config_without_the_key(
    tmp_path: Path,
) -> None:
    """core.json without sptVersion key returns None."""
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"projectName": "SPT", "compatibleTarkovVersion": "1.0"}))

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_when_file_missing(tmp_path: Path) -> None:
    """When core.json does not exist, read_core_json_version returns None.

    Creates a GameLayout with the directory structure but no core.json file.
    """
    layout = _layout(tmp_path)

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_when_json_malformed(tmp_path: Path) -> None:
    """When core.json is invalid JSON, read_core_json_version returns None.

    Writes syntactically invalid JSON to core.json (unclosed brace).
    """
    layout = _layout(tmp_path)
    _write_core_json(layout, "{not valid json")

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_for_an_empty_string_value(
    tmp_path: Path,
) -> None:
    """Whitespace-only sptVersion is treated as empty."""
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "   "}))

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_for_a_valid_json_non_object_root(
    tmp_path: Path,
) -> None:
    """Non-object JSON root (e.g., array) returns None."""
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps([1, 2, 3]))

    assert read_core_json_version(layout) is None


# ============================================================================
# read_pe_file_version
# ============================================================================


@pytest.mark.parametrize(
    ("key_name", "version"),
    [("FileVersion", "4.1.2"), ("ProductVersion", "4.0.9")],
    ids=["file-version", "falls-back-to-product-version"],
)
def test_read_pe_file_version_reads_the_version_resource(tmp_path: Path, key_name: str, version: str) -> None:
    """Parse FileVersion; if missing, fall back to ProductVersion."""
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(build_synthetic_exe(version, key_name))

    assert read_pe_file_version(exe) == version


def test_read_pe_file_version_none_for_garbage_bytes(tmp_path: Path) -> None:
    """Non-PE file content returns None without raising.

    Writes random junk bytes to a file; read_pe_file_version() must return
    None (no crash on missing MZ header).
    """
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(b"not a PE file at all, just some junk bytes" * 4)

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_for_a_missing_file(tmp_path: Path) -> None:
    """When the file does not exist, read_pe_file_version returns None.

    Passes a non-existent path; must return None (not raise FileNotFoundError).
    """
    assert read_pe_file_version(tmp_path / "does-not-exist.exe") is None


# ============================================================================
# Repo.config_get / config_set
# ============================================================================


def test_config_get_returns_none_for_an_unset_key(initialized_repo: Repo) -> None:
    """Calling config_get() on a non-existent key returns None.

    Queries a key that was never set; must return None.
    """
    assert initialized_repo.config_get("sptmod.unset-key") is None


def test_config_set_then_get_roundtrips_the_value(initialized_repo: Repo) -> None:
    """config_set() followed by config_get() returns the set value.

    Sets a key to "hello-value" and immediately gets it back; must match.
    """
    initialized_repo.config_set("sptmod.testkey", "hello-value")

    assert initialized_repo.config_get("sptmod.testkey") == "hello-value"


def test_config_set_overwrites_an_existing_value(initialized_repo: Repo) -> None:
    """Calling config_set() twice overwrites the first value.

    Sets the key to "first", then immediately sets it to "second"; the final
    get must return "second".
    """
    initialized_repo.config_set("sptmod.testkey", "first")
    initialized_repo.config_set("sptmod.testkey", "second")

    assert initialized_repo.config_get("sptmod.testkey") == "second"


def test_config_get_raises_git_error_for_a_reason_other_than_an_unset_key(
    initialized_repo: Repo,
) -> None:
    """A corrupted ``.git/config`` makes git refuse to read *any* key, which
    is a different failure than the plain "key unset" (returncode 1) case
    every other config_get test exercises."""
    config_file = initialized_repo.path / ".git" / "config"
    with config_file.open("a", encoding="utf-8") as handle:
        handle.write("not a valid config line at all\n")

    with pytest.raises(GitError):
        initialized_repo.config_get("sptmod.testkey")


# ============================================================================
# store_spt_version / read_stored_version
# ============================================================================


def test_read_stored_version_none_when_nothing_was_stored(
    initialized_repo: Repo,
) -> None:
    """read_stored_version() returns None when store_spt_version() was never called.

    On a fresh repo, the SPT_VERSION_CONFIG_KEY has not been set.
    """
    assert read_stored_version(initialized_repo) is None


def test_store_then_read_stored_version_roundtrips(initialized_repo: Repo) -> None:
    """Store and read operations preserve the version string in git config."""
    store_spt_version(initialized_repo, "4.1.2")

    assert read_stored_version(initialized_repo) == "4.1.2"
    assert initialized_repo.config_get(SPT_VERSION_CONFIG_KEY) == "4.1.2"


# ============================================================================
# resolve_spt_version chain: core.json -> PE -> stored -> None
# ============================================================================


def _build_layout_and_repo(
    tmp_path: Path,
    *,
    core_version: str | None = None,
    exe_version: str | None = None,
) -> tuple[GameLayout, Repo]:
    """Create a GameLayout and initialized Repo with optional version sources.

    Used by the resolve_spt_version chain tests; sets up core.json and/or
    a synthetic server EXE, and initializes git.
    """
    layout = _layout(tmp_path)
    core_config: dict[str, str] = {"projectName": "SPT"}
    if core_version is not None:
        core_config["sptVersion"] = core_version
    _write_core_json(layout, json.dumps(core_config))
    if exe_version is not None:
        write_server_exe(layout.root, layout.runtime_dir_name, exe_version)
    repo = Repo.init(layout)
    return layout, repo


@pytest.mark.parametrize(
    ("core_version", "exe_version", "stored", "expected"),
    [
        ("9.9.9", "1.1.1", "2.2.2", "9.9.9"),
        (None, "1.1.1", "2.2.2", "1.1.1"),
        (None, None, "2.2.2", "2.2.2"),
        (None, None, None, None),
    ],
    ids=[
        "prefers-core-json",
        "falls-back-to-pe",
        "falls-back-to-stored",
        "none-when-chain-empty",
    ],
)
def test_resolve_spt_version_walks_the_fallback_chain(
    tmp_path: Path,
    core_version: str | None,
    exe_version: str | None,
    stored: str | None,
    expected: str | None,
) -> None:
    """Fallback chain: core.json → PE → git config → None."""
    layout, repo = _build_layout_and_repo(tmp_path, core_version=core_version, exe_version=exe_version)
    if stored is not None:
        store_spt_version(repo, stored)

    assert resolve_spt_version(layout, repo) == expected


# ============================================================================
# set_spt_version (service)
# ============================================================================


def test_set_spt_version_stores_and_returns_the_stripped_value(
    initialized_repo: Repo,
) -> None:
    """set_spt_version() trims whitespace and stores the result.

    Calls with "  4.1.2  "; must return "4.1.2" and store it trimmed.
    """
    result = services.set_spt_version(initialized_repo.path, "  4.1.2  ")

    assert result == "4.1.2"
    assert read_stored_version(initialized_repo) == "4.1.2"


def test_set_spt_version_raises_value_error_for_an_empty_version(
    initialized_repo: Repo,
) -> None:
    """set_spt_version() raises ValueError when passed an empty string.

    The error message must contain "empty".
    """
    with pytest.raises(ValueError, match="empty"):
        services.set_spt_version(initialized_repo.path, "")


def test_set_spt_version_raises_not_a_game_dir_error_for_a_non_game_dir(
    tmp_path: Path,
) -> None:
    """set_spt_version() raises NotAGameDirError for a non-game directory.

    Calls on an empty directory; must raise NotAGameDirError.
    """
    empty = tmp_path / "not-a-game-dir"
    empty.mkdir()

    with pytest.raises(NotAGameDirError):
        services.set_spt_version(empty, "4.1.2")


def test_set_spt_version_raises_git_error_when_not_yet_a_repository(
    fake_game_dir: Path,
) -> None:
    """set_spt_version() raises GitError when git is not yet initialized.

    Calls on a valid game dir with no .git; must raise GitError.
    """
    with pytest.raises(GitError):
        services.set_spt_version(fake_game_dir, "4.1.2")


# ============================================================================
# set_spt_version over the rpc adapter
# ============================================================================


def test_rpc_set_spt_version_stores_and_returns_the_stripped_value(
    fake_game_dir: Path,
) -> None:
    """RPC endpoint for set_spt_version returns result event with trimmed version."""
    services.init_game_dir(fake_game_dir)

    result = runner.invoke(
        cli_app,
        ["rpc", "set_spt_version"],
        input=json.dumps({"path": str(fake_game_dir), "version": "  4.1.9  "}),
    )

    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert result.exit_code == 0
    assert lines == [{"event": "result", "data": {"spt_version": "4.1.9"}}]
    assert read_stored_version(Repo(fake_game_dir)) == "4.1.9"


# ============================================================================
# CLI contract pin: `sptmod init --spt-version` / interactive prompt
# ============================================================================


def test_cli_init_with_spt_version_option_stores_it_and_skips_the_prompt(
    fake_game_dir_runtime: Path,
) -> None:
    """The --spt-version CLI option stores the version without prompting.

    Runs ``sptmod init --spt-version 4.1.2``; must exit 0 and print
    "initialized SPT 4.1.2" to stdout (no interactive prompt).
    """
    result = runner.invoke(
        cli_app,
        ["init", str(fake_game_dir_runtime), "--spt-version", "4.1.2"],
    )

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout


def test_cli_init_without_spt_version_prompts_and_stores_the_answer(
    fake_game_dir_runtime: Path,
) -> None:
    """Without --spt-version, CLI init prompts interactively and stores input.

    Runs ``sptmod init`` without --spt-version and feeds "4.1.2\n" to stdin;
    must exit 0 and print "initialized SPT 4.1.2".
    """
    result = runner.invoke(cli_app, ["init", str(fake_game_dir_runtime)], input="4.1.2\n")

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout


def test_cli_init_reports_a_warning_when_both_runtime_dirs_are_present(
    fake_game_dir_runtime: Path,
) -> None:
    """CLI init prints a warning when both SPT_Runtime and SPT directories exist.

    Creates both runtime dirs and runs init; must exit 0 and print both names
    to stdout (the warning from detect_layout).
    """
    (fake_game_dir_runtime / "SPT").mkdir()
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = runner.invoke(cli_app, ["init", str(fake_game_dir_runtime)])

    assert result.exit_code == 0
    assert "SPT_Runtime" in result.stdout
    assert "SPT" in result.stdout


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


def test_init_on_runtime_layout_ignored_dummies_are_absent_from_the_commit(
    fake_game_dir_runtime: Path,
) -> None:
    """Ignored paths (logs, backups, game data) are not committed."""
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = services.init_game_dir(fake_game_dir_runtime)

    committed = {f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)}
    ignored = {
        Path("SPT_Runtime/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT_Runtime/user/profiles/backups/old.json"),
    }
    assert committed.isdisjoint(ignored)


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
