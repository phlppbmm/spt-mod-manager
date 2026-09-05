"""Tests for ``sptmod.core.archive.extract``.

Covers the zip and 7z roundtrip, rejection of an unknown suffix, path
traversal inside a zip and a 7z, encrypted zip/7z archives, the
rar-without-backend error path, and a real rar roundtrip against sample
archives from the rarfile 4.5 test suite (tests/data/). Whichever of those
two rar cases applies depends on whether this machine actually has a rar
backend, via rar_backend_available().
"""

import contextlib
import os
from pathlib import Path

import pytest
import rarfile

from fixture_types import MakeArchive
from sptmod.core.archive import RAR_INSTALL_HINT, extract, rar_backend_available
from sptmod.core.errors import ArchiveError


@pytest.mark.parametrize("fmt", ["zip", "7z"])
def test_extract_roundtrip_lands_files_with_content(
    make_archive: MakeArchive, tmp_path: Path, fmt: str
) -> None:
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

    assert (dest / "package.json").read_text(encoding="utf-8") == (
        '{"name": "CoolMod"}'
    )
    assert (dest / "sub" / "dir" / "mod.dll").read_text(encoding="utf-8") == (
        "binary-ish-content"
    )


def test_extract_unknown_suffix_raises_archive_error(tmp_path: Path) -> None:
    """extract() raises ArchiveError for unsupported suffix (.tar), error message names the suffix."""
    archive = tmp_path / "archive.tar"
    archive.write_bytes(b"not a real archive")
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)
    assert ".tar" in str(excinfo.value)


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


def test_extract_zip_traversal_absolute_member_is_rejected(
    make_archive: MakeArchive, tmp_path: Path
) -> None:
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
    """Encrypted zip archives raise ArchiveError (not raw RuntimeError)."""
    archive = Path(__file__).parent / "data" / "encrypted.zip"
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError):
        extract(archive, dest)


def test_extract_encrypted_7z_raises_archive_error_not_password_required(
    tmp_path: Path,
) -> None:
    """Encrypted 7z archives raise ArchiveError (not py7zr.PasswordRequired)."""
    import py7zr

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


@pytest.mark.skipif(
    rar_backend_available(), reason="a rar backend is present on this machine"
)
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


@pytest.mark.skipif(
    not rar_backend_available(), reason="no rar backend (unrar/bsdtar) available"
)
@pytest.mark.parametrize("sample_name", ["rar3-subdirs.rar", "rar5-subdirs.rar"])
def test_extract_rar_roundtrip_lands_files_with_content(
    tmp_path: Path, sample_name: str
) -> None:
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


@pytest.mark.skipif(
    not rar_backend_available(), reason="no rar backend (unrar/bsdtar) available"
)
@pytest.mark.parametrize("misleading_name", ["2.0.1", "download"])
def test_extract_rar_detects_format_from_magic_bytes_regardless_of_file_name(
    tmp_path: Path, misleading_name: str
) -> None:
    """Format detection from magic bytes works for .rar even with misleading filenames."""
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
    """Error message for unsupported formats lists all supported formats."""
    archive = tmp_path / "archive.tar"
    archive.write_bytes(b"not a real archive")
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert str(excinfo.value) == (
        "unsupported archive format '.tar' (archive.tar); "
        "supported: .7z, .zip, .rar"
    )


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


@pytest.mark.skipif(
    not rar_backend_available(), reason="no rar backend (unrar/bsdtar) available"
)
def test_extract_rar_raises_archive_error_when_the_destination_is_read_only(
    tmp_path: Path,
) -> None:
    """extract() raises ArchiveError when dest dir is chmod(0o500), error names the archive file."""
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
    """extract() raises ArchiveError; error message names the file and lists supported formats."""
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
    """Corrupt zip archives (with valid magic bytes) raise ArchiveError."""
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"PK\x03\x04" + b"not a real zip central directory" * 4)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "cannot read zip archive" in str(excinfo.value)


def test_extract_corrupt_7z_with_valid_magic_bytes_raises_archive_error(
    tmp_path: Path,
) -> None:
    """Corrupt 7z archives (with valid magic bytes) raise ArchiveError."""
    archive = tmp_path / "archive.7z"
    archive.write_bytes(
        b"7z\xbc\xaf\x27\x1c" + b"not a real 7z header or body" * 4
    )
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError) as excinfo:
        extract(archive, dest)

    assert "cannot read 7z archive" in str(excinfo.value)
