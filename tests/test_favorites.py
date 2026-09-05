"""Tests for ``sptmod.core.favorites``: file-based favorite mod id storage."""

import json
from pathlib import Path

import pytest

from sptmod.core.favorites import (
    FAVORITES_FILE_NAME,
    default_favorites_path,
    load_favorites,
    save_favorites,
)

# --- default_favorites_path ------------------------------------------------


def test_default_favorites_path_honors_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the body is `pass`, this fails: result is None, not the expected path."""
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    result = default_favorites_path()

    assert result == config_home / "sptmod" / "favorites.json"


def test_favorites_file_name_constant_is_favorites_json() -> None:
    """The FAVORITES_FILE_NAME constant is exactly "favorites.json".

    Asserts the constant value to catch accidental renames.
    """
    assert FAVORITES_FILE_NAME == "favorites.json"


# --- load_favorites ----------------------------------------------------------


def test_load_favorites_returns_empty_list_for_a_missing_file_without_creating_it(
    tmp_path: Path,
) -> None:
    """If the body is `pass`, this fails: returns None instead of []."""
    path = tmp_path / "favorites.json"

    result = load_favorites(path)

    assert result == []
    assert not path.exists()


def test_load_favorites_returns_sorted_unique_ids(tmp_path: Path) -> None:
    """If the body is `pass`, this fails: returns None instead of [1, 3, 5]."""
    path = tmp_path / "favorites.json"
    path.write_text(json.dumps([5, 1, 3, 1, 5]), encoding="utf-8")

    assert load_favorites(path) == [1, 3, 5]


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "",
        json.dumps({"1": True}),
        json.dumps([1, "two", 3]),
        json.dumps([1, True, 3]),
        json.dumps([1, None]),
        json.dumps([1.5]),
    ],
    ids=[
        "invalid-json",
        "empty-file",
        "json-object",
        "string-member",
        "bool-member",
        "null-member",
        "float-member",
    ],
)
def test_load_favorites_treats_malformed_content_as_an_empty_list(tmp_path: Path, content: str) -> None:
    """If the malformed-content guards are dropped, this fails: a TypeError or
    ValueError propagates, or a garbage value like True or "two" survives into
    the returned list, instead of the whole file being treated as empty."""
    path = tmp_path / "favorites.json"
    path.write_text(content, encoding="utf-8")

    assert load_favorites(path) == []


def test_load_favorites_does_not_crash_on_an_unreadable_directory_instead_of_a_file(
    tmp_path: Path,
) -> None:
    """If the OSError guard is dropped, this fails: an OSError propagates
    instead of being reported as an empty list."""
    directory_as_path = tmp_path / "favorites.json"
    directory_as_path.mkdir()

    assert load_favorites(directory_as_path) == []


# --- save_favorites ------------------------------------------------------


def test_save_favorites_round_trips_sorted_and_deduplicated_through_load_favorites(
    tmp_path: Path,
) -> None:
    """If the body is `pass`, this fails: load_favorites still reports []."""
    path = tmp_path / "favorites.json"

    save_favorites(path, [3, 1, 2, 1])

    assert load_favorites(path) == [1, 2, 3]


def test_save_favorites_creates_missing_parent_directories(tmp_path: Path) -> None:
    """If the parent-directory creation is dropped, this fails: save_favorites
    raises FileNotFoundError instead of writing the file."""
    path = tmp_path / "nested" / "dir" / "favorites.json"

    save_favorites(path, [42])

    assert load_favorites(path) == [42]


def test_save_favorites_leaves_only_the_target_file_behind_no_temp_file(
    tmp_path: Path,
) -> None:
    """If the temp-file cleanup/replace is broken, this fails: a leftover
    `.favorites-*` temp file remains alongside favorites.json."""
    path = tmp_path / "favorites.json"

    save_favorites(path, [1])

    assert [entry.name for entry in tmp_path.iterdir()] == [FAVORITES_FILE_NAME]


def test_save_favorites_cleans_up_its_temp_file_when_the_replace_fails(
    tmp_path: Path,
) -> None:
    """If the except/cleanup branch were dropped, this fails: a leftover
    `.favorites-*` temp file remains in the directory after the OSError."""
    path = tmp_path / "favorites.json"
    path.mkdir()  # os.replace(temp, path) fails: path is a directory, not a file

    with pytest.raises(OSError):
        save_favorites(path, [1])

    assert [entry.name for entry in tmp_path.iterdir()] == [path.name]


def test_save_favorites_overwrites_a_previous_list_entirely(tmp_path: Path) -> None:
    """If the body is `pass`, this fails: load_favorites still returns the
    first list [1, 2] instead of the second [9]."""
    path = tmp_path / "favorites.json"
    save_favorites(path, [1, 2])

    save_favorites(path, [9])

    assert load_favorites(path) == [9]
