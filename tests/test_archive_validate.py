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

# Keyed by the parametrize id so the "all together" test can reuse the exact
# same member names without going back through ``pytest.param`` objects.
SAFE_MEMBER_PATHS: dict[str, str] = {
    "nested-file": "a/b/c.txt",
    "top-level-file": "package.json",
    "server-anchor-path": "SPT/user/mods/CoolMod/package.json",
    "client-anchor-path": "BepInEx/plugins/CoolPlugin.dll",
    "dots-in-name-not-traversal": "weird..name/file.txt",
    "short-nested-file": "a/b.txt",
}

SAFE_MEMBERS = [
    pytest.param(member, id=member_id)
    for member_id, member in SAFE_MEMBER_PATHS.items()
]


@pytest.mark.parametrize("member", UNSAFE_MEMBERS)
def test_validate_member_paths_rejects_unsafe_member(member: str) -> None:
    """Paths with directory traversal (../) or absolute addressing are rejected."""
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
    """Safe member paths (nested, server/client anchors) are accepted."""
    validate_member_paths([member])


def test_validate_member_paths_accepts_all_safe_members_together() -> None:
    """All safe-path categories (nested, server/client anchors, dotted names) pass together."""
    validate_member_paths(list(SAFE_MEMBER_PATHS.values()))


def test_validate_member_paths_accepts_empty_sequence() -> None:
    """validate_member_paths([]) raises nothing (edge case of empty member list)."""
    validate_member_paths([])
