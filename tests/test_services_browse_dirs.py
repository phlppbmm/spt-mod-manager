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
    """list_directories with path=None defaults to the user's home directory.

    Calling list_directories(None) returns result.path == Path.home().resolve().
    """
    result = services.list_directories(None)

    assert result.path == Path.home().resolve()


def test_list_directories_lists_subdirectories_only_sorted_hidden_excluded(
    browse_root: Path,
) -> None:
    """list_directories returns only subdirectories, sorted, with hidden entries excluded.

    From [alpha, zeta, .hidden, note.txt], it returns [alpha, zeta] without
    .hidden or files, sorted alphabetically.
    """
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
    """list_directories returns parent=None when called on the filesystem root.

    Calling list_directories("/") returns a result with parent == None.
    """
    result = services.list_directories("/")

    assert result.parent is None


def test_list_directories_raises_invalid_path_error_for_a_nonexistent_path(
    tmp_path: Path,
) -> None:
    """list_directories raises InvalidPathError when the path does not exist.

    Passing a path that doesn't exist raises InvalidPathError.
    """
    with pytest.raises(InvalidPathError):
        services.list_directories(str(tmp_path / "does-not-exist"))


def test_list_directories_raises_invalid_path_error_for_a_file(
    tmp_path: Path,
) -> None:
    """list_directories raises InvalidPathError when the path is a file.

    Passing a file instead of a directory raises InvalidPathError.
    """
    a_file = tmp_path / "not-a-directory.txt"
    a_file.write_text("content", encoding="utf-8")

    with pytest.raises(InvalidPathError):
        services.list_directories(str(a_file))


def test_list_directories_raises_invalid_path_error_for_permission_denied(
    tmp_path: Path,
) -> None:
    """list_directories raises InvalidPathError when a directory has no read permission.

    A directory with mode 0o000 raises InvalidPathError when passed to
    list_directories.
    """
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
    """locate_directory returns only directories matching the name and containing all fingerprint entries.

    Given roots with parent_a/SPT (no BepInEx) and parent_b/SPT (has BepInEx
    and cache), searching for ("SPT", ["BepInEx", "cache"]) returns only
    parent_b/SPT.
    """
    matches = services.locate_directory("SPT", ["BepInEx", "cache"], roots=[locate_root])

    assert len(matches) == 1
    assert matches[0].path == locate_root / "parent_b" / "SPT"
    assert matches[0].is_game_dir is False


def test_locate_directory_empty_fingerprint_matches_by_name_only(
    locate_root: Path,
) -> None:
    """locate_directory with an empty fingerprint matches all directories by name.

    Searching for ("SPT", []) returns both parent_a/SPT and parent_b/SPT.
    """
    matches = services.locate_directory("SPT", [], roots=[locate_root])

    assert sorted(match.path for match in matches) == sorted(
        [locate_root / "parent_a" / "SPT", locate_root / "parent_b" / "SPT"]
    )


def test_locate_directory_returns_an_empty_list_when_nothing_matches(
    locate_root: Path,
) -> None:
    """locate_directory returns an empty list when no directories match.

    Searching for ("NoSuchDirectoryName", []) returns [].
    """
    matches = services.locate_directory("NoSuchDirectoryName", [], roots=[locate_root])

    assert not matches


def test_locate_directory_raises_invalid_path_error_for_a_name_with_a_separator() -> None:
    """locate_directory raises InvalidPathError when the name contains path separators.

    Passing "a/b" as the name raises InvalidPathError because names must be
    single directory components.
    """
    with pytest.raises(InvalidPathError):
        services.locate_directory("a/b", [])


def test_locate_directory_deduplicates_a_root_passed_more_than_once(
    locate_root: Path,
) -> None:
    """locate_directory deduplicates roots so passing [root, root] yields one result.

    Passing the same root twice does not create duplicate results in the returned list.
    """
    matches = services.locate_directory("SPT", ["BepInEx", "cache"], roots=[locate_root, locate_root])

    assert len(matches) == 1


def test_locate_directory_caps_results_at_ten(tmp_path: Path) -> None:
    """locate_directory returns at most ten results even when more matches exist.

    Creating 11 parent_*/SPT directories and searching for "SPT" returns
    exactly 10 results.
    """
    root = tmp_path / "many-matches"
    for i in range(11):
        (root / f"parent_{i}" / "SPT").mkdir(parents=True)

    matches = services.locate_directory("SPT", [], roots=[root])

    assert len(matches) == 10


def test_locate_directory_without_roots_searches_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """locate_directory without roots defaults to searching the home directory.

    When roots is not provided or empty, the home directory is searched
    automatically.
    """
    home = tmp_path / "home"
    marker = home / "SptModManagerLocateMarker9f3"
    marker.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    matches = services.locate_directory("SptModManagerLocateMarker9f3", [])

    assert marker in [match.path for match in matches]


def test_locate_directory_skips_an_unreadable_subdirectory_without_raising(
    tmp_path: Path,
) -> None:
    """locate_directory skips unreadable subdirectories and continues searching.

    When one subdirectory has mode 0o000, the search skips it and returns
    results from readable directories without raising.
    """
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

    assert not matches


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
