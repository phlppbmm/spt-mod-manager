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
from sptmod.core.errors import InvalidPathError


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
    """None defaults to listing the user's home directory."""
    result = services.list_directories(None)

    assert result.path == Path.home().resolve()


def test_list_directories_lists_subdirectories_only_sorted_hidden_excluded(
    browse_root: Path,
) -> None:
    """Only directories are returned, sorted alphabetically, excluding hidden ones."""
    result = services.list_directories(str(browse_root))

    assert result.path == browse_root.resolve()
    assert result.parent == browse_root.resolve().parent
    assert [entry.name for entry in result.entries] == ["alpha", "zeta"]
    assert all(entry.is_game_dir is False for entry in result.entries)


def test_list_directories_marks_an_entry_that_looks_like_an_spt_install(
    browse_root: Path,
) -> None:
    """Directories containing SPT and BepInEx subdirectories are marked as game directories."""
    game_dir = browse_root / "GameDir"
    (game_dir / "SPT").mkdir(parents=True)
    (game_dir / "BepInEx").mkdir()

    result = services.list_directories(str(browse_root))

    by_name = {entry.name: entry.is_game_dir for entry in result.entries}
    assert by_name["GameDir"] is True
    assert by_name["alpha"] is False


def test_list_directories_parent_is_none_at_the_filesystem_root() -> None:
    """The parent reference is None when listing the root directory."""
    result = services.list_directories("/")

    assert result.parent is None


def test_list_directories_raises_invalid_path_error_for_a_nonexistent_path(
    tmp_path: Path,
) -> None:
    """InvalidPathError is raised when the path does not exist."""
    with pytest.raises(InvalidPathError):
        services.list_directories(str(tmp_path / "does-not-exist"))


def test_list_directories_raises_invalid_path_error_for_a_file(
    tmp_path: Path,
) -> None:
    """InvalidPathError is raised when the path is a file, not a directory."""
    a_file = tmp_path / "not-a-directory.txt"
    a_file.write_text("content", encoding="utf-8")

    with pytest.raises(InvalidPathError):
        services.list_directories(str(a_file))


def test_list_directories_raises_invalid_path_error_for_permission_denied(
    tmp_path: Path,
) -> None:
    """InvalidPathError is raised when directory cannot be read due to permission denial."""
    unreadable = tmp_path / "no-access"
    unreadable.mkdir()
    os.chmod(unreadable, 0o000)
    try:
        with pytest.raises(InvalidPathError):
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
    """Only directories matching both the name and all fingerprint entries are returned."""
    matches = services.locate_directory(
        "SPT", ["BepInEx", "cache"], roots=[locate_root]
    )

    assert len(matches) == 1
    assert matches[0].path == locate_root / "parent_b" / "SPT"
    assert matches[0].is_game_dir is False


def test_locate_directory_empty_fingerprint_matches_by_name_only(
    locate_root: Path,
) -> None:
    """An empty fingerprint results in matching directories by name alone."""
    matches = services.locate_directory("SPT", [], roots=[locate_root])

    assert sorted(match.path for match in matches) == sorted(
        [locate_root / "parent_a" / "SPT", locate_root / "parent_b" / "SPT"]
    )


def test_locate_directory_returns_an_empty_list_when_nothing_matches(
    locate_root: Path,
) -> None:
    """An empty list is returned when no directories match the name and fingerprint."""
    matches = services.locate_directory("NoSuchDirectoryName", [], roots=[locate_root])

    assert matches == []


def test_locate_directory_raises_invalid_path_error_for_a_name_with_a_separator() -> (
    None
):
    """InvalidPathError is raised when the name contains path separators."""
    with pytest.raises(InvalidPathError):
        services.locate_directory("a/b", [])


def test_locate_directory_deduplicates_a_root_passed_more_than_once(
    locate_root: Path,
) -> None:
    """Duplicate roots do not result in duplicate search results."""
    matches = services.locate_directory(
        "SPT", ["BepInEx", "cache"], roots=[locate_root, locate_root]
    )

    assert len(matches) == 1


def test_locate_directory_caps_results_at_ten(tmp_path: Path) -> None:
    """Search results are capped at ten matches."""
    root = tmp_path / "many-matches"
    for i in range(11):
        (root / f"parent_{i}" / "SPT").mkdir(parents=True)

    matches = services.locate_directory("SPT", [], roots=[root])

    assert len(matches) == 10


def test_locate_directory_without_roots_searches_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The home directory is searched when no roots are provided."""
    home = tmp_path / "home"
    marker = home / "SptModManagerLocateMarker9f3"
    marker.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    matches = services.locate_directory("SptModManagerLocateMarker9f3", [])

    assert marker in [match.path for match in matches]


def test_locate_directory_skips_an_unreadable_subdirectory_without_raising(
    tmp_path: Path,
) -> None:
    """Unreadable subdirectories do not interrupt the search."""
    root = tmp_path / "with-unreadable"
    unreadable = root / "no-access"
    unreadable.mkdir(parents=True)
    (root / "SPT").mkdir()
    os.chmod(unreadable, 0o000)
    try:
        matches = services.locate_directory("SPT", [], roots=[root])
    finally:
        os.chmod(unreadable, 0o755)

    assert [match.path for match in matches] == [root / "SPT"]


def test_locate_directory_skips_directories_in_the_skip_list(tmp_path: Path) -> None:
    """Known deep directories like node_modules are excluded from the search."""
    root = tmp_path / "with-node-modules"
    (root / "node_modules" / "SPT").mkdir(parents=True)

    matches = services.locate_directory("SPT", [], roots=[root])

    assert matches == []


def test_locate_directory_does_not_descend_past_the_max_depth(tmp_path: Path) -> None:
    """The search respects the maximum depth limit."""
    root = tmp_path / "deep"
    # Five path segments below root are reached on the fifth (last) walk
    # iteration; a sixth segment is one level too deep to ever be visited.
    within_depth = root / "a" / "b" / "c" / "d" / "SPT"
    beyond_depth = root / "a" / "b" / "c" / "d" / "e" / "SPT"
    within_depth.mkdir(parents=True)
    beyond_depth.mkdir(parents=True)

    matches = services.locate_directory("SPT", [], roots=[root])

    assert [match.path for match in matches] == [within_depth]
