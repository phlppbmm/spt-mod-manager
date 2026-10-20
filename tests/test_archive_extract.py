"""Tests for ``sptmod.core.archive.extract``.

Covers the zip and 7z roundtrip, rejection of an unknown suffix, path
traversal inside a zip and a 7z, encrypted zip/7z archives, the
rar-without-backend error path, a real rar roundtrip against sample
archives from the rarfile 4.5 test suite (tests/data/), and the BCJ2-filtered
7z fallback to libarchive (roundtrip, executable bits, path traversal, missing
binding).
Whichever of the two rar cases applies depends on whether this machine
actually has a rar backend, via rar_backend_available().
"""

import contextlib
import os
import sys
from pathlib import Path

import py7zr
import pytest
import rarfile

from fixture_types import MakeArchive
from sptmod.core.archive import (
    LIBARCHIVE_BINDING_HINT,
    LIBARCHIVE_INSTALL_HINT,
    RAR_INSTALL_HINT,
    extract,
    rar_backend_available,
)
from sptmod.core.errors import ArchiveError


@pytest.mark.parametrize("fmt", ["zip", "7z"])
def test_extract_roundtrip_lands_files_with_content(make_archive: MakeArchive, tmp_path: Path, fmt: str) -> None:
    """extract() writes archive members to disk with exact content preserved."""
    archive = make_archive(
        {
            "package.json": '{"name": "CoolMod"}',
            "sub/dir/mod.dll": "binary-ish-content",
        },
        fmt,
    )
    dest = tmp_path / "dest"
    dest.mkdir()

    extract(archive, dest)

    assert (dest / "package.json").read_text(encoding="utf-8") == ('{"name": "CoolMod"}')
    assert (dest / "sub" / "dir" / "mod.dll").read_text(encoding="utf-8") == ("binary-ish-content")


# py7zr does not sanitize member names on write (verified: writing with
# arcname="../evil.txt" round-trips through getnames() unchanged), so the
# relative-traversal case is parametrized over both zip and 7z. The
# absolute-path case stays zip-only: make_archive's 7z staging step joins
# the staging dir with the member's relpath via Path.__truediv__, and an
# absolute relpath like "/etc/evil.txt" makes that join discard the staging
# dir entirely (Path("/tmp/x") / "/etc/evil.txt" == Path("/etc/evil.txt")),
# which would try to stage the fixture file itself outside tmp_path -- a
# fixture-construction hazard, not something worth reproducing here.
@pytest.mark.parametrize("fmt", ["zip", "7z"])
def test_extract_traversal_member_is_rejected_before_writing_outside(
    make_archive: MakeArchive, tmp_path: Path, fmt: str
) -> None:
    """Path-traversal members (e.g. ../evil.txt) are rejected before extraction."""
    archive = make_archive({"../evil.txt": "pwned"}, fmt)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError):
        extract(archive, dest)

    assert not (tmp_path / "evil.txt").exists()
    assert not (dest.parent / "evil.txt").exists()


def test_extract_zip_traversal_absolute_member_is_rejected(make_archive: MakeArchive, tmp_path: Path) -> None:
    """Absolute-path members (e.g. /etc/evil.txt) are rejected before extraction."""
    archive = make_archive({"/etc/evil.txt": "pwned"}, "zip")
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError):
        extract(archive, dest)

    assert not Path("/etc/evil.txt").exists()


# Pre-built once via `pyzipper.AESZipFile(..., encryption=pyzipper.WZ_AES)`
# (pyzipper is not a project dependency, so the fixture is committed instead
# of generated at test time) containing a single member "secret.txt" with
# password "secret123". Verified: stdlib zipfile.namelist() succeeds without
# the password (names are not encrypted), but extractall() without it raises
# a raw RuntimeError -- exactly the escape this test guards against.
def test_extract_encrypted_zip_raises_archive_error_not_runtime_error(
    tmp_path: Path,
) -> None:
    """extract() maps raw RuntimeError from encrypted zip to ArchiveError.

    Uses a pre-built encrypted.zip (AES-encrypted with password "secret123").
    Calling extract() without password internally gets RuntimeError from zipfile,
    which must be caught and re-raised as ArchiveError.
    """
    archive = Path(__file__).parent / "data" / "encrypted.zip"
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError):
        extract(archive, dest)


def test_extract_encrypted_7z_raises_archive_error_not_password_required(
    tmp_path: Path,
) -> None:
    """extract() maps py7zr.PasswordRequired from encrypted 7z to ArchiveError.

    Creates an encrypted .7z file (password "secret123") using py7zr. Calling
    extract() must catch py7zr.PasswordRequired and raise ArchiveError instead.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    plain = stage / "secret.txt"
    plain.write_text("protected-content", encoding="utf-8")

    archive = tmp_path / "encrypted.7z"
    with py7zr.SevenZipFile(archive, "w", password="secret123") as seven_zip:
        seven_zip.write(plain, arcname="secret.txt")

    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError):
        extract(archive, dest)


@pytest.mark.skipif(rar_backend_available(), reason="a rar backend is present on this machine")
def test_extract_rar_without_backend_raises_helpful_archive_error(
    tmp_path: Path,
) -> None:
    """ArchiveError for .rar files without a backend includes install hints."""
    archive = tmp_path / "archive.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00placeholder-not-a-real-rar")
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)
    assert "install" in str(excinfo.value).lower()


# Real sample archives from the rarfile 4.5 test suite (tests/data/), used
# because building a genuine .rar needs the proprietary 'rar' CLI tool, which
# make_archive does not provide. Both share the same member layout:
#   sub/dir1/file1.txt        (6 bytes)
#   sub/dir2/file2.txt        (6 bytes)
#   sub/with space/long fn.txt (8 bytes)
#   sub/üȵĩöḋè/file.txt        (5 bytes)
#   sub/, sub/empty/, sub/dir1/, sub/dir2/, sub/with space/, sub/üȵĩöḋè/ (dirs)
# as confirmed via `bsdtar -tvf tests/data/rar5-subdirs.rar`.
RAR_SAMPLE_FILE_SIZES = {
    Path("sub/dir1/file1.txt"): 6,
    Path("sub/dir2/file2.txt"): 6,
    Path("sub/with space/long fn.txt"): 8,
    Path("sub/üȵĩöḋè/file.txt"): 5,
}
RAR_SAMPLE_DIRS = [
    Path("sub"),
    Path("sub/empty"),
    Path("sub/dir1"),
    Path("sub/dir2"),
    Path("sub/with space"),
    Path("sub/üȵĩöḋè"),
]


@pytest.mark.skipif(not rar_backend_available(), reason="no rar backend (unrar/bsdtar) available")
@pytest.mark.parametrize("sample_name", ["rar3-subdirs.rar", "rar5-subdirs.rar"])
def test_extract_rar_roundtrip_lands_files_with_content(tmp_path: Path, sample_name: str) -> None:
    """Real .rar archives are extracted correctly (rar3 and rar5 formats)."""
    archive = Path(__file__).parent / "data" / sample_name
    dest = tmp_path / "dest"
    dest.mkdir()

    extract(archive, dest)

    for relpath, size in RAR_SAMPLE_FILE_SIZES.items():
        extracted = dest / relpath
        content = extracted.read_bytes()
        assert len(content) == size
        assert content != b""

    for relpath in RAR_SAMPLE_DIRS:
        assert (dest / relpath).is_dir()


# --- format detection from magic bytes (regression for the WTT - Content
# Backport bug: a Forge download link ending in a bare version segment, e.g.
# '.../2.0.1', becomes the local file name via download()'s URL-path
# fallback, and its trailing '.1' suffix was wrongly rejected as an
# unsupported format) --------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "misleading_name"),
    [
        ("zip", "2.0.1"),
        ("zip", "download"),
        ("zip", "WTT-Content-Backport-2.0.1"),
        ("zip", "mislabeled.7z"),
        ("7z", "2.0.1"),
        ("7z", "download"),
        ("7z", "WTT-Content-Backport-2.0.1"),
        ("7z", "mislabeled.zip"),
    ],
)
def test_extract_detects_format_from_magic_bytes_regardless_of_file_name(
    make_archive: MakeArchive, tmp_path: Path, fmt: str, misleading_name: str
) -> None:
    """Covers both a version-like/missing suffix (no dot, or a dot followed
    by digits that collide with no known suffix) and a suffix that names the
    *other* supported format outright. Either way extract() must dispatch on
    the magic bytes it reads from the file, not on the name it was given.
    """
    marker = f"content-for-{fmt}-{misleading_name}"
    archive = make_archive({"package.json": marker}, fmt)
    renamed = tmp_path / misleading_name
    renamed.write_bytes(archive.read_bytes())
    dest = tmp_path / "dest"
    dest.mkdir()

    extract(renamed, dest)

    assert (dest / "package.json").read_text(encoding="utf-8") == marker


@pytest.mark.skipif(not rar_backend_available(), reason="no rar backend (unrar/bsdtar) available")
@pytest.mark.parametrize("misleading_name", ["2.0.1", "download"])
def test_extract_rar_detects_format_from_magic_bytes_regardless_of_file_name(
    tmp_path: Path, misleading_name: str
) -> None:
    """extract() detects .rar format from magic bytes despite misleading filename.

    Uses real rar5-subdirs.rar sample renamed to "2.0.1" or "download" (no suffix).
    extract() must dispatch on magic bytes (Rar!\\x1a\\x07), not filename, and
    extract file1.txt correctly (6 bytes).
    """
    sample = Path(__file__).parent / "data" / "rar5-subdirs.rar"
    renamed = tmp_path / misleading_name
    renamed.write_bytes(sample.read_bytes())
    dest = tmp_path / "dest"
    dest.mkdir()

    extract(renamed, dest)

    extracted = dest / "sub" / "dir1" / "file1.txt"
    content = extracted.read_bytes()
    assert len(content) == 6
    assert content != b""


def test_extract_unknown_suffix_error_names_supported_formats(tmp_path: Path) -> None:
    """extract() error message for unknown format lists all supported formats.

    Creates archive.tar (no valid magic bytes) and calls extract(). Error message
    must exactly match "unsupported archive format '.tar' (archive.tar); supported:
    .7z, .zip, .rar" to help users know what formats work.
    """
    archive = tmp_path / "archive.tar"
    archive.write_bytes(b"not a real archive")
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert str(excinfo.value) == ("unsupported archive format '.tar' (archive.tar); supported: .7z, .zip, .rar")


def test_extract_rar_without_backend_on_path_raises_archive_error_naming_the_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH is the real process-environment boundary rarfile's tool
    discovery reads; emptying it and forcing rediscovery reproduces "no
    backend installed" without needing to actually uninstall anything, even
    on a machine (like this sandbox) that normally has one."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    # Forcing rediscovery with an empty PATH is expected to find nothing.
    with contextlib.suppress(rarfile.RarCannotExec):
        rarfile.tool_setup(force=True)
    try:
        assert rar_backend_available() is False

        archive = Path(__file__).parent / "data" / "rar5-subdirs.rar"
        dest = tmp_path / "dest"
        dest.mkdir()

        with pytest.raises(ArchiveError) as excinfo:
            extract(archive, dest)

        assert RAR_INSTALL_HINT in str(excinfo.value)
    finally:
        # Restore PATH before forcing rediscovery, so later tests (and the
        # rest of the suite) see the real backend again.
        monkeypatch.undo()
        rarfile.tool_setup(force=True)


@pytest.mark.skipif(not rar_backend_available(), reason="no rar backend (unrar/bsdtar) available")
def test_extract_rar_raises_archive_error_when_the_destination_is_read_only(
    tmp_path: Path,
) -> None:
    """extract() raises ArchiveError when destination directory is read-only.

    Uses real rar5-subdirs.rar and a destination directory chmod(0o500). extract()
    must raise ArchiveError when rarfile cannot write, and error message must
    contain the filename "rar5-subdirs.rar" to identify which archive failed.
    """
    archive = Path(__file__).parent / "data" / "rar5-subdirs.rar"
    dest = tmp_path / "dest"
    dest.mkdir()
    os.chmod(dest, 0o500)
    try:
        with pytest.raises(ArchiveError) as excinfo:
            extract(archive, dest)

        assert "rar5-subdirs.rar" in str(excinfo.value)
    finally:
        os.chmod(dest, 0o700)


def test_extract_non_archive_with_no_suffix_raises_archive_error(
    tmp_path: Path,
) -> None:
    """extract() raises ArchiveError for non-archive data with missing suffix.

    Creates a file with no suffix (not-an-archive-at-all) containing random bytes
    (no magic bytes). extract() must raise ArchiveError; message must include both
    the filename and the list "supported: .7z, .zip, .rar".
    """
    archive = tmp_path / "not-an-archive-at-all"
    archive.write_bytes(b"just some plain bytes, no magic number here")
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    message = str(excinfo.value)
    assert "not-an-archive-at-all" in message
    assert "supported: .7z, .zip, .rar" in message


# --- unreadable archive_path: magic-byte sniffing and the extractor's own
# open both fail with OSError, not a format-specific parse error -------------


def test_extract_zip_named_directory_raises_archive_error_naming_the_directory(
    tmp_path: Path,
) -> None:
    """``archive_path`` cannot even be opened for magic-byte sniffing (it is
    a directory): ``_detect_format`` falls back to the ``.zip`` suffix, and
    ``zipfile.ZipFile`` then fails to open it too -- both via the ``OSError``
    branch, not the format-specific error branches covered elsewhere."""
    archive = tmp_path / "archive.zip"
    archive.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "cannot read zip archive" in str(excinfo.value)


def test_extract_corrupt_zip_with_valid_magic_bytes_raises_archive_error(
    tmp_path: Path,
) -> None:
    """extract() raises ArchiveError for corrupt zip (bad central directory).

    Creates archive.zip with valid PK\\x03\\x04 magic bytes followed by random data
    (no valid central directory). extract() must detect the format from magic bytes,
    attempt to open with zipfile, and raise ArchiveError on BadZipFile.
    """
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"PK\x03\x04" + b"not a real zip central directory" * 4)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "cannot read zip archive" in str(excinfo.value)


# --- BCJ2-filtered 7z: py7zr raises UnsupportedCompressionMethodError, the
# libarchive fallback reads it instead. bcj2-mod.7z / bcj2-traversal.7z are
# real 7z archives (built with 7z's default BCJ2-filtered "ultra" preset for
# an executable, verified above to make py7zr raise that exact exception on
# extractall()) committed to tests/data/ because building one needs the
# proprietary 7-Zip CLI, which make_archive does not provide. ---------------


def test_extract_bcj2_7z_falls_back_to_libarchive_and_reads_correctly(tmp_path: Path) -> None:
    """extract() reads a BCJ2-filtered 7z (py7zr cannot) via the libarchive fallback.

    bcj2-mod.7z holds three files (doorstop_config.ini, BepInEx/core/BepInEx.dll,
    BepInEx/plugins/spt/x.dll) plus directory entries; every file must land with
    its exact content and every directory entry must exist as a directory.
    """
    archive = Path(__file__).parent / "data" / "bcj2-mod.7z"
    dest = tmp_path / "dest"
    dest.mkdir()

    extract(archive, dest)

    assert (dest / "doorstop_config.ini").read_text(encoding="utf-8") == "[General]\nenabled=true\n"
    core_dll = (dest / "BepInEx" / "core" / "BepInEx.dll").read_bytes()
    assert len(core_dll) == 724
    assert core_dll.startswith(b"MZ\x90\x00bepinex core stub\n")
    plugin_dll = (dest / "BepInEx" / "plugins" / "spt" / "x.dll").read_bytes()
    assert len(plugin_dll) == 644
    assert plugin_dll.startswith(b"MZ\x90\x00spt client stub\n")
    assert (dest / "BepInEx" / "core").is_dir()
    assert (dest / "BepInEx" / "plugins" / "spt").is_dir()


def test_extract_bcj2_7z_keeps_the_executable_bit_the_archive_records(tmp_path: Path) -> None:
    """The libarchive fallback restores the executable bits each member carries.

    bcj2-exec.7z holds ``SPT_Runtime/SPT.Server.Linux`` stored as 0755 and
    ``SPT_Runtime/config.json`` stored as 0644, the shape of the real SPT
    release. After extraction the server is executable by everyone who may read
    it and the config is executable by nobody: the published releases go
    through this fallback, and a server that lost its bit here no longer starts.
    """
    archive = Path(__file__).parent / "data" / "bcj2-exec.7z"
    dest = tmp_path / "dest"
    dest.mkdir()

    extract(archive, dest)

    assert (dest / "SPT_Runtime/SPT.Server.Linux").stat().st_mode & 0o111 == 0o111
    assert (dest / "SPT_Runtime/config.json").stat().st_mode & 0o111 == 0


def test_extract_bcj2_7z_traversal_member_is_rejected_and_writes_nothing(tmp_path: Path) -> None:
    """The libarchive fallback validates member paths before writing, exactly
    like the py7zr/zipfile paths: bcj2-traversal.7z holds a single member
    named ``../evil.dll``, which must be rejected before any byte is written,
    neither inside dest nor as a sibling of it.
    """
    archive = Path(__file__).parent / "data" / "bcj2-traversal.7z"
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "unsafe path in archive (path traversal): ../evil.dll" in str(excinfo.value)
    assert not any(dest.iterdir())
    assert not (tmp_path / "evil.dll").exists()


def test_extract_bcj2_7z_raises_archive_error_when_a_target_file_is_blocked(
    tmp_path: Path,
) -> None:
    """extract() raises ArchiveError, not a raw OSError, when the libarchive
    fallback cannot write one of the archive's files because a directory
    already occupies that path.

    py7zr's own extraction attempt (writing the plain-stored files and
    creating every directory before hitting the BCJ2 entries) completes
    without touching that path, so the ``OSError`` this reproduces comes from
    inside the libarchive fallback's own write loop, not from py7zr.
    """
    archive = Path(__file__).parent / "data" / "bcj2-mod.7z"
    dest = tmp_path / "dest"
    # BepInEx/core/BepInEx.dll comes first among the BCJ2 files (per
    # getnames() order) and is where py7zr's own extractall() call aborts
    # with UnsupportedCompressionMethodError before writing anything else, so
    # blocking a *later* file's path lets py7zr's attempt fail for the
    # BCJ2 reason untouched, and the libarchive fallback -- an independent,
    # full second pass over the archive -- is what then actually reaches and
    # trips over the blocked path.
    blocked = dest / "BepInEx" / "plugins" / "spt" / "x.dll"
    blocked.mkdir(parents=True)

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "bcj2-mod.7z" in str(excinfo.value)


def test_extract_bcj2_7z_without_the_libarchive_package_names_the_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the ``libarchive`` Python package is absent, extract() must say that
    sptmod's installation lacks a dependency -- not send the player after the
    system library, which they may well have. Setting the module to ``None`` in
    ``sys.modules`` makes the next ``import`` of it fail as an absent module
    would, without the package having to be missing from this environment.
    """
    monkeypatch.setitem(sys.modules, "libarchive", None)
    archive = Path(__file__).parent / "data" / "bcj2-mod.7z"
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert LIBARCHIVE_BINDING_HINT in str(excinfo.value)
    assert LIBARCHIVE_INSTALL_HINT not in str(excinfo.value)


def test_extract_bcj2_7z_without_the_libarchive_system_library_names_the_system_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the package is present but its system library cannot be loaded,
    extract() must name the system package to install. The binding reads the
    library path from ``LIBARCHIVE``; pointing that at a file that does not
    exist makes loading fail as a missing library does, and dropping the
    already imported binding makes the next import load it afresh.
    """
    for name in [module for module in sys.modules if module.partition(".")[0] == "libarchive"]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setenv("LIBARCHIVE", str(tmp_path / "missing" / "libarchive.so"))
    archive = Path(__file__).parent / "data" / "bcj2-mod.7z"
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert LIBARCHIVE_INSTALL_HINT in str(excinfo.value)


def test_extract_corrupt_7z_with_valid_magic_bytes_raises_archive_error(
    tmp_path: Path,
) -> None:
    """extract() raises ArchiveError for corrupt 7z (bad header/body).

    Creates archive.7z with valid 7z\\xbc\\xaf\\x27\\x1c magic bytes followed by
    random data (no valid 7z header or body). extract() must detect the format
    from magic bytes and raise ArchiveError on py7zr parse failure.
    """
    archive = tmp_path / "archive.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"not a real 7z header or body" * 4)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "cannot read 7z archive" in str(excinfo.value)
