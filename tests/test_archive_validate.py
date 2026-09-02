"""Tests for ``sptmod.core.archive.validate_member_paths``.

Pure string-level checks: no archive needs to be built for these, per the
docstring the function validates raw member names before extraction touches
disk.
"""

import pytest

from sptmod.core.archive import validate_member_paths
from sptmod.core.errors import ArchiveError

UNSAFE_MEMBERS = [
    pytest.param("..", id="bare-dotdot"),
    pytest.param("../evil.txt", id="leading-dotdot"),
    pytest.param("a/../../evil.txt", id="embedded-dotdot-escapes"),
    pytest.param("a/b/../../../evil.txt", id="deep-embedded-dotdot-escapes"),
    pytest.param("/etc/passwd", id="posix-absolute"),
    pytest.param("C:\\evil.txt", id="windows-drive-absolute"),
    pytest.param("\\\\server\\share\\evil.txt", id="windows-unc-absolute"),
    pytest.param("C:evil.txt", id="windows-drive-relative"),
    pytest.param("a\\..\\..\\evil.txt", id="windows-backslash-dotdot"),
]

SAFE_MEMBERS = [
    pytest.param("a/b/c.txt", id="nested-file"),
    pytest.param("package.json", id="top-level-file"),
    pytest.param("SPT/user/mods/CoolMod/package.json", id="server-anchor-path"),
    pytest.param("BepInEx/plugins/CoolPlugin.dll", id="client-anchor-path"),
    pytest.param("weird..name/file.txt", id="dots-in-name-not-traversal"),
    pytest.param("a/b.txt", id="short-nested-file"),
]


@pytest.mark.parametrize("member", UNSAFE_MEMBERS)
def test_validate_member_paths_rejects_unsafe_member(member: str) -> None:
    with pytest.raises(ArchiveError) as excinfo:
        validate_member_paths([member])
    assert member in str(excinfo.value)


def test_validate_member_paths_rejects_unsafe_member_mixed_with_safe_ones() -> None:
    """The unsafe entry is still named even when hidden among safe ones."""
    with pytest.raises(ArchiveError) as excinfo:
        validate_member_paths(["ok/file.txt", "package.json", "../evil.txt"])
    assert "../evil.txt" in str(excinfo.value)


@pytest.mark.parametrize("member", SAFE_MEMBERS)
def test_validate_member_paths_accepts_safe_member(member: str) -> None:
    validate_member_paths([member])


def test_validate_member_paths_accepts_all_safe_members_together() -> None:
    validate_member_paths([p.values[0] for p in SAFE_MEMBERS])


def test_validate_member_paths_accepts_empty_sequence() -> None:
    validate_member_paths([])
