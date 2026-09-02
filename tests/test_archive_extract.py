"""Tests for ``sptmod.core.archive.extract``.

Covers the zip and 7z roundtrip, rejection of an unknown suffix, path
traversal inside a zip and a 7z, encrypted zip/7z archives, the
rar-without-backend error path, and a real rar roundtrip against sample
archives from the rarfile 4.5 test suite (tests/data/). Whichever of those
two rar cases applies depends on whether this machine actually has a rar
backend, via rar_backend_available().
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from sptmod.core.archive import extract, rar_backend_available
from sptmod.core.errors import ArchiveError

MakeArchive = Callable[[dict[str, str | bytes], str], Path]


@pytest.mark.parametrize("fmt", ["zip", "7z"])
def test_extract_roundtrip_lands_files_with_content(
    make_archive: MakeArchive, tmp_path: Path, fmt: str
) -> None:
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
    archive = Path(__file__).parent / "data" / "encrypted.zip"
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ArchiveError):
        extract(archive, dest)


def test_extract_encrypted_7z_raises_archive_error_not_password_required(
    tmp_path: Path,
) -> None:
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
