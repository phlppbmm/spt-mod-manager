"""RPC fixtures for the sptmod test suite.

Registered through ``pytest_plugins`` in :mod:`conftest`; shared by the
``sptmod rpc`` test modules that split the envelope, lifecycle, catalog and
Forge cases across files.
"""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import clean_repo_at
from fixture_types import MakeArchive
from sptmod.server import rpc


@pytest.fixture
def inject_error(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, Exception], None]:
    """Swap a spec's function for one raising ``error`` for every call.

    The stub only needs to accept the request's own fields as keyword
    arguments; ``status`` (single field ``path``) is used for every case here,
    so its stub always takes just ``path``.
    """

    def _inject(name: str, error: Exception) -> None:
        original = rpc.RPC_FUNCTIONS[name]

        def _raise(**_kwargs: object) -> object:
            raise error

        monkeypatch.setitem(rpc.RPC_FUNCTIONS, name, dataclasses.replace(original, function=_raise))

    return _inject


def install_request(fake_game_dir: Path, make_archive: MakeArchive, **overrides: Any) -> dict[str, Any]:
    """Return a dictionary that ``sptmod rpc install_from_archive`` expects on stdin.

    Creates a zip archive containing SPT/user/mods/CoolMod/package.json with
    minimal mod metadata and returns request={path, archive} plus any caller
    overrides (used in operation_id tests to pass a specific caller ID).
    """
    clean_repo_at(fake_game_dir)
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.2.3", "author": "Acme"})},
        "zip",
    )
    request = {"path": str(fake_game_dir), "archive": str(archive)}
    request.update(overrides)
    return request
