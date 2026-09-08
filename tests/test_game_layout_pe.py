"""Tests for ``read_pe_file_version`` (spt-mod-manager-fyx).

Covers the best-effort PE/VS_VERSIONINFO parser against a synthetic PE built
in-test: the sanity check that the builder produces a parseable image, the
happy path (``FileVersion`` with a ``ProductVersion`` fallback), and the
malformed/truncated PE and resource-directory corruptions that must all
degrade to ``None``. Split out of :mod:`test_game_layout`, which keeps
``detect_layout`` and the game-directory accessors.
"""

import struct
from collections.abc import Callable
from pathlib import Path

import pytest

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
    pad4,
    utf16z,
    version_block,
)
from sptmod.core.game_dir import read_pe_file_version


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
