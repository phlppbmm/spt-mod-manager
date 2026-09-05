"""Call signatures of the factory fixtures defined in ``tests/conftest.py``.

The factories take keyword-only arguments with defaults (and ``install_commit``
additionally takes ``**trailer_overrides``), which ``collections.abc.Callable``
cannot express, so each one gets a ``Protocol`` that tests can use to annotate
the fixture argument they receive.
"""

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sptmod.core.forge.client import ForgeClient
from sptmod.core.repo import Repo

JsonDict = dict[str, Any]

# The ``client_factory`` fixture forwards arbitrary keyword arguments to
# ``ForgeClient``, so the parameter list stays open.
type ClientFactory = Callable[..., ForgeClient]

# The ``spawn_process`` fixture starts one throwaway process per call and
# ``spawn_sleeper`` is its fixed ``sleep 30`` variant.
type SpawnProcess = Callable[[Sequence[str]], subprocess.Popen[bytes]]
type SpawnSleeper = Callable[[], subprocess.Popen[bytes]]


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


class TrailerInstallCommit(Protocol):
    """``install_commit`` seen by the call sites that pass trailer overrides.

    ``fixture_types.InstallCommit`` also declares the keyword-only ``files``
    and ``minute`` parameters, so a ``**{"Mod-Forge-Id": ...}`` unpack is
    rejected (a ``str`` value could land on ``minute: int``). The call sites
    below pass trailer overrides only.
    """

    def __call__(
        self,
        repo: Repo,
        name: str,
        version: str = ...,
        *,
        mod_type: str = ...,
        **trailer_overrides: str,
    ) -> str: ...
