"""Shared fixtures for the sptmod test suite."""

import inspect
import itertools
import json
import os
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import suppress
from pathlib import Path

import psutil
import py7zr
import pytest
from typer.testing import CliRunner

from fixture_types import InstallCommit, JsonDict, MakeArchive
from sptmod.cli import app
from sptmod.core import game_dir, services
from sptmod.core.game_dir import GameProcess, ProcessSighting, detect_layout
from sptmod.core.repo import Repo

# Fixture modules split out of this file by area; pytest loads them as plugins,
# so their fixtures are available suite-wide exactly as if they lived here.
pytest_plugins = [
    "fixtures_forge",
    "fixtures_launch",
    "fixtures_profile",
    "fixtures_rpc",
    "fixtures_spt",
]


@pytest.fixture(autouse=True)
def plain_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the CLI render its help and error screens without terminal styling.

    Rich styles its output whenever it believes something is watching, and a
    continuous integration run is one of the things it believes that about: it
    forces colour on when ``CI`` is set, so every assertion about what the CLI
    prints reads escape sequences instead of the words. Declaring the terminal
    dumb takes styling out of the picture, so the text a test matches is the
    text a reader sees.
    """
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


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
def local_source_store(tmp_path: Path) -> Path:
    """A local mod source store file path under ``tmp_path``.

    Passed as ``store_path`` to every local source service/RPC/CLI call, so the
    suites never touch the developer's real ``localsources.json``.
    """
    return tmp_path / "localsources.json"


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


# The real scans, bound before the autouse fixture below replaces the module
# attributes. ``own_processes_only`` needs the genuine scans; reading
# ``game_dir.game_processes`` inside that fixture would pick up the empty stub
# and silently turn the fixture into a no-op.
_real_game_processes = game_dir.game_processes
_real_running_game_processes = game_dir.running_game_processes
_real_spt_process_sightings = game_dir.spt_process_sightings


@pytest.fixture(autouse=True)
def no_process_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the SPT processes of the machine it runs on.

    Replaces the three scanning functions of ``sptmod.core.game_dir`` with
    empty ones and switches the scan off through ``SPTMOD_PROCESS_SCAN``. The
    replacements cover every in-process caller, because production code reaches
    the scan through the module attribute; the environment variable covers the
    watcher process a launch starts, which inherits the environment and scans
    on its own where no monkeypatch can reach. Without both halves a test run
    can find, and act on, the game a developer is playing.

    Tests that need a genuine scan request :func:`real_process_scan` or
    :func:`own_processes_only`, which undo this in a narrow, guarded way.
    """

    def no_game_processes() -> list[GameProcess]:
        return []

    def no_process_names() -> list[str]:
        return []

    def no_sightings() -> list[ProcessSighting]:
        return []

    monkeypatch.setattr(game_dir, "game_processes", no_game_processes)
    monkeypatch.setattr(game_dir, "running_game_processes", no_process_names)
    monkeypatch.setattr(game_dir, "spt_process_sightings", no_sightings)
    monkeypatch.setenv(game_dir.PROCESS_SCAN_ENV, "off")


@pytest.fixture
def real_process_scan(no_process_scan: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Let one test reach the genuine process table again.

    Undoes both halves of :func:`no_process_scan`: the environment switch and
    the three module attributes. Requested by the unit test of the scan itself,
    which has to see the real implementation and only ever reads what it finds.
    Tests that act on what the scan reports request :func:`own_processes_only`
    instead, which narrows the result to their own processes and refuses to run
    while this machine really plays SPT.
    """
    del no_process_scan
    monkeypatch.delenv(game_dir.PROCESS_SCAN_ENV, raising=False)
    monkeypatch.setattr(game_dir, "game_processes", _real_game_processes)
    monkeypatch.setattr(game_dir, "running_game_processes", _real_running_game_processes)
    monkeypatch.setattr(game_dir, "spt_process_sightings", _real_spt_process_sightings)


def _started_by_a_test_run(pid: int, test_temp_root: Path) -> bool:
    """Return whether ``pid`` is a stub some pytest run started, not a real game.

    The stubs these tests spawn carry the file names of the SPT server and the
    game on purpose, so a second pytest run of this repository -- another
    worktree, another agent -- looks exactly like a running game to a scan of
    the whole process table. Two marks tell such a stub apart from the real
    thing: its program lives under the temporary directory pytest hands out,
    and a pytest process is among its ancestors. A game a developer really
    plays has neither.

    A process that vanishes while it is examined was not playing anything, so
    it does not count; anything else that cannot be read counts as real,
    because refusing to run a test is the harmless answer.
    """
    try:
        process = psutil.Process(pid)
        if Path(process.exe()).is_relative_to(test_temp_root):
            return True
        return any(_runs_pytest(ancestor) for ancestor in process.parents())
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def _runs_pytest(process: psutil.Process) -> bool:
    """Return whether ``process`` is a pytest run, by the program it was given."""
    with suppress(psutil.Error):
        return any("pytest" in Path(argument).name for argument in process.cmdline())
    return False


@pytest.fixture
def own_processes_only(
    real_process_scan: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Narrow the real process scans to the processes this test started.

    Requested by the tests that drive the watcher and the launch state against
    a genuine process table. The real scans do the real classification, but
    only descendants of the test process can show up in their results, and the
    SPT a developer plays is never a descendant of pytest.

    Refuses to run the test at all while this machine really has an SPT server
    or game running. These are the only tests that act on what a real scan
    reports, so this is the one place where refusing is the right answer: one
    of them closing a running raid is what the whole seam exists to prevent.
    The refusal ignores what :func:`_started_by_a_test_run` recognises as
    another pytest run's stub, and only that: narrowing it to this test's own
    processes instead would make it a no-op, since those are precisely the ones
    it must not look at.
    """
    del real_process_scan
    test_temp_root = tmp_path_factory.getbasetemp().parent
    if any(
        not _started_by_a_test_run(process.pid, test_temp_root)
        for process in game_dir.game_processes()
    ):
        pytest.fail(
            "An SPT server or game is running on this machine. This test must "
            "not stop it; close the game before running the launch tests."
        )

    def own_pids() -> set[int]:
        return {child.pid for child in psutil.Process(os.getpid()).children(recursive=True)}

    def own_processes() -> list[GameProcess]:
        mine = own_pids()
        return [process for process in _real_game_processes() if process.pid in mine]

    def own_sightings() -> list[ProcessSighting]:
        mine = own_pids()
        return [sighting for sighting in _real_spt_process_sightings() if sighting.pid in mine]

    monkeypatch.setattr(game_dir, "game_processes", own_processes)
    monkeypatch.setattr(game_dir, "spt_process_sightings", own_sightings)
