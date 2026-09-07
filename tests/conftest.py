"""Shared fixtures for the sptmod test suite."""

import inspect
import itertools
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psutil
import py7zr
import pytest
import respx
from respx.models import Call
from typer.testing import CliRunner

from fixture_types import (
    ClientFactory,
    InstallCommit,
    JsonDict,
    MakeArchive,
    MakeProfile,
    SpawnProcess,
    SpawnSleeper,
)
from sptmod.cli import app
from sptmod.core import game_dir, services
from sptmod.core.forge.catalog import DB_FILE_NAME, Catalog
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient
from sptmod.core.game_dir import detect_layout
from sptmod.core.launch import LAUNCH_STATE_FILE_NAME
from sptmod.core.models import Json
from sptmod.core.repo import Repo


@pytest.fixture(autouse=True)
def isolate_git_config(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Isolate every test from the developer machine's git configuration.

    Points HOME and the global/system git config files at locations that do
    not exist, so a machine-global ``user.name``/``user.email`` (or any other
    global git setting) can never leak into a test. Repo operations must work
    purely from the local, per-repository identity set up by ``Repo.init``.
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_home / "does-not-exist-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(fake_home / "does-not-exist-gitconfig-system"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_game_dir(tmp_path: Path) -> Path:
    """Build a minimal, realistic SPT game directory.

    Layout:
      SPT/user/mods/                        (empty, trackable)
      SPT/user/profiles/abc.json            (minimal profile, trackable)
      SPT/user/profiles/backups/old.json    (dummy, must stay ignored)
      SPT/user/logs/x.log                   (dummy, must stay ignored)
      SPT/SPT_Data/configs/core.json        (sptVersion, read directly, untracked)
      BepInEx/plugins/                      (empty, trackable)
      BepInEx/config/                       (empty, trackable)
      EscapeFromTarkov_Data/foo              (dummy, must stay ignored)

    Reused by every task building on top of the git layer.
    """
    root = tmp_path / "game"
    (root / "SPT" / "user" / "mods").mkdir(parents=True)
    (root / "SPT" / "user" / "profiles" / "backups").mkdir(parents=True)
    (root / "SPT" / "user" / "logs").mkdir(parents=True)
    (root / "SPT" / "SPT_Data" / "configs").mkdir(parents=True)
    (root / "BepInEx" / "plugins").mkdir(parents=True)
    (root / "BepInEx" / "config").mkdir(parents=True)
    (root / "EscapeFromTarkov_Data").mkdir(parents=True)

    profile: JsonDict = {
        "Inventory": {"items": []},
        "TradersInfo": {},
        "Quests": [],
    }
    (root / "SPT" / "user" / "profiles" / "abc.json").write_text(json.dumps(profile), encoding="utf-8")

    core_config = {"sptVersion": "4.1.0"}
    (root / "SPT" / "SPT_Data" / "configs" / "core.json").write_text(json.dumps(core_config), encoding="utf-8")

    (root / "SPT" / "user" / "logs" / "x.log").write_text("dummy log line\n", encoding="utf-8")
    (root / "EscapeFromTarkov_Data" / "foo").write_text("dummy game data\n", encoding="utf-8")
    (root / "SPT" / "user" / "profiles" / "backups" / "old.json").write_text("{}", encoding="utf-8")

    return root


@pytest.fixture
def fake_game_dir_runtime(tmp_path: Path) -> Path:
    """Build a minimal, realistic SPT 4.1+ game directory (``SPT_Runtime`` layout).

    Mirrors :func:`fake_game_dir` but uses the ``SPT_Runtime`` runtime directory
    SPT 4.1+ installers write, and a genuinely 4.x-shaped ``core.json`` that
    carries no ``sptVersion`` field (that field lived only in the 3.x/4.0.x
    layout; 4.x compiles the version into ``SPT.Server.exe`` instead). Layout:

      SPT_Runtime/user/mods/                        (empty, trackable)
      SPT_Runtime/user/profiles/abc.json            (minimal profile, trackable)
      SPT_Runtime/user/profiles/backups/old.json    (dummy, must stay ignored)
      SPT_Runtime/user/logs/x.log                   (dummy, must stay ignored)
      SPT_Runtime/SPT_Data/configs/core.json        (no sptVersion key, untracked)
      BepInEx/plugins/                              (empty, trackable)
      BepInEx/config/                               (empty, trackable)
      EscapeFromTarkov_Data/foo                      (dummy, must stay ignored)

    Without a ``SPT.Server.exe`` added by the caller (or a stored version) the
    SPT version of this fixture is genuinely undetectable, which is deliberate:
    it is the layout the version-chain fallback and CLI-prompt tests build on.
    """
    root = tmp_path / "game"
    (root / "SPT_Runtime" / "user" / "mods").mkdir(parents=True)
    (root / "SPT_Runtime" / "user" / "profiles" / "backups").mkdir(parents=True)
    (root / "SPT_Runtime" / "user" / "logs").mkdir(parents=True)
    (root / "SPT_Runtime" / "SPT_Data" / "configs").mkdir(parents=True)
    (root / "BepInEx" / "plugins").mkdir(parents=True)
    (root / "BepInEx" / "config").mkdir(parents=True)
    (root / "EscapeFromTarkov_Data").mkdir(parents=True)

    profile: JsonDict = {
        "Inventory": {"items": []},
        "TradersInfo": {},
        "Quests": [],
    }
    (root / "SPT_Runtime" / "user" / "profiles" / "abc.json").write_text(json.dumps(profile), encoding="utf-8")

    core_config = {"projectName": "SPT", "compatibleTarkovVersion": "1.0.0"}
    (root / "SPT_Runtime" / "SPT_Data" / "configs" / "core.json").write_text(json.dumps(core_config), encoding="utf-8")

    (root / "SPT_Runtime" / "user" / "logs" / "x.log").write_text("dummy log line\n", encoding="utf-8")
    (root / "EscapeFromTarkov_Data" / "foo").write_text("dummy game data\n", encoding="utf-8")
    (root / "SPT_Runtime" / "user" / "profiles" / "backups" / "old.json").write_text("{}", encoding="utf-8")

    return root


@pytest.fixture
def initialized_repo(fake_game_dir: Path) -> Repo:
    """A ``Repo`` freshly initialized on top of :func:`fake_game_dir`.

    CONTRACT CHANGE (spt-mod-manager-fyx): ``Repo.init`` now takes the detected
    ``GameLayout`` instead of a bare ``Path``.
    """
    return Repo.init(detect_layout(fake_game_dir))


@pytest.fixture
def committed_repo(initialized_repo: Repo) -> Iterator[tuple[Repo, str]]:
    """A repo with one commit tracking a single mod file.

    Yields ``(repo, first_commit_hash)``. ``SPT/user/mods/TestMod/mod.dll`` is
    the tracked file added by the initial commit.
    """
    mod_dir = initialized_repo.path / "SPT" / "user" / "mods" / "TestMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "mod.dll").write_bytes(b"binary-mod-content-v1")

    commit_hash = initialized_repo.commit(
        "mod: TestMod 1.0.0",
        trailers={
            "Mod": "TestMod",
            "Mod-Version": "1.0.0",
            "Mod-Type": "client",
            "Mod-Author": "tester",
            "Mod-SPT": "~4.1.0",
            "Mod-Source": "https://example.invalid/TestMod-1.0.0.7z",
            "Mod-Forge-Id": "1",
            "Mod-Forge-Version-Id": "2",
            "Mod-Guid": "",
            "Mod-State": "installed",
        },
    )
    yield initialized_repo, commit_hash


@pytest.fixture
def make_profile() -> MakeProfile:
    """Build a realistic minimal SPT 4.1.3 profile document.

    Both characters (``pmc``, ``scav``), every container the residue category
    table (``sptmod.core.profile._CATEGORIES``) knows, and a nested inventory
    per character (a stash root, a container item, a child inside it via
    ``parentId`` and a grandchild inside the child), so the ``parentId``
    closure is exercised. Every container is always populated -- with the
    "other" id by default -- so a test proves a placement is a genuine miss,
    not an absent container.

    Returns a callable
    ``make_profile(hits=frozenset(), item_id=..., other_item_id=..., ...)
    -> dict``. ``hits`` is a set of ``"<character>:<category>"`` tokens
    (``character`` is ``"pmc"``, ``"scav"`` or ``""`` for the shared
    sections), each swapping that placement's id for the mod's id instead of
    the unrelated one; some categories (``repeatable-quest``, ``dialogue``,
    ``insurance``) additionally accept a ``:<field>`` suffix to select which
    matching field carries the mod's id, e.g. ``"pmc:repeatable-quest:qid"``.
    """

    def _make(
        *,
        hits: frozenset[str] = frozenset(),
        item_id: str = "5c0d1b8b86f7744bb2683cfa",
        other_item_id: str = "5c0d1b8b86f7744bb2683c00",
        trader_id: str = "5ac3b934136fb568fa78b1e2",
        other_trader_id: str = "5ac3b934136fb568fa78b100",
        quest_id: str = "quest-mod",
        other_quest_id: str = "quest-unrelated",
        recipe_id: str = "5c0d1b8b86f7744bb2683cfd",
        other_recipe_id: str = "5c0d1b8b86f7744bb2683c01",
        achievement_id: str = "achievement-mod",
        other_achievement_id: str = "achievement-unrelated",
    ) -> JsonDict:
        def v(placement: str, mod_value: object, other_value: object) -> object:
            return mod_value if placement in hits else other_value

        def character(name: str, *, achievements: bool, repeatable: bool) -> JsonDict:
            root_id = f"{name}-stash-root"
            container_id = f"{name}-container"
            child_id = f"{name}-child"
            grandchild_id = f"{name}-grandchild"
            unrelated_id = f"{name}-unrelated"
            data: JsonDict = {
                "Inventory": {
                    "items": [
                        {"_id": root_id, "_tpl": other_item_id, "slotId": "stash"},
                        {
                            "_id": container_id,
                            "_tpl": v(f"{name}:item", item_id, other_item_id),
                            "parentId": root_id,
                            "slotId": "main",
                        },
                        {
                            "_id": child_id,
                            "_tpl": other_item_id,
                            "parentId": container_id,
                            "slotId": "cartridges",
                        },
                        {
                            "_id": grandchild_id,
                            "_tpl": other_item_id,
                            "parentId": child_id,
                            "slotId": "main",
                        },
                        {
                            "_id": unrelated_id,
                            "_tpl": other_item_id,
                            "parentId": root_id,
                            "slotId": "pocket1",
                        },
                    ],
                    "equipment": root_id,
                    "stash": root_id,
                    "sortingTable": f"{name}-sorting",
                    "questRaidItems": f"{name}-raid",
                    "questStashItems": f"{name}-stash-items",
                    "hideoutAreaStashes": {},
                    "fastPanel": {},
                    "favoriteItems": [],
                    "hideoutCustomizationStashId": f"{name}-customization-stash",
                },
                "Encyclopedia": {v(f"{name}:encyclopedia", item_id, other_item_id): True},
                "WishList": {v(f"{name}:wishlist", item_id, other_item_id): 1},
                "TradersInfo": {v(f"{name}:trader", trader_id, other_trader_id): {"salesSum": 0}},
                "Quests": [
                    {
                        "qid": v(f"{name}:quest", quest_id, other_quest_id),
                        "status": 1,
                        "statusTimers": {},
                        "completedConditions": [],
                        "availableAfter": 0,
                    }
                ],
                "TaskConditionCounters": {
                    f"{name}-cond": {
                        "id": f"{name}-cond",
                        "type": "Counter",
                        "value": 1,
                        "sourceId": v(f"{name}:task-counter", quest_id, other_quest_id),
                    }
                },
                "Hideout": {
                    "Production": {
                        f"{name}-prod": {
                            "recipeId": v(
                                f"{name}:hideout-production",
                                recipe_id,
                                other_recipe_id,
                            ),
                            "Progress": 0,
                        }
                    },
                    "Areas": [
                        {
                            "type": 0,
                            "level": 1,
                            "active": True,
                            "passiveBonusesEnabled": True,
                            "completeTime": 0,
                            "constructing": False,
                            "slots": [
                                {"locationIndex": 0},
                                {
                                    "locationIndex": 1,
                                    "items": [
                                        {
                                            "_id": f"{name}-slot-item",
                                            "_tpl": v(
                                                f"{name}:hideout-area",
                                                item_id,
                                                other_item_id,
                                            ),
                                        }
                                    ],
                                },
                            ],
                            "lastRecipe": "",
                        }
                    ],
                    "Improvements": {},
                    "HideoutCounters": None,
                    "Seed": 1,
                    "MannequinPoses": {},
                    "sptUpdateLastRunTimestamp": 0,
                    "Customization": {},
                },
                "UnlockedInfo": {
                    "unlockedProductionRecipe": [v(f"{name}:unlocked-recipe", recipe_id, other_recipe_id)]
                },
                "InsuredItems": [
                    {
                        "tid": v(f"{name}:insured-item:tid", trader_id, other_trader_id),
                        "itemId": v(f"{name}:insured-item:itemId", container_id, unrelated_id),
                    }
                ],
            }
            if repeatable:
                data["RepeatableQuests"] = [
                    {
                        "id": f"{name}-repeatable",
                        "name": "Daily",
                        "activeQuests": [
                            {
                                "_id": v(f"{name}:repeatable-quest:id", quest_id, "rq-id"),
                                "templateId": v(
                                    f"{name}:repeatable-quest:templateId",
                                    quest_id,
                                    "rq-template",
                                ),
                                "traderId": v(
                                    f"{name}:repeatable-quest:traderId",
                                    trader_id,
                                    other_trader_id,
                                ),
                                "questStatus": {
                                    "id": "rq-status-id",
                                    "uid": f"{name}-uid",
                                    "qid": v(
                                        f"{name}:repeatable-quest:qid",
                                        quest_id,
                                        "rq-qid",
                                    ),
                                },
                            }
                        ],
                        "inactiveQuests": [],
                        "endTime": 0,
                    }
                ]
            if achievements:
                data["Achievements"] = {v("pmc:achievement", achievement_id, other_achievement_id): (1700000000)}
            return data

        return {
            "info": {"nickname": "tester"},
            "characters": {
                "pmc": character("pmc", achievements=True, repeatable=True),
                "scav": character("scav", achievements=False, repeatable=False),
            },
            "userbuilds": {
                "weaponBuilds": [
                    {
                        "Id": "build-1",
                        "Name": "Build 1",
                        "Items": [
                            {
                                "_id": "build-item-1",
                                "_tpl": v("userbuild:weapon", item_id, other_item_id),
                            }
                        ],
                    }
                ],
                "equipmentBuilds": [
                    {
                        "Id": "build-2",
                        "Name": "Build 2",
                        "Items": [
                            {
                                "_id": "build-item-2",
                                "_tpl": v("userbuild:equipment", item_id, other_item_id),
                            }
                        ],
                    }
                ],
                "magazineBuilds": [
                    {
                        "Id": "build-3",
                        "Name": "Build 3",
                        "Items": [
                            {
                                "_id": "build-item-3",
                                "_tpl": v("userbuild:magazine", item_id, other_item_id),
                            }
                        ],
                    }
                ],
            },
            "dialogues": {
                v(":dialogue:trader", trader_id, other_trader_id): {
                    "_id": v(":dialogue:trader", trader_id, other_trader_id),
                    "type": 2,
                    "new": 0,
                    "attachmentsNew": 0,
                    "pinned": False,
                    "messages": [
                        {
                            "_id": "message-1",
                            "uid": other_trader_id,
                            "type": 2,
                            "dt": 0,
                            "templateId": v(
                                ":dialogue:templateId",
                                f"{quest_id}-reward",
                                "unrelated-template-id",
                            ),
                            "text": "",
                            "hasRewards": False,
                            "rewardCollected": False,
                            "items": {
                                "data": [
                                    {
                                        "_id": "message-item-1",
                                        "_tpl": v(":dialogue:items", item_id, other_item_id),
                                    }
                                ]
                            },
                        }
                    ],
                }
            },
            "insurance": [
                {
                    "scheduledTime": 0,
                    "traderId": v(":insurance:trader", trader_id, other_trader_id),
                    "maxStorageTime": 0,
                    "systemData": {},
                    "messageType": 0,
                    "messageTemplateId": "",
                    "items": [
                        {
                            "_id": "insured-1",
                            "_tpl": v(":insurance:item", item_id, other_item_id),
                        },
                        {
                            "_id": "insured-2",
                            "_tpl": other_item_id,
                            "parentId": "insured-1",
                        },
                    ],
                }
            ],
            "traderPurchases": {
                v(":trader-purchase", trader_id, other_trader_id): {"assort-1": {"count": 1, "purchaseTimestamp": 0}}
            },
            "customisationUnlocks": [{"id": "unlock-1", "source": "default", "type": "suite"}],
            "spt": {"version": "4.1.3", "mods": []},
        }

    return _make


@pytest.fixture
def make_archive(tmp_path: Path) -> MakeArchive:
    """Factory building a real archive file from ``{relpath: content}`` entries.

    Returns a callable ``make_archive(files, fmt) -> Path`` where ``fmt`` is
    ``"zip"`` (stdlib ``zipfile``) or ``"7z"`` (``py7zr``). ``content`` may be
    ``str`` (UTF-8 encoded) or ``bytes``. Member names are written exactly as
    given -- including unsafe ones for the zip backend -- so the same factory
    covers both roundtrip and path-traversal tests.

    ``fmt="rar"`` is intentionally unsupported: building a real ``.rar``
    archive needs the proprietary ``rar`` command line tool, which is not
    available in this environment. Tests exercising rar handling must skip
    via ``rar_backend_available()`` instead of faking a rar as a zip.
    """
    counter = itertools.count()

    def _make(files: Mapping[str, str | bytes], fmt: str) -> Path:
        archive_path = tmp_path / f"archive-{next(counter)}.{fmt}"
        if fmt == "zip":
            with zipfile.ZipFile(archive_path, "w") as zf:
                for relpath, content in files.items():
                    data = content.encode("utf-8") if isinstance(content, str) else content
                    zf.writestr(relpath, data)
        elif fmt == "7z":
            staging = tmp_path / f"stage-{next(counter)}"
            staging.mkdir()
            with py7zr.SevenZipFile(archive_path, "w") as zf:
                for member_index, (relpath, content) in enumerate(files.items()):
                    data = content.encode("utf-8") if isinstance(content, str) else content
                    # The staged file lives under a flat, safe name -- only
                    # the arcname passed to py7zr carries the (possibly
                    # unsafe, e.g. "../evil.txt") relpath. This keeps a
                    # traversal-member archive from writing outside the
                    # staging dir while it is still being *built*, which
                    # would otherwise make extraction tests unable to tell
                    # apart a file written by extract() from one written by
                    # this fixture.
                    src = staging / f"member-{member_index}"
                    src.write_bytes(data)
                    zf.write(src, arcname=relpath)
        elif fmt == "rar":
            raise NotImplementedError(
                "creating .rar archives needs the 'rar' CLI tool, which is "
                "not available in this environment; gate rar tests on "
                "rar_backend_available() instead of calling make_archive "
                "with fmt='rar'"
            )
        else:
            raise ValueError(f"unsupported archive format: {fmt!r}")
        return archive_path

    return _make


@pytest.fixture
def clean_repo(initialized_repo: Repo, monkeypatch: pytest.MonkeyPatch) -> Repo:
    """An ``initialized_repo`` with one baseline whitelist commit, tree clean.

    Mirrors the initial commit ``init_game_dir`` is specified to create
    (``.gitignore`` plus the tracked profile), without depending on that
    (stubbed) service. Reused by tests of ``install_from_archive``,
    ``list_mods``, ``mod_files``, ``remove_mod`` and ``history``, which all
    need a clean tree with a known "time zero" to build further commits on.
    """
    date = "2024-01-01T00:00:00+00:00"
    monkeypatch.setenv("GIT_AUTHOR_DATE", date)
    monkeypatch.setenv("GIT_COMMITTER_DATE", date)
    initialized_repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    return initialized_repo


@pytest.fixture
def install_commit(monkeypatch: pytest.MonkeyPatch) -> InstallCommit:
    """Factory writing a mod's files and committing them with full mod trailers.

    Builds git history directly (bypassing the stubbed ``install_from_archive``
    service) so ``list_mods``, ``mod_files``, ``remove_mod`` and ``history``
    tests can set up realistic install commits on their own.

    Returns a callable
    ``install_commit(repo, name, version="1.0.0", *, mod_type="client",
    state="installed", files=None, minute=1, **trailer_overrides) -> commit_hash``.
    ``files`` maps a path (relative to the repo root, as ``str``) to its
    content (``str`` or ``bytes``); defaults to a single
    ``BepInEx/plugins/<name>.dll``. ``minute`` sets both the author and
    committer date to a fixed, strictly increasing timestamp (minutes after
    a fixed 2024-01-01 baseline) so tests can assert commit ordering without
    relying on wall-clock timing.
    """

    def _install(
        repo: Repo,
        name: str,
        version: str = "1.0.0",
        *,
        mod_type: str = "client",
        state: str = "installed",
        files: Mapping[str, str | bytes] | None = None,
        minute: int = 1,
        **trailer_overrides: str,
    ) -> str:
        entries = files or {f"BepInEx/plugins/{name}.dll": f"{name}-{version}-content"}
        paths: list[Path] = []
        for relpath, content in entries.items():
            full = repo.path / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8") if isinstance(content, str) else content
            full.write_bytes(data)
            paths.append(Path(relpath))

        trailers = {
            "Mod": name,
            "Mod-Version": version,
            "Mod-Type": mod_type,
            "Mod-Author": "tester",
            "Mod-SPT": "~4.1.0",
            "Mod-Source": f"{name}-{version}.zip",
            "Mod-Forge-Id": "",
            "Mod-Forge-Version-Id": "",
            "Mod-Guid": "",
            "Mod-State": state,
        }
        trailers.update(trailer_overrides)

        date = f"2024-01-01T00:{minute:02d}:00+00:00"
        monkeypatch.setenv("GIT_AUTHOR_DATE", date)
        monkeypatch.setenv("GIT_COMMITTER_DATE", date)
        return repo.commit(f"mod: {name} {version}", trailers=trailers, paths=paths)

    return _install


# --------------------------------------------------------------------------
# launch.py / sptserver.py support
# --------------------------------------------------------------------------

# A stub server/client "binary": on start it dumps its own argv, cwd and a
# snapshot of its environment to "<its own script path>.dump.json", then
# sleeps. It never opens a socket and never talks to the network -- readiness
# is always simulated through respx on the ping endpoint. Deriving the dump
# path from ``sys.argv[0]`` (rather than an inherited env var) keeps the dump
# unambiguous even though the client stub's environment is deliberately
# reshaped by launch.py before it is spawned.
_STUB_SCRIPT = f"""#!{sys.executable}
import json
import os
import sys
import time

dump_path = sys.argv[0] + ".dump.json"
with open(dump_path, "w", encoding="utf-8") as handle:
    json.dump(
        {{
            "pid": os.getpid(),
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "env": dict(os.environ),
        }},
        handle,
    )
time.sleep(30)
"""


def _write_stub_binary(path: Path) -> None:
    """Write an executable stub process to ``path`` (parent must exist)."""
    path.write_text(_STUB_SCRIPT, encoding="utf-8")
    os.chmod(path, 0o755)


def stub_dump(binary_path: Path) -> JsonDict:
    """Read back the argv/cwd/env a stub process at ``binary_path`` recorded.

    Raises ``FileNotFoundError`` until the stub has actually started and
    wound; callers poll on it after ``launch_profile`` returns.
    """
    dump: JsonDict = json.loads(Path(f"{binary_path}.dump.json").read_text(encoding="utf-8"))
    return dump


@dataclass(frozen=True)
class LaunchableGameDir:
    """A :func:`fake_launchable_dir` and everything a test needs to drive it."""

    root: Path
    profile_id: str
    username: str
    nickname: str
    base_url: str
    server_binary: Path
    umu_path: Path
    client_exe: Path
    http_config_file: Path
    launcher_settings_file: Path


def kill_launchable_processes(root: Path) -> None:
    """Kill every stub/watcher process a launch test may have started.

    Every process ``launch_profile`` starts (server, client, watcher) is a
    direct child of this test process at the moment it is spawned -- detached
    only means it gets its own session, not a different parent -- so walking
    ``children(recursive=True)`` of the current pid finds them reliably even
    when a test asserts a failure before any dump file or state file was
    written (those are only a secondary source, kept as a fallback for a
    process that was somehow reparented). Safe to call when nothing was ever
    started.
    """
    pids: set[int] = set()
    with suppress(psutil.Error):
        for child in psutil.Process(os.getpid()).children(recursive=True):
            with suppress(psutil.Error):
                if str(child.cwd()).startswith(str(root)):
                    pids.add(child.pid)
    for dump in root.rglob("*.dump.json"):
        with suppress(OSError, ValueError, KeyError):
            pids.add(json.loads(dump.read_text(encoding="utf-8"))["pid"])
    state_file = root / ".sptmod-launch.json"
    if state_file.is_file():
        with suppress(OSError, ValueError):
            state: Json = json.loads(state_file.read_text(encoding="utf-8"))
            for key in ("server", "client", "watcher"):
                entry = state.get(key) if isinstance(state, dict) else None
                pid = entry.get("pid") if isinstance(entry, dict) else None
                if isinstance(pid, int):
                    pids.add(pid)
    processes: list[psutil.Process] = []
    for pid in pids:
        with suppress(psutil.Error):
            process = psutil.Process(pid)
            for child in process.children(recursive=True):
                with suppress(psutil.Error):
                    child.kill()
            process.kill()
            processes.append(process)
    if processes:
        psutil.wait_procs(processes, timeout=2)


@pytest.fixture
def fake_launchable_dir(fake_game_dir_runtime: Path) -> Iterator[LaunchableGameDir]:
    """A :func:`fake_game_dir_runtime` ready for :mod:`sptmod.core.launch`.

    Adds ``sptLogger.json``, ``http.json`` (default ``127.0.0.1:6969``),
    ``LauncherSettings.json`` (verbatim live shape, ``GameMode`` off), a
    profile with ``info.username``/``characters.pmc.Info.Nickname``, an
    executable stub server at the real ``server_binary`` path and an
    executable stub "umu" at the ``UmuPath`` named in the settings. Nothing
    binds a port; nothing talks to the network.
    """
    root = fake_game_dir_runtime
    runtime = root / "SPT_Runtime"

    (runtime / "sptLogger.json").write_text("{}", encoding="utf-8")

    (runtime / "SPT_Data" / "configs" / "http.json").write_text(
        json.dumps(
            {
                "ip": "127.0.0.1",
                "port": 6969,
                "backendIp": "127.0.0.1",
                "backendPort": 6969,
                "logRequests": True,
                "serverImagePathOverride": {},
                "webAuthenticationConfig": {
                    "enabled": True,
                    "enableDefaultUser": True,
                    "allowDefaultUserFromAnyIp": True,
                    "requireCredentialsOnLocalhost": False,
                    "defaultUser": {
                        "username": "spt",
                        "password": "spt",
                        "isAdministrator": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    launcher_dir = runtime / "user" / "Launcher"
    launcher_dir.mkdir(parents=True)
    umu_path = root / "stub-umu.py"
    _write_stub_binary(umu_path)
    (launcher_dir / "LauncherSettings.json").write_text(
        json.dumps(
            {
                "Servers": [],
                "StartLocation": {"X": 0, "Y": 0},
                "StartSize": {"Height": 1381, "Width": 2522},
                "FirstRun": False,
                "EnableGamePath": False,
                "GamePath": str(root),
                "CloseToTray": False,
                "MinimizeOnLaunch": True,
                "AlwaysTop": False,
                "UseBackground": True,
                "ClearCacheOnLaunch": False,
                "AutoConnectLastProfile": False,
                "PreferredProfile": None,
                "ExcludeFromCleanup": [],
                "Language": "en",
                "LinuxSettings": {
                    "PrefixPath": str(root / "prefix"),
                    "UmuPath": str(umu_path),
                    "LaunchSettings": "PROTON_USE_XALIA=0 PROTON_MEDIA_USE_GST=1",
                    "ProtonVersion": "GE-Proton11-5",
                    "ProtonPaths": [],
                    "GameMode": False,
                    "DefaultEnv": 'WINEDLLOVERRIDES="winhttp=n,b"',
                },
                "DebugLogging": False,
            }
        ),
        encoding="utf-8",
    )

    profile_id = "6a986df8b3c3f533ed2cd26a"
    username = "0Sullivan"
    nickname = "TesterNick"
    (runtime / "user" / "profiles" / f"{profile_id}.json").write_text(
        json.dumps(
            {
                "info": {"id": profile_id, "username": username},
                "characters": {"pmc": {"Info": {"Nickname": nickname}}},
            }
        ),
        encoding="utf-8",
    )

    layout = detect_layout(root)
    server_binary = root / layout.server_binary
    server_binary.parent.mkdir(parents=True, exist_ok=True)
    _write_stub_binary(server_binary)

    client_exe = root / layout.client_exe
    client_exe.write_text("stub client executable placeholder", encoding="utf-8")

    try:
        yield LaunchableGameDir(
            root=root,
            profile_id=profile_id,
            username=username,
            nickname=nickname,
            base_url="https://127.0.0.1:6969",
            server_binary=server_binary,
            umu_path=umu_path,
            client_exe=client_exe,
            http_config_file=root / layout.http_config_file,
            launcher_settings_file=root / layout.launcher_settings_file,
        )
    finally:
        kill_launchable_processes(root)


class FakeClock:
    """A monotonic-clock stand-in that only advances when told to.

    Matches the ``Callable[[], float]`` signature ``ForgeClient`` expects
    for its ``clock`` constructor argument.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        """Moves the fake clock forward by ``seconds``."""
        self.value += seconds


def recorded_requests(route: respx.Route) -> list[httpx.Request]:
    """Return the requests a respx route recorded, in call order.

    ``respx.CallList`` derives from a bare ``list``, so its entries carry no
    type; copying it into a typed list restores what respx documents.
    """
    calls: list[Call] = list(route.calls)
    return [call.request for call in calls]


class RecordingSleep:
    """A sleep stand-in that records requested durations without blocking.

    Matches the ``Callable[[float], None]`` signature ``ForgeClient``
    expects for its ``sleep`` constructor argument. Never calls the real
    ``time.sleep``.
    """

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def fake_clock() -> FakeClock:
    """Fixture providing a FakeClock instance initialized to 1000.0.

    The clock returns a constant monotonic value (1000.0) until advance(seconds)
    is called; this allows tests to control time precisely for throttle and retry
    logic without blocking.
    """
    return FakeClock()


@pytest.fixture
def recording_sleep() -> RecordingSleep:
    """A mock sleep function that records requested sleep durations without blocking."""
    return RecordingSleep()


@pytest.fixture
def client_factory(fake_clock: FakeClock, recording_sleep: RecordingSleep) -> Iterator[ClientFactory]:
    """Build ``ForgeClient`` instances wired to the fake clock/recording sleep.

    Every client built through the factory is closed on teardown.
    """
    made: list[ForgeClient] = []

    def _make(**kwargs: Any) -> ForgeClient:
        """Build a ForgeClient with injected clock and sleep test doubles.

        The client is wired to use fake_clock (controlled time) and recording_sleep
        (non-blocking duration recording); all instances are automatically closed
        during fixture teardown.
        """
        client = ForgeClient(clock=fake_clock, sleep=recording_sleep, **kwargs)
        made.append(client)
        return client

    yield _make
    for client in made:
        client.close()


@pytest.fixture
def respx_router() -> Iterator[respx.MockRouter]:
    """A respx HTTP mocking router set to the Forge's default base URL."""
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path of the catalog SQLite file for one test, inside ``tmp_path``.

    Never the real platformdirs config dir: catalog tests must not touch the
    developer's actual config directory.
    """
    return tmp_path / "forge-catalog.db"


@pytest.fixture
def catalog(db_path: Path) -> Iterator[Catalog]:
    """A ``Catalog`` opened at the test's isolated ``db_path``."""
    with Catalog(db_path) as cat:
        yield cat


def _open_process(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start ``argv``; the ``spawn_process`` fixture owns and kills the handle."""
    return subprocess.Popen(list(argv))


@pytest.fixture
def spawn_process() -> Iterator[SpawnProcess]:
    """Factory starting throwaway real processes, all killed on teardown."""
    spawned: list[subprocess.Popen[bytes]] = []

    def _spawn(argv: Sequence[str]) -> subprocess.Popen[bytes]:
        spawned.append(_open_process(argv))
        return spawned[-1]

    yield _spawn
    for process in spawned:
        with suppress(OSError):
            process.kill()
            process.wait(timeout=2)


@pytest.fixture
def spawn_sleeper(spawn_process: SpawnProcess) -> SpawnSleeper:
    """Factory spawning a throwaway real process that just sleeps."""
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None

    def _spawn() -> subprocess.Popen[bytes]:
        return spawn_process([sleep_bin, "30"])

    return _spawn


def clean_repo_at(path: Path) -> Repo:
    """Initialise the game directory at ``path`` and commit its clean baseline.

    The path-taking counterpart of the :func:`clean_repo` fixture, for tests
    that pick the game directory themselves (CLI and RPC tests mostly).
    """
    repo = Repo.init(detect_layout(path))
    repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    return repo


# The whitelist sptmod generated before it tracked the SPT program set: it
# covered the mods, the profiles and BepInEx only, so an installation created
# with it has neither the runtime directory's files nor SPT_Data committed.
_LEGACY_WHITELIST = """\
*
!.gitignore
!{runtime}/
{runtime}/*
!{runtime}/user/
{runtime}/user/*
!{runtime}/user/mods/
!{runtime}/user/mods/**
!{runtime}/user/profiles/
!{runtime}/user/profiles/*.json
{runtime}/user/profiles/backups/
!BepInEx/
BepInEx/*
!BepInEx/plugins/
!BepInEx/plugins/**
!BepInEx/config/
!BepInEx/config/**
!BepInEx/patchers/
!BepInEx/patchers/**
"""


def legacy_whitelist_repo(path: Path) -> Repo:
    """Initialise the game directory at ``path`` the way an older sptmod did.

    Writes the whitelist that did not cover the SPT program set yet and commits
    the baseline with it, so every program file below the runtime directory
    stays untracked and ignored. This is the state an installation set up
    before that whitelist change is migrated from.
    """
    layout = detect_layout(path)
    repo = Repo.init(layout)
    whitelist = _LEGACY_WHITELIST.format(runtime=layout.runtime_dir_name)
    (path / ".gitignore").write_text(whitelist, encoding="utf-8")
    repo.commit("chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None)
    return repo


def forge_trailers(mod_id: str, version_id: str | None = None) -> JsonDict:
    """Forge id trailers for ``install_commit``, for ``**``-unpacking.

    The trailer names contain hyphens, so they can only be passed as keyword
    overrides through ``**``. The return type is ``dict[str, Any]`` on
    purpose: unpacking a ``dict[str, str]`` into ``install_commit`` is
    rejected by both type checkers because of its non-``str`` ``files`` and
    ``minute`` parameters.
    """
    trailers: JsonDict = {"Mod-Forge-Id": mod_id}
    if version_id is not None:
        trailers["Mod-Forge-Version-Id"] = version_id
    return trailers


def service_function_names() -> list[str]:
    """Every public function of ``sptmod.core.services``, as declared by ``__all__``."""
    return sorted(
        name
        for name in services.__all__
        if inspect.isfunction(getattr(services, name)) and not name.startswith("_")
    )


def invoke_rpc(name: str, request: JsonDict) -> tuple[int, list[JsonDict]]:
    """Run ``rpc <name>`` and return ``(exit_code, parsed stdout lines)``."""
    result = CliRunner().invoke(app, ["rpc", name], input=json.dumps(request))
    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    return result.exit_code, lines


@pytest.fixture
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report no running SPT process, whatever runs on the test host.

    Requested through a module-level ``pytest.mark.usefixtures`` by every
    suite whose service calls would otherwise raise ``GameRunningError``
    when the developer happens to play while the tests run.
    """
    def no_running_processes() -> list[str]:
        return []

    monkeypatch.setattr(game_dir, "running_game_processes", no_running_processes)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Empty temporary directory used as the download cache root."""
    directory = tmp_path / "cache"
    directory.mkdir()
    return directory


@pytest.fixture
def isolated_default_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``default_db_path()`` at an isolated location for CLI tests.

    The CLI never passes ``db_path`` explicitly, so it resolves through
    ``forge.catalog.default_db_path()``, which must not touch the developer's
    real config directory.
    """
    config_dir = tmp_path / "config"
    def user_config_path(*_args: object, **_kwargs: object) -> Path:
        return config_dir

    monkeypatch.setattr(
        "sptmod.core.forge.catalog.platformdirs.user_config_path", user_config_path
    )
    return config_dir / DB_FILE_NAME


def write_launch_state(
    root: Path,
    *,
    profile_id: str = "",
    stop_server_on_exit: bool = True,
    server: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
    watcher: dict[str, Any] | None = None,
) -> None:
    """Write LAUNCH_STATE_FILE_NAME to the game directory with given process state.

    Creates the JSON state file recording the profile, process PIDs and start times
    (server, client, watcher), and the stop_server_on_exit flag. Keyword args
    allow each process reference and the flag to be individually set; defaults are
    empty profile and true for stop_server_on_exit.
    """
    payload = {
        "profile_id": profile_id,
        "stop_server_on_exit": stop_server_on_exit,
        "server": server,
        "client": client,
        "watcher": watcher,
    }
    (root / LAUNCH_STATE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def process_ref(pid: int, started_at: float, *, started_by_sptmod: bool | None = None) -> dict[str, Any]:
    """Build a process reference dict for the launch state file.

    Returns a dict with pid and started_at (float timestamp); if started_by_sptmod
    is not None, it is also included. This object is assigned to server, client, or
    watcher fields in the state file.
    """
    payload: dict[str, Any] = {"pid": pid, "started_at": started_at}
    if started_by_sptmod is not None:
        payload["started_by_sptmod"] = started_by_sptmod
    return payload


def empty_pmc_profile() -> JsonDict:
    """Return a minimal valid PMC profile with empty containers."""
    return {
        "characters": {
            "pmc": {
                "Inventory": {"items": []},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }


def profile_with_item(item_id: str) -> JsonDict:
    """Build a profile document containing an inventory item with the given ID."""
    return {
        "characters": {
            "pmc": {
                "Inventory": {
                    "items": [
                        {
                            "_id": "i1",
                            "_tpl": item_id,
                            "parentId": "5c0d1b8b86f7744bb2683c99",
                            "slotId": "hideout",
                        }
                    ]
                },
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
