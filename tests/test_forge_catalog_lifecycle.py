"""Tests for Catalog.__init__ failure handling: no leaked connections."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sptmod.core.forge.catalog import Catalog


class _ClosingRecorder:
    """Wraps a real sqlite3.Connection, recording whether close() was called."""

    def __init__(self, real: sqlite3.Connection) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "closed", False)

    def close(self) -> None:
        object.__setattr__(self, "closed", True)
        self._real.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._real, name, value)


def test_init_on_a_non_sqlite_file_raises_database_error(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a sqlite database, just plain junk padding" * 5)

    with pytest.raises(sqlite3.DatabaseError):
        Catalog(garbage)


def test_init_failure_closes_the_connection_instead_of_leaking_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a sqlite database, just plain junk padding" * 5)

    original_connect = sqlite3.connect
    created: list[_ClosingRecorder] = []

    def fake_connect(*args: Any, **kwargs: Any) -> _ClosingRecorder:
        wrapper = _ClosingRecorder(original_connect(*args, **kwargs))
        created.append(wrapper)
        return wrapper

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(sqlite3.DatabaseError):
        Catalog(garbage)

    assert len(created) == 1
    assert created[0].closed is True
