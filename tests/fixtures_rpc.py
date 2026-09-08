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
from fixture_types import JsonDict, MakeArchive
from sptmod.server import rpc


@pytest.fixture
def inject_error(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, Exception], None]:
    """Swap a spec's function for one raising ``error`` for every call.

    A last resort for the handful of core errors whose real precondition
    cannot be produced through a public entry point at any reasonable cost.
    Every other error is driven from a real precondition through
    ``sptmod rpc``; the remaining users of this fixture are exactly:

    * ``DependencyError`` and ``UnresolvableDependencyError`` -- raised while
      resolving a Forge dependency tree that no reachable catalog state
      produces.
    * ``PartialInstallError`` -- needs a multi-mod install to fail halfway
      through, after at least one mod was committed.
    * ``ProfileResidueError`` -- needs a removal that the residue scan
      refuses, which the removal path itself never reaches.
    * ``GitError`` with ``conflicting_paths`` -- needs git to report a merge
      conflict, which sptmod's own commands never provoke.
    * a plain ``RuntimeError``, for the unexpected-exception envelope.

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


def assert_progress_then_result(lines: list[JsonDict]) -> None:
    """Assert the NDJSON envelope rule: progress lines, then exactly one result.

    Every long-running RPC function reports progress before it answers, so a
    caller may read lines until the first non-progress one and treat that as
    the answer. This checks that rule once for all of them.
    """
    events = [line["event"] for line in lines]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
