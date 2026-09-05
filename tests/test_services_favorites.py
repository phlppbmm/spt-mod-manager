"""Tests for the favorites service glue in ``sptmod.core.services``.

Covers ``list_favorites`` and ``set_favorite``: an explicit ``favorites_path``
redirects the file exactly like ``db_path`` does for the catalog (see
``isolated_default_db_path`` in ``test_services_catalog.py``); the default
path is exercised by monkeypatching ``platformdirs.user_config_path`` the
same way.
"""

import json
from pathlib import Path

import pytest

from sptmod.core import services
from sptmod.core.favorites import FAVORITES_FILE_NAME, save_favorites
from sptmod.core.forge.catalog import default_db_path

# --- favorites_path plumbing -------------------------------------------


def test_list_favorites_on_an_empty_state_returns_an_empty_list_no_crash(
    tmp_path: Path,
) -> None:
    """When no favorites file exists yet, list_favorites returns an empty list
    (not None, not an exception) without creating the file."""
    path = tmp_path / "favorites.json"

    assert services.list_favorites(favorites_path=path) == []
    assert not path.exists()


def test_set_favorite_true_marks_the_id_as_a_favorite(tmp_path: Path) -> None:
    """set_favorite(7, True) returns [7] (not None) and writes the file, making
    the ID retrievable by list_favorites."""
    path = tmp_path / "favorites.json"

    result = services.set_favorite(7, True, favorites_path=path)

    assert result == [7]


def test_set_favorite_true_twice_is_idempotent(tmp_path: Path) -> None:
    """If the dedup by set() is dropped, this fails: result is [3, 3] instead
    of the single entry [3]."""
    path = tmp_path / "favorites.json"
    services.set_favorite(3, True, favorites_path=path)

    result = services.set_favorite(3, True, favorites_path=path)

    assert result == [3]


def test_set_favorite_false_removes_a_previously_favored_id(tmp_path: Path) -> None:
    """set_favorite(3, False) removes the ID from the list and returns an empty
    list after a preceding set_favorite(3, True) call."""
    path = tmp_path / "favorites.json"
    services.set_favorite(3, True, favorites_path=path)

    result = services.set_favorite(3, False, favorites_path=path)

    assert result == []


def test_set_favorite_false_on_an_absent_id_is_a_noop_and_does_not_raise(
    tmp_path: Path,
) -> None:
    """If discard() were replaced by a raising remove(), this fails: a
    KeyError propagates instead of returning []."""
    path = tmp_path / "favorites.json"

    result = services.set_favorite(99, False, favorites_path=path)

    assert result == []


def test_set_favorite_accepts_any_id_without_a_catalog_lookup(tmp_path: Path) -> None:
    """set_favorite accepts any numeric ID (here 999999) without consulting the
    catalog, allowing users to favorite mods not yet synced."""
    path = tmp_path / "favorites.json"

    result = services.set_favorite(999999, True, favorites_path=path)

    assert result == [999999]


def test_set_favorite_returns_the_full_sorted_list_after_multiple_ids(
    tmp_path: Path,
) -> None:
    """If the sort were dropped, this fails: order reflects insertion order
    [5, 1, 3] instead of the sorted [1, 3, 5]."""
    path = tmp_path / "favorites.json"
    services.set_favorite(5, True, favorites_path=path)
    services.set_favorite(1, True, favorites_path=path)

    result = services.set_favorite(3, True, favorites_path=path)

    assert result == [1, 3, 5]


def test_list_favorites_reads_fresh_from_disk_on_every_call(tmp_path: Path) -> None:
    """If list_favorites cached its first result, this fails: the second call
    still reports [2] instead of picking up the on-disk change to [9]."""
    path = tmp_path / "favorites.json"
    services.set_favorite(2, True, favorites_path=path)
    assert services.list_favorites(favorites_path=path) == [2]

    save_favorites(path, [9])  # a write from a separate call/process

    assert services.list_favorites(favorites_path=path) == [9]


def test_corrupt_favorites_file_reads_as_empty_and_the_next_set_overwrites_it(
    tmp_path: Path,
) -> None:
    """If the malformed-content guard in load_favorites were dropped, this
    fails: list_favorites raises instead of returning []."""
    path = tmp_path / "favorites.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert services.list_favorites(favorites_path=path) == []

    result = services.set_favorite(4, True, favorites_path=path)

    assert result == [4]
    assert json.loads(path.read_text(encoding="utf-8")) == [4]


# --- default favorites path ----------------------------------------------


@pytest.fixture
def isolated_default_favorites_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Point ``default_favorites_path()`` at an isolated location.

    Mirrors ``isolated_default_db_path`` in ``test_services_catalog.py``.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        "sptmod.core.favorites.platformdirs.user_config_path",
        lambda *args, **kwargs: config_dir,
    )
    return config_dir / FAVORITES_FILE_NAME


def test_list_favorites_without_a_path_uses_the_default_favorites_path(
    isolated_default_favorites_path: Path,
) -> None:
    """If default_favorites_path() were bypassed, this fails: the isolated
    file location would never be consulted, giving no signal either way that
    the default was reached."""
    assert services.list_favorites() == []
    assert not isolated_default_favorites_path.exists()


def test_set_favorite_without_a_path_writes_to_the_default_favorites_path(
    isolated_default_favorites_path: Path,
) -> None:
    """If default_favorites_path() were bypassed, this fails: the isolated
    file is never created even though set_favorite reports success."""
    result = services.set_favorite(11, True)

    assert result == [11]
    assert json.loads(isolated_default_favorites_path.read_text(encoding="utf-8")) == [
        11
    ]


# --- survives clear_catalog_cache -----------------------------------------


def test_favorites_survive_clear_catalog_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Favorites live beside the catalog db in the same config dir; clearing
    the catalog cache must not touch them.

    If clear_catalog_cache's file targeting were broadened (e.g. wiping the
    whole config dir instead of just the db file), this fails: list_favorites
    would report [] after the clear instead of [7].
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        "sptmod.core.favorites.platformdirs.user_config_path",
        lambda *args, **kwargs: config_dir,
    )
    monkeypatch.setattr(
        "sptmod.core.forge.catalog.platformdirs.user_config_path",
        lambda *args, **kwargs: config_dir,
    )
    services.set_favorite(7, True)
    db_path = default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"fake catalog db content")

    result = services.clear_catalog_cache()

    assert result.removed is True
    assert services.list_favorites() == [7]
