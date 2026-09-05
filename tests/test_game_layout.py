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

from fixture_types import InstallCommit, MakeArchive
from sptmod.cli import app as cli_app
from sptmod.core import game_dir, services
from sptmod.core.errors import GitError, NotAGameDirError
from sptmod.core.game_dir import (
    SPT_VERSION_CONFIG_KEY,
    GameLayout,
    detect_layout,
    read_core_json_version,
    read_pe_file_version,
    read_stored_version,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.repo import Repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


# ============================================================================
# Synthetic PE builder (stdlib only): a minimal PE32+ image with one
# VS_VERSION_INFO resource carrying a StringFileInfo/String entry, built to
# the documented Microsoft VS_VERSIONINFO binary layout (independent of
# read_pe_file_version's own parsing implementation) and cross-checked with
# `pefile` during development.
# ============================================================================


def _utf16z(s: str) -> bytes:
    return (s + "\0").encode("utf-16-le")


def _pad4(data: bytes) -> bytes:
    return data + b"\0" * ((-len(data)) % 4)


def _version_block(
    key: str, value_word_count: int, value: bytes, children: bytes = b""
) -> bytes:
    key_bytes = _utf16z(key)
    head = struct.pack("<HHH", 0, value_word_count, 1 if value else 0) + key_bytes
    body = _pad4(head) + _pad4(value) if value else _pad4(head)
    total = body + children
    return struct.pack("<HHH", len(total), value_word_count, 1) + key_bytes + total[
        len(head) :
    ]


def _build_version_resource(version: str, key_name: str) -> bytes:
    fixed_file_info = struct.pack(
        "<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0
    )
    value_str = _utf16z(version)
    string_entry = _version_block(key_name, len(version) + 1, value_str)
    string_table = _version_block("040904B0", 0, b"", string_entry)
    string_file_info = _version_block("StringFileInfo", 0, b"", string_table)
    return _version_block(
        "VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, string_file_info
    )


# Fixed layout constants shared between the builder and the corruption tests
# below: the builder always places a single ".rsrc" section with exactly one
# RT_VERSION/name/language resource-directory chain (level1/level2/level3),
# one IMAGE_RESOURCE_DATA_ENTRY and the VS_VERSION_INFO bytes, in that exact
# order, at these fixed file offsets -- so a corruption test can flip a few
# known bytes of an otherwise-valid image instead of hand-building a whole
# malformed PE from scratch.
_SECTION_ALIGNMENT, _FILE_ALIGNMENT, _HEADERS_SIZE = 0x1000, 0x200, 0x400
_RSRC_RVA, _RSRC_RAW_OFFSET = _SECTION_ALIGNMENT, _HEADERS_SIZE
_LEVEL_SIZE, _DATA_ENTRY_SIZE = 24, 16
_HEADER_TOTAL = 3 * _LEVEL_SIZE + _DATA_ENTRY_SIZE
_LEVEL1_FILE_OFFSET = _RSRC_RAW_OFFSET
_LEVEL2_FILE_OFFSET = _LEVEL1_FILE_OFFSET + _LEVEL_SIZE
_LEVEL3_FILE_OFFSET = _LEVEL2_FILE_OFFSET + _LEVEL_SIZE
_DATA_ENTRY_FILE_OFFSET = _LEVEL3_FILE_OFFSET + _LEVEL_SIZE
_VERSION_DATA_FILE_OFFSET = _RSRC_RAW_OFFSET + _HEADER_TOTAL
# Within each 24-byte resource-directory level, the id/target II pair sits
# at local offset 16 (id) / 20 (target); "target" carries the subdirectory
# flag (0x80000000) when it points at another directory level rather than
# an IMAGE_RESOURCE_DATA_ENTRY.
_LEVEL_TARGET_LOCAL_OFFSET = 20
_COFF_FILE_OFFSET = 64
_OPTIONAL_FILE_OFFSET = _COFF_FILE_OFFSET + 24
_SIZE_OF_OPTIONAL_HEADER_FILE_OFFSET = _COFF_FILE_OFFSET + 20
# _DATA_DIRECTORY_OFFSETS[0x20B] (PE32+) == 112, resource entry is index 2.
_RESOURCE_DIRECTORY_RVA_FILE_OFFSET = _OPTIONAL_FILE_OFFSET + 112 + 2 * 8


def _build_pe_around_version_data(version_data: bytes) -> bytes:
    """Build a minimal, structurally valid PE32+ image around ``version_data``.

    ``version_data`` is embedded as the leaf of a normal, single-entry
    RT_VERSION/name/language resource-directory chain; the caller may pass
    perfectly valid VS_VERSIONINFO bytes (as :func:`_build_version_resource`
    produces), or deliberately malformed ones, to test how the string table
    is parsed once the surrounding PE/resource-directory navigation has
    already succeeded.
    """
    padded_version = _pad4(version_data)
    data_rva = _RSRC_RVA + _HEADER_TOTAL

    level1 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack(
        "<II", 16, 0x80000000 | _LEVEL_SIZE
    )
    level2 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack(
        "<II", 1, 0x80000000 | (2 * _LEVEL_SIZE)
    )
    level3 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack(
        "<II", 1033, 3 * _LEVEL_SIZE
    )
    data_entry = struct.pack("<IIII", data_rva, len(version_data), 0, 0)
    rsrc_section = level1 + level2 + level3 + data_entry + padded_version
    rsrc_raw_size = (
        (len(rsrc_section) + _FILE_ALIGNMENT - 1) // _FILE_ALIGNMENT
    ) * _FILE_ALIGNMENT
    rsrc_padded = rsrc_section + b"\0" * (rsrc_raw_size - len(rsrc_section))
    image_size = _RSRC_RVA + (
        (len(rsrc_section) + _SECTION_ALIGNMENT - 1) // _SECTION_ALIGNMENT
    ) * _SECTION_ALIGNMENT

    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 60, 64)

    coff = struct.pack("<IHHIIIHH", 0x00004550, 0x8664, 1, 0, 0, 0, 0xF0, 0x0022)

    optional = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B, 14, 0, 0x200, 0, 0, 0, 0x1000, 0x140000000,
        _SECTION_ALIGNMENT, _FILE_ALIGNMENT, 6, 0, 1, 0, 6, 0, 0,
        image_size, _HEADERS_SIZE, 0, 3, 0,
        0x100000, 0x1000, 0x100000, 0x1000, 0, 16,
    )
    data_dirs = bytearray(16 * 8)
    struct.pack_into("<II", data_dirs, 2 * 8, _RSRC_RVA, len(rsrc_section))
    optional += bytes(data_dirs)

    section_header = struct.pack(
        "<8sIIIIIIHHI",
        b".rsrc\0\0\0",
        len(rsrc_section),
        _RSRC_RVA,
        rsrc_raw_size,
        _RSRC_RAW_OFFSET,
        0, 0, 0, 0,
        0x40000040,
    )

    header_blob = bytes(dos) + coff + optional + section_header
    header_blob += b"\0" * (_HEADERS_SIZE - len(header_blob))
    return header_blob + rsrc_padded


def build_synthetic_exe(version: str, key_name: str = "FileVersion") -> bytes:
    """Build a minimal PE32+ image whose VS_VERSION_INFO carries ``key_name``."""
    return _build_pe_around_version_data(_build_version_resource(version, key_name))


def _patched(data: bytes, offset: int, replacement: bytes) -> bytes:
    """Return ``data`` with ``replacement`` written at byte ``offset``."""
    patched = bytearray(data)
    patched[offset : offset + len(replacement)] = replacement
    return bytes(patched)


def write_server_exe(root: Path, runtime_dir_name: str, version: str) -> Path:
    exe_path = root / runtime_dir_name / "SPT.Server.exe"
    exe_path.write_bytes(build_synthetic_exe(version))
    return exe_path


class TestSyntheticExeBuilderSanityCheck:
    """The builder itself must actually produce a version FileVersion reads."""

    def test_builder_produces_a_pe_read_pe_file_version_can_parse(
        self, tmp_path: Path
    ) -> None:
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
    return _patched(data, _OPTIONAL_FILE_OFFSET, struct.pack("<H", 0x0107))


def _corrupt_optional_header_too_small_for_resource_entry(data: bytes) -> bytes:
    """A declared ``SizeOfOptionalHeader`` too small to reach data-directory
    entry 2 (the resource directory)."""
    return _patched(data, _SIZE_OF_OPTIONAL_HEADER_FILE_OFFSET, struct.pack("<H", 120))


def _corrupt_resource_directory_rva_absent(data: bytes) -> bytes:
    """No resource directory at all (RVA zeroed)."""
    return _patched(data, _RESOURCE_DIRECTORY_RVA_FILE_OFFSET, struct.pack("<I", 0))


def _corrupt_resource_directory_rva_unmatched_by_any_section(data: bytes) -> bytes:
    """A resource RVA no section's address range actually covers."""
    return _patched(
        data, _RESOURCE_DIRECTORY_RVA_FILE_OFFSET, struct.pack("<I", 0xFFFF000)
    )


def _corrupt_section_headers_truncated(data: bytes) -> bytes:
    """Cuts the file mid-way through the (single) section header."""
    return data[:338]


def _corrupt_data_entry_rva_out_of_section_bounds(data: bytes) -> bytes:
    """A resource data entry whose ``data_rva`` resolves before the section
    start, so the sliced-out version bytes would start at a negative offset."""
    return _patched(data, _DATA_ENTRY_FILE_OFFSET, struct.pack("<I", 0))


def _corrupt_level1_target_not_subdirectory_flagged(data: bytes) -> bytes:
    """The RT_VERSION entry points straight at data instead of the expected
    name-level subdirectory."""
    return _patched(
        data,
        _LEVEL1_FILE_OFFSET + _LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", _LEVEL_SIZE),
    )


def _corrupt_level3_target_still_subdirectory_flagged(data: bytes) -> bytes:
    """The language-level entry is itself flagged as another subdirectory,
    so the fixed 3-level walk never finds a leaf."""
    return _patched(
        data,
        _LEVEL3_FILE_OFFSET + _LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", 0x80000000 | (3 * _LEVEL_SIZE)),
    )


def _corrupt_leaf_offset_out_of_bounds(data: bytes) -> bytes:
    """The located leaf offset points past the end of the resource data."""
    return _patched(
        data,
        _LEVEL3_FILE_OFFSET + _LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", 0x7FFFFFF0),
    )


def _corrupt_resource_directory_offset_out_of_bounds(data: bytes) -> bytes:
    """The name-level subdirectory offset itself is past the end of the
    resource data, so even its own header cannot be read."""
    return _patched(
        data,
        _LEVEL1_FILE_OFFSET + _LEVEL_TARGET_LOCAL_OFFSET,
        struct.pack("<I", 0x80000000 | 500),
    )


def _corrupt_resource_entry_scan_runs_past_the_buffer(data: bytes) -> bytes:
    """A directory that claims far more entries than actually fit, none of
    which match the wanted RT_VERSION id, so the scan itself runs off the
    end of the resource data."""
    patched = _patched(data, _LEVEL1_FILE_OFFSET + 14, struct.pack("<H", 100))
    return _patched(patched, _LEVEL1_FILE_OFFSET + 16, struct.pack("<I", 999))


def _corrupt_resource_entry_scan_finds_no_match_within_bounds(data: bytes) -> bytes:
    """A directory with a couple of extra, non-matching but in-bounds
    entries: the scan exhausts every entry without ever going out of
    bounds, unlike :func:`_corrupt_resource_entry_scan_runs_past_the_buffer`."""
    patched = _patched(data, _LEVEL1_FILE_OFFSET + 14, struct.pack("<H", 3))
    return _patched(patched, _LEVEL1_FILE_OFFSET + 16, struct.pack("<I", 999))


def _corrupt_string_file_info_table_child_is_unparseable(data: bytes) -> bytes:
    """The ``StringFileInfo`` node itself parses fine, but its only child
    (the language/codepage table) declares an impossible length, so walking
    its children stops immediately with none found."""
    return _patched(
        data, _VERSION_DATA_FILE_OFFSET + 128, struct.pack("<H", 0xFFFF)
    )


def _corrupt_version_block_too_short_to_have_a_header(data: bytes) -> bytes:
    """The data entry declares a size too small for even the 6-byte
    VS_VERSIONINFO node header."""
    return _patched(data, _DATA_ENTRY_FILE_OFFSET + 4, struct.pack("<I", 2))


def _corrupt_version_block_declares_an_impossible_length(data: bytes) -> bytes:
    """The VS_VERSIONINFO node's own ``length`` field claims far more bytes
    than the (correctly-sized) sliced-out block actually has."""
    return _patched(data, _VERSION_DATA_FILE_OFFSET, struct.pack("<H", 0xFFFF))


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
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(corrupt(build_synthetic_exe("1.2.3")))

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_when_neither_file_nor_product_version_present(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(build_synthetic_exe("1.2.3", key_name="Comments"))

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_when_string_file_info_is_absent(
    tmp_path: Path,
) -> None:
    """A VS_VERSION_INFO whose only child is ``VarFileInfo`` (a real, common
    sibling of ``StringFileInfo``, just never both together here): the
    string-table walk must skip it and end up with nothing, not crash."""
    fixed_file_info = struct.pack(
        "<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0
    )
    var_table = _version_block("Translation", 2, struct.pack("<HH", 0x0409, 0x04B0))
    var_file_info = _version_block("VarFileInfo", 0, b"", var_table)
    version_data = _version_block(
        "VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, var_file_info
    )
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(_build_pe_around_version_data(version_data))

    assert read_pe_file_version(exe) is None


def _version_block_typed(
    key: str,
    value_word_count: int,
    value: bytes,
    value_type: int,
    children: bytes = b"",
) -> bytes:
    """Like ``_version_block``, but with an explicit ``wType`` field (0 =
    binary, 1 = text) instead of always claiming text."""
    key_bytes = _utf16z(key)
    head = struct.pack("<HHH", 0, value_word_count, value_type) + key_bytes
    body = _pad4(head) + _pad4(value) if value else _pad4(head)
    total = body + children
    return (
        struct.pack("<HHH", len(total), value_word_count, value_type)
        + key_bytes
        + total[len(head) :]
    )


def test_read_pe_file_version_skips_a_binary_typed_string_entry_and_finds_the_next(
    tmp_path: Path,
) -> None:
    """A ``StringFileInfo`` table entry declared as binary (``wType=0``) must
    not be decoded as UTF-16 text (it stays empty), and must not stop the
    walk from finding a normal, correctly text-typed ``FileVersion`` sibling."""
    fixed_file_info = struct.pack(
        "<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0
    )
    binary_entry = _version_block_typed(
        "BinaryData", 2, b"\x01\x02\x03\x04", value_type=0
    )
    text_entry = _version_block("FileVersion", 6, _utf16z("1.2.3"))
    string_table = _version_block(
        "040904B0", 0, b"", binary_entry + text_entry
    )
    string_file_info = _version_block("StringFileInfo", 0, b"", string_table)
    version_data = _version_block(
        "VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, string_file_info
    )
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(_build_pe_around_version_data(version_data))

    assert read_pe_file_version(exe) == "1.2.3"


# ============================================================================
# detect_layout
# ============================================================================


def test_detect_layout_accepts_runtime_only(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT_Runtime"
    assert layout.warning == ""


def test_detect_layout_accepts_spt_only(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT"
    assert layout.warning == ""


def test_detect_layout_prefers_runtime_over_spt_and_warns(tmp_path: Path) -> None:
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
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    assert "BepInEx" in str(exc_info.value)


def test_detect_layout_rejects_a_flat_spt_3x_installation(tmp_path: Path) -> None:
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
    layout = GameLayout(root=Path("/game"), runtime_dir_name=runtime_dir_name)

    assert layout.runtime_dir == Path(runtime_dir_name)
    assert layout.mods_dir == Path(runtime_dir_name) / "user" / "mods"
    assert layout.profiles_dir == Path(runtime_dir_name) / "user" / "profiles"
    assert (
        layout.profile_backups_dir
        == Path(runtime_dir_name) / "user" / "profiles" / "backups"
    )
    assert (
        layout.core_config_file
        == Path(runtime_dir_name) / "SPT_Data" / "configs" / "core.json"
    )
    assert layout.server_exe == Path(runtime_dir_name) / "SPT.Server.exe"


# ============================================================================
# read_core_json_version
# ============================================================================


def _layout(tmp_path: Path, runtime_dir_name: str = "SPT_Runtime") -> GameLayout:
    (tmp_path / "game" / runtime_dir_name / "SPT_Data" / "configs").mkdir(
        parents=True, exist_ok=True
    )
    return GameLayout(root=tmp_path / "game", runtime_dir_name=runtime_dir_name)


def _write_core_json(layout: GameLayout, content: str) -> None:
    (layout.root / layout.core_config_file).write_text(content, encoding="utf-8")


def test_read_core_json_version_returns_the_field_when_present(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "4.1.0"}))

    assert read_core_json_version(layout) == "4.1.0"


def test_read_core_json_version_none_for_a_4x_style_config_without_the_key(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _write_core_json(
        layout, json.dumps({"projectName": "SPT", "compatibleTarkovVersion": "1.0"})
    )

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_when_file_missing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_when_json_malformed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write_core_json(layout, "{not valid json")

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_for_an_empty_string_value(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "   "}))

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_for_a_valid_json_non_object_root(
    tmp_path: Path,
) -> None:
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
def test_read_pe_file_version_reads_the_version_resource(
    tmp_path: Path, key_name: str, version: str
) -> None:
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(build_synthetic_exe(version, key_name))

    assert read_pe_file_version(exe) == version


def test_read_pe_file_version_none_for_garbage_bytes(tmp_path: Path) -> None:
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(b"not a PE file at all, just some junk bytes" * 4)

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_for_a_missing_file(tmp_path: Path) -> None:
    assert read_pe_file_version(tmp_path / "does-not-exist.exe") is None


# ============================================================================
# Repo.config_get / config_set
# ============================================================================


def test_config_get_returns_none_for_an_unset_key(initialized_repo: Repo) -> None:
    assert initialized_repo.config_get("sptmod.unset-key") is None


def test_config_set_then_get_roundtrips_the_value(initialized_repo: Repo) -> None:
    initialized_repo.config_set("sptmod.testkey", "hello-value")

    assert initialized_repo.config_get("sptmod.testkey") == "hello-value"


def test_config_set_overwrites_an_existing_value(initialized_repo: Repo) -> None:
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
    assert read_stored_version(initialized_repo) is None


def test_store_then_read_stored_version_roundtrips(initialized_repo: Repo) -> None:
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
    layout, repo = _build_layout_and_repo(
        tmp_path, core_version=core_version, exe_version=exe_version
    )
    if stored is not None:
        store_spt_version(repo, stored)

    assert resolve_spt_version(layout, repo) == expected


# ============================================================================
# set_spt_version (service)
# ============================================================================


def test_set_spt_version_stores_and_returns_the_stripped_value(
    initialized_repo: Repo,
) -> None:
    result = services.set_spt_version(initialized_repo.path, "  4.1.2  ")

    assert result == "4.1.2"
    assert read_stored_version(initialized_repo) == "4.1.2"


def test_set_spt_version_raises_value_error_for_an_empty_version(
    initialized_repo: Repo,
) -> None:
    with pytest.raises(ValueError, match="empty"):
        services.set_spt_version(initialized_repo.path, "")


def test_set_spt_version_raises_not_a_game_dir_error_for_a_non_game_dir(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "not-a-game-dir"
    empty.mkdir()

    with pytest.raises(NotAGameDirError):
        services.set_spt_version(empty, "4.1.2")


def test_set_spt_version_raises_git_error_when_not_yet_a_repository(
    fake_game_dir: Path,
) -> None:
    with pytest.raises(GitError):
        services.set_spt_version(fake_game_dir, "4.1.2")


# ============================================================================
# set_spt_version over the rpc adapter
# ============================================================================


def test_rpc_set_spt_version_stores_and_returns_the_stripped_value(
    fake_game_dir: Path,
) -> None:
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
    result = runner.invoke(
        cli_app,
        ["init", str(fake_game_dir_runtime), "--spt-version", "4.1.2"],
    )

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout


def test_cli_init_without_spt_version_prompts_and_stores_the_answer(
    fake_game_dir_runtime: Path,
) -> None:
    result = runner.invoke(
        cli_app, ["init", str(fake_game_dir_runtime)], input="4.1.2\n"
    )

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout


def test_cli_init_reports_a_warning_when_both_runtime_dirs_are_present(
    fake_game_dir_runtime: Path,
) -> None:
    (fake_game_dir_runtime / "SPT").mkdir()
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = runner.invoke(cli_app, ["init", str(fake_game_dir_runtime)])

    assert result.exit_code == 0
    assert "SPT_Runtime" in result.stdout
    assert "SPT" in result.stdout


# ============================================================================
# End-to-end on the SPT_Runtime layout
# ============================================================================


def test_init_on_runtime_layout_tracks_only_the_whitelist_and_runtime_profile(
    fake_game_dir_runtime: Path,
) -> None:
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = services.init_game_dir(fake_game_dir_runtime)

    assert result.spt_version == "4.1.2"
    committed = {
        f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)
    }
    assert committed == {
        Path(".gitignore"),
        Path("SPT_Runtime/user/profiles/abc.json"),
    }


def test_init_on_runtime_layout_ignored_dummies_are_absent_from_the_commit(
    fake_game_dir_runtime: Path,
) -> None:
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = services.init_game_dir(fake_game_dir_runtime)

    committed = {
        f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)
    }
    ignored = {
        Path("SPT_Runtime/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT_Runtime/user/profiles/backups/old.json"),
        Path("SPT_Runtime/SPT_Data/configs/core.json"),
    }
    assert committed.isdisjoint(ignored)


def test_install_from_archive_translates_the_spt_prefix_to_the_runtime_dir(
    fake_game_dir_runtime: Path, make_archive: MakeArchive
) -> None:
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    services.init_game_dir(fake_game_dir_runtime)
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "me"}
            )
        },
        "zip",
    )

    result = services.install_from_archive(fake_game_dir_runtime, archive)

    assert result.files == [Path("SPT_Runtime/user/mods/CoolMod/package.json")]
    assert (
        fake_game_dir_runtime / "SPT_Runtime/user/mods/CoolMod/package.json"
    ).exists()
    assert not (fake_game_dir_runtime / "SPT/user/mods/CoolMod/package.json").exists()


def test_list_mods_and_status_work_on_the_runtime_layout(
    fake_game_dir_runtime: Path, make_archive: MakeArchive
) -> None:
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

    assert [mod.name for mod in services.list_mods(fake_game_dir_runtime)] == [
        "CoolMod"
    ]

    profile_path = fake_game_dir_runtime / "SPT_Runtime/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    report = services.status(fake_game_dir_runtime)

    assert report.modified_profiles == [
        Path("SPT_Runtime/user/profiles/abc.json")
    ]


def test_check_profiles_finds_residue_on_the_runtime_layout(
    fake_game_dir_runtime: Path, install_commit: InstallCommit
) -> None:
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    repo = Repo.init(detect_layout(fake_game_dir_runtime))
    repo.commit(
        "chore: clean SPT 4.1.2", trailers={"Spt-Version": "4.1.2"}, paths=None
    )
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
    profile_path.write_text(
        json.dumps(
            {
                "characters": {
                    "pmc": {
                        "Inventory": {
                            "items": [
                                {
                                    "_id": "i1",
                                    "_tpl": item_id,
                                    "parentId": "5c0d1b8b86f7744bb2683c99",
                                    "slotId": "hideout",
                                }
                            ]
                        },
                        "TradersInfo": {},
                        "Quests": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    repo.commit(
        "chore: profile snapshot",
        paths=[Path("SPT_Runtime/user/profiles/abc.json")],
    )

    result = services.check_profiles(fake_game_dir_runtime)

    assert set(result.residues.keys()) == {"ServerMod"}
