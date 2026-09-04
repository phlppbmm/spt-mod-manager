"""Tests for the directory-browsing services.

``list_directories`` lists the directories immediately below a path (or the
user's home directory, without one) for a folder picker; ``locate_directory``
searches a set of injectable roots for directories matching a name and an
optional fingerprint of child entries, which is how the frontend resolves a
browser folder-picker selection back to an absolute, verified path.
"""

import os
from pathlib import Path

import pytest

from sptmod.core import services


@pytest.fixture
def browse_root(tmp_path: Path) -> Path:
    """A directory with two plain subdirectories, a hidden one, and a file."""
    root = tmp_path / "browse-root"
    (root / "alpha").mkdir(parents=True)
    (root / "zeta").mkdir()
    (root / ".hidden").mkdir()
    (root / "note.txt").write_text("not a directory", encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# services.list_directories
# --------------------------------------------------------------------------


def test_list_directories_without_a_path_lists_the_home_directory() -> None:
    result = services.list_directories(None)

    assert result.path == Path.home().resolve()


def test_list_directories_lists_subdirectories_only_sorted_hidden_excluded(
    browse_root: Path,
) -> None:
    result = services.list_directories(str(browse_root))

    assert result.path == browse_root.resolve()
    assert result.parent == browse_root.resolve().parent
    assert [entry.name for entry in result.entries] == ["alpha", "zeta"]
    assert all(entry.is_game_dir is False for entry in result.entries)


def test_list_directories_marks_an_entry_that_looks_like_an_spt_install(
    browse_root: Path,
) -> None:
    game_dir = browse_root / "GameDir"
    (game_dir / "SPT").mkdir(parents=True)
    (game_dir / "BepInEx").mkdir()

    result = services.list_directories(str(browse_root))

    by_name = {entry.name: entry.is_game_dir for entry in result.entries}
    assert by_name["GameDir"] is True
    assert by_name["alpha"] is False


def test_list_directories_parent_is_none_at_the_filesystem_root() -> None:
    result = services.list_directories("/")

    assert result.parent is None


def test_list_directories_raises_invalid_path_error_for_a_nonexistent_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(services.InvalidPathError):
        services.list_directories(str(tmp_path / "does-not-exist"))


def test_list_directories_raises_invalid_path_error_for_a_file(
    tmp_path: Path,
) -> None:
    a_file = tmp_path / "not-a-directory.txt"
    a_file.write_text("content", encoding="utf-8")

    with pytest.raises(services.InvalidPathError):
        services.list_directories(str(a_file))


def test_list_directories_raises_invalid_path_error_for_permission_denied(
    tmp_path: Path,
) -> None:
    unreadable = tmp_path / "no-access"
    unreadable.mkdir()
    os.chmod(unreadable, 0o000)
    try:
        with pytest.raises(services.InvalidPathError):
            services.list_directories(str(unreadable))
    finally:
        os.chmod(unreadable, 0o755)


# --------------------------------------------------------------------------
# services.locate_directory
# --------------------------------------------------------------------------


@pytest.fixture
def locate_root(tmp_path: Path) -> Path:
    """Two directories named 'SPT': only one carries the fingerprint entries.

    CONTRACT CHANGE (spt-mod-manager-fyx): the second fingerprint entry used to
    be an arbitrary name ("SPT_Runtime"); now that name makes is_game_dir()
    true, so it was swapped for a still-arbitrary "cache" to keep this fixture
    a fingerprint match that is deliberately not a real game dir."""
    root = tmp_path / "locate-root"
    (root / "parent_a" / "SPT").mkdir(parents=True)
    (root / "parent_b" / "SPT" / "BepInEx").mkdir(parents=True)
    (root / "parent_b" / "SPT" / "cache").mkdir(parents=True)
    return root


def test_locate_directory_returns_only_the_fingerprint_matching_directory(
    locate_root: Path,
) -> None:
    matches = services.locate_directory(
        "SPT", ["BepInEx", "cache"], roots=[locate_root]
    )

    assert len(matches) == 1
    assert matches[0].path == locate_root / "parent_b" / "SPT"
    assert matches[0].is_game_dir is False


def test_locate_directory_empty_fingerprint_matches_by_name_only(
    locate_root: Path,
) -> None:
    matches = services.locate_directory("SPT", [], roots=[locate_root])

    assert sorted(match.path for match in matches) == sorted(
        [locate_root / "parent_a" / "SPT", locate_root / "parent_b" / "SPT"]
    )


def test_locate_directory_returns_an_empty_list_when_nothing_matches(
    locate_root: Path,
) -> None:
    matches = services.locate_directory("NoSuchDirectoryName", [], roots=[locate_root])

    assert matches == []


def test_locate_directory_raises_invalid_path_error_for_a_name_with_a_separator() -> (
    None
):
    with pytest.raises(services.InvalidPathError):
        services.locate_directory("a/b", [])
