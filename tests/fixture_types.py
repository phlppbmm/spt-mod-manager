"""Call signatures of the factory fixtures defined in ``tests/conftest.py``.

The three factories take keyword-only arguments with defaults (and
``install_commit`` additionally takes ``**trailer_overrides``), which
``collections.abc.Callable`` cannot express, so each one gets a ``Protocol``
that tests can use to annotate the fixture argument they receive.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from sptmod.core.repo import Repo

JsonDict = dict[str, Any]


class MakeProfile(Protocol):
    """Signature of the ``make_profile`` fixture."""

    def __call__(
        self,
        *,
        hits: frozenset[str] = frozenset(),
        item_id: str = ...,
        other_item_id: str = ...,
        trader_id: str = ...,
        other_trader_id: str = ...,
        quest_id: str = ...,
        other_quest_id: str = ...,
        recipe_id: str = ...,
        other_recipe_id: str = ...,
        achievement_id: str = ...,
        other_achievement_id: str = ...,
    ) -> JsonDict: ...


class MakeArchive(Protocol):
    """Signature of the ``make_archive`` fixture."""

    def __call__(self, files: Mapping[str, str | bytes], fmt: str) -> Path: ...


class InstallCommit(Protocol):
    """Signature of the ``install_commit`` fixture."""

    def __call__(
        self,
        repo: Repo,
        name: str,
        version: str = ...,
        *,
        mod_type: str = ...,
        state: str = ...,
        files: Mapping[str, str | bytes] | None = ...,
        minute: int = ...,
        **trailer_overrides: str,
    ) -> str: ...
