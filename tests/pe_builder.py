"""Synthetic PE32+ builder used by the SPT-version tests (stdlib only).

Builds a minimal PE32+ image with one VS_VERSION_INFO resource carrying a
StringFileInfo/String entry, to the documented Microsoft VS_VERSIONINFO binary
layout (independent of ``read_pe_file_version``'s own parsing) and
cross-checked with ``pefile`` during development. The layout constants are
public because ``test_game_layout`` corrupts a few known bytes of an otherwise
valid image instead of hand-building malformed PEs.
"""

import struct
from collections.abc import Sequence
from pathlib import Path


def utf16z(s: str) -> bytes:
    """Encode a string as UTF-16LE with a null terminator."""
    return (s + "\0").encode("utf-16-le")


def pad4(data: bytes) -> bytes:
    """Pad bytes to the next 4-byte-aligned length with null bytes.

    Used by the synthetic PE builder to align VS_VERSION_INFO block structures.
    """
    return data + b"\0" * ((-len(data)) % 4)


def version_block(key: str, value_word_count: int, value: bytes, children: bytes = b"") -> bytes:
    """Build a single version-info block per Microsoft VS_VERSIONINFO binary layout."""
    key_bytes = utf16z(key)
    head = struct.pack("<HHH", 0, value_word_count, 1 if value else 0) + key_bytes
    body = pad4(head) + pad4(value) if value else pad4(head)
    total = body + children
    return struct.pack("<HHH", len(total), value_word_count, 1) + key_bytes + total[len(head) :]


def build_version_resource_multi(entries: Sequence[tuple[str, str]]) -> bytes:
    """Build a VS_VERSION_INFO resource with one string-table entry per pair
    in ``entries``, in order (so a later entry of the same key would shadow
    an earlier one, the way the real string table does)."""
    fixed_file_info = struct.pack("<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0)
    string_entries = b"".join(
        version_block(key, len(value) + 1, utf16z(value)) for key, value in entries
    )
    string_table = version_block("040904B0", 0, b"", string_entries)
    string_file_info = version_block("StringFileInfo", 0, b"", string_table)
    return version_block("VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, string_file_info)


def build_version_resource(version: str, key_name: str) -> bytes:
    """Build a VS_VERSION_INFO resource with a single string-table entry."""
    return build_version_resource_multi([(key_name, version)])


# Fixed layout constants shared between the builder and the corruption tests
# below: the builder always places a single ".rsrc" section with exactly one
# RT_VERSION/name/language resource-directory chain (level1/level2/level3),
# one IMAGE_RESOURCE_DATA_ENTRY and the VS_VERSION_INFO bytes, in that exact
# order, at these fixed file offsets -- so a corruption test can flip a few
# known bytes of an otherwise-valid image instead of hand-building a whole
# malformed PE from scratch.
SECTION_ALIGNMENT, FILE_ALIGNMENT, HEADERS_SIZE = 0x1000, 0x200, 0x400
RSRC_RVA, RSRC_RAW_OFFSET = SECTION_ALIGNMENT, HEADERS_SIZE
LEVEL_SIZE, DATA_ENTRY_SIZE = 24, 16
HEADER_TOTAL = 3 * LEVEL_SIZE + DATA_ENTRY_SIZE
LEVEL1_FILE_OFFSET = RSRC_RAW_OFFSET
LEVEL2_FILE_OFFSET = LEVEL1_FILE_OFFSET + LEVEL_SIZE
LEVEL3_FILE_OFFSET = LEVEL2_FILE_OFFSET + LEVEL_SIZE
DATA_ENTRY_FILE_OFFSET = LEVEL3_FILE_OFFSET + LEVEL_SIZE
VERSION_DATA_FILE_OFFSET = RSRC_RAW_OFFSET + HEADER_TOTAL
# Within each 24-byte resource-directory level, the id/target II pair sits
# at local offset 16 (id) / 20 (target); "target" carries the subdirectory
# flag (0x80000000) when it points at another directory level rather than
# an IMAGE_RESOURCE_DATA_ENTRY.
LEVEL_TARGET_LOCAL_OFFSET = 20
COFF_FILE_OFFSET = 64
OPTIONAL_FILE_OFFSET = COFF_FILE_OFFSET + 24
SIZE_OF_OPTIONAL_HEADER_FILE_OFFSET = COFF_FILE_OFFSET + 20
# _DATA_DIRECTORY_OFFSETS[0x20B] (PE32+) == 112, resource entry is index 2.
RESOURCE_DIRECTORY_RVA_FILE_OFFSET = OPTIONAL_FILE_OFFSET + 112 + 2 * 8


def build_pe_around_version_data(version_data: bytes) -> bytes:
    """Build a minimal, structurally valid PE32+ image around ``version_data``.

    ``version_data`` is embedded as the leaf of a normal, single-entry
    RT_VERSION/name/language resource-directory chain; the caller may pass
    perfectly valid VS_VERSIONINFO bytes (as :func:`build_version_resource`
    produces), or deliberately malformed ones, to test how the string table
    is parsed once the surrounding PE/resource-directory navigation has
    already succeeded.
    """
    padded_version = pad4(version_data)
    data_rva = RSRC_RVA + HEADER_TOTAL

    level1 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 16, 0x80000000 | LEVEL_SIZE)
    level2 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1, 0x80000000 | (2 * LEVEL_SIZE))
    level3 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1033, 3 * LEVEL_SIZE)
    data_entry = struct.pack("<IIII", data_rva, len(version_data), 0, 0)
    rsrc_section = level1 + level2 + level3 + data_entry + padded_version
    rsrc_raw_size = ((len(rsrc_section) + FILE_ALIGNMENT - 1) // FILE_ALIGNMENT) * FILE_ALIGNMENT
    rsrc_padded = rsrc_section + b"\0" * (rsrc_raw_size - len(rsrc_section))
    image_size = RSRC_RVA + ((len(rsrc_section) + SECTION_ALIGNMENT - 1) // SECTION_ALIGNMENT) * SECTION_ALIGNMENT

    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 60, 64)

    coff = struct.pack("<IHHIIIHH", 0x00004550, 0x8664, 1, 0, 0, 0, 0xF0, 0x0022)

    optional = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B,
        14,
        0,
        0x200,
        0,
        0,
        0,
        0x1000,
        0x140000000,
        SECTION_ALIGNMENT,
        FILE_ALIGNMENT,
        6,
        0,
        1,
        0,
        6,
        0,
        0,
        image_size,
        HEADERS_SIZE,
        0,
        3,
        0,
        0x100000,
        0x1000,
        0x100000,
        0x1000,
        0,
        16,
    )
    data_dirs = bytearray(16 * 8)
    struct.pack_into("<II", data_dirs, 2 * 8, RSRC_RVA, len(rsrc_section))
    optional += bytes(data_dirs)

    section_header = struct.pack(
        "<8sIIIIIIHHI",
        b".rsrc\0\0\0",
        len(rsrc_section),
        RSRC_RVA,
        rsrc_raw_size,
        RSRC_RAW_OFFSET,
        0,
        0,
        0,
        0,
        0x40000040,
    )

    header_blob = bytes(dos) + coff + optional + section_header
    header_blob += b"\0" * (HEADERS_SIZE - len(header_blob))
    return header_blob + rsrc_padded


def build_synthetic_exe(version: str, key_name: str = "FileVersion") -> bytes:
    """Build a minimal PE32+ image whose VS_VERSION_INFO carries ``key_name``."""
    return build_pe_around_version_data(build_version_resource(version, key_name))


def build_synthetic_exe_multi(entries: Sequence[tuple[str, str]]) -> bytes:
    """Build a minimal PE32+ image whose VS_VERSION_INFO carries every
    ``(key, value)`` pair in ``entries`` as its own string-table entry."""
    return build_pe_around_version_data(build_version_resource_multi(entries))


def write_server_exe(root: Path, runtime_dir_name: str, version: str) -> Path:
    """Write a synthetic PE EXE with the given version to the expected server location."""
    exe_path = root / runtime_dir_name / "SPT.Server.exe"
    exe_path.write_bytes(build_synthetic_exe(version))
    return exe_path
