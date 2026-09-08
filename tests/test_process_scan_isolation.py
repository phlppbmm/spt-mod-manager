"""Guard tests over the test tree itself: no test may signal a foreign process.

The suite runs on the machine a developer plays SPT on. Everything that can
close a process -- ``stop_game``, ``watch_game`` and the launch flow -- must
only ever reach a process the test itself started, which the shared autouse
fixture ``no_process_scan`` in ``tests/conftest.py`` guarantees by emptying the
process scan of ``sptmod.core.game_dir``. That guarantee has exactly one hole:
a test module that reaches ``psutil`` or the scanning functions directly. These
tests parse the test tree and pin that hole shut, so the protection cannot be
lost again by a later test that innocently imports a name.

Reading the process table is not what is dangerous, so the scanner's own unit
test is exempt from the import rule and pinned to stay a pure read instead: it
may end the processes it started itself and nothing else.
"""

import ast
import os
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
SCANNER_TEST_MODULE = "test_game_dir.py"
GAME_DIR_MODULE = "sptmod.core.game_dir"
PSUTIL_MODULE = "psutil"
SCAN_NAMES = frozenset({"game_processes", "running_game_processes", "spt_process_sightings"})
# The two ``psutil`` entry points that hand out the whole process table.
TABLE_NAMES = frozenset({"process_iter", "pids"})
# The functions that close processes; the scanner's own test must call none.
STOPPING_NAMES = ("stop_game", "watch_game", "launch_profile")
# The method calls that end a process, whatever object they are made on.
STOPPING_CALLS = frozenset({"terminate", "kill", "send_signal"})
# The fixtures that hand out a process the test itself started.
SPAWNING_FIXTURES = frozenset({"spawn_process", "spawn_sleeper"})


def _test_modules() -> list[Path]:
    """Every Python module of the test tree, subdirectories included."""
    return sorted(TESTS_DIR.rglob("*.py"))


def _module_name(module: Path) -> str:
    """Name one module by its path below the test tree, so subdirectories differ."""
    return module.relative_to(TESTS_DIR).as_posix()


def _parse(module: Path) -> ast.Module:
    """Parse one test module into its syntax tree."""
    return ast.parse(module.read_text(encoding="utf-8"))


def _psutil_aliases(tree: ast.Module) -> set[str]:
    """Return every local name a module binds to the ``psutil`` module itself."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == PSUTIL_MODULE
    }


def _walks_the_process_table(tree: ast.Module) -> bool:
    """Return whether a module really reaches the whole process table.

    Both ways in count: a call on whatever name the module bound ``psutil`` to,
    and an import that pulls ``process_iter`` or ``pids`` out of it by name.
    The same dotted name written as a string, which is how ``monkeypatch``
    addresses it, and a mention in a docstring scan nothing.
    """
    aliases = _psutil_aliases(tree)
    calls = any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in TABLE_NAMES
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in aliases
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )
    imports = any(
        node.module == PSUTIL_MODULE and alias.name in TABLE_NAMES
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
    return calls or imports


def _imported_scan_names(tree: ast.Module) -> set[str]:
    """Return the scanning functions a module imports from ``game_dir`` by name."""
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == GAME_DIR_MODULE
        for alias in node.names
        if alias.name in SCAN_NAMES
    }


def _spawned_handles(tree: ast.Module) -> set[str]:
    """Return the names a module binds to a process it started itself.

    Only the two spawning fixtures hand such a process out, and both are torn
    down with the test that asked for them.
    """
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in SPAWNING_FIXTURES
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _foreign_stopping_calls(tree: ast.Module) -> list[str]:
    """Return every call ending a process the module did not start itself.

    A module may end what it spawned; ending anything else -- a process handed
    over by a scan above all -- is what must never appear.
    """
    spawned = _spawned_handles(tree)
    return sorted(
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in STOPPING_CALLS
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id in spawned)
    )


def test_the_guard_reads_the_modules_it_claims_to_guard() -> None:
    """The module list this guard works on really holds the test modules.

    A wrong directory or a broken pattern would make every assertion below
    pass over nothing, so the source of the evidence is checked first, against
    an independent walk of the same directory tree.
    """
    names = {_module_name(module) for module in _test_modules()}
    walked = {
        _module_name(Path(directory) / file)
        for directory, _subdirectories, files in os.walk(TESTS_DIR)
        for file in files
        if file.endswith(".py")
    }

    assert SCANNER_TEST_MODULE in names
    assert {"conftest.py", "test_launch.py", "test_launch_watch.py", "test_rpc_launch.py"} <= names
    assert names == walked


def test_no_test_module_scans_the_process_table_through_psutil() -> None:
    """No test walks the process table itself.

    A test that reaches ``process_iter`` or ``pids`` sees the real SPT of the
    machine whatever the shared fixture stubs out, and whatever it then does
    with those processes is outside the suite's control.
    """
    offenders = [
        _module_name(module) for module in _test_modules() if _walks_the_process_table(_parse(module))
    ]

    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        "import psutil\npsutil.process_iter()",
        "import psutil as ps\nps.process_iter()",
        "import psutil\npsutil.pids()",
        "from psutil import process_iter\nprocess_iter()",
        "from psutil import pids as all_pids\nall_pids()",
    ],
    ids=["dotted", "renamed-module", "pids", "imported-name", "renamed-name"],
)
def test_the_process_table_check_sees_every_way_in(source: str) -> None:
    """Each way of reaching the process table is recognised as one.

    Renaming the import or pulling the function out of the module changes
    nothing about what the code does, so the check above must not be evadable
    by writing the same scan differently.
    """
    assert _walks_the_process_table(ast.parse(source)) is True


@pytest.mark.parametrize(
    "source",
    [
        'monkeypatch.setattr("psutil.process_iter", stub)',
        '"""A docstring naming psutil.process_iter."""',
        "import psutil\npsutil.Process(1).create_time()",
    ],
    ids=["patched-by-string", "docstring", "single-process"],
)
def test_the_process_table_check_leaves_harmless_mentions_alone(source: str) -> None:
    """Naming the scan without running it is not a scan.

    Patching it by string, writing about it and asking after one known process
    all leave the process table unread, so a check that flagged them would be
    noise nobody could act on.
    """
    assert _walks_the_process_table(ast.parse(source)) is False


def test_only_the_scanner_test_imports_the_scanning_functions_by_name() -> None:
    """Direct imports of the scanning functions live in one module only.

    ``game_processes`` and its two siblings are replaced on the module object
    by the autouse fixture, which a direct ``from ... import`` copies past.
    Exactly one module may do that: the unit test of the scan itself, which has
    to reach the real implementation.
    """
    imported = {
        _module_name(module): sorted(_imported_scan_names(_parse(module)))
        for module in _test_modules()
        if _module_name(module) != SCANNER_TEST_MODULE
    }

    assert {name: names for name, names in imported.items() if names} == {}


def test_the_scanner_test_imports_the_scanning_functions_it_is_exempt_for() -> None:
    """The exemption above describes a module that really does import them.

    Without this the previous test would keep passing after the exempt module
    stopped importing anything, and the exemption would quietly widen into a
    rule nobody checks.
    """
    assert _imported_scan_names(_parse(TESTS_DIR / SCANNER_TEST_MODULE)) == set(SCAN_NAMES)


@pytest.mark.parametrize("name", STOPPING_NAMES)
def test_the_scanner_test_never_mentions_anything_that_closes_a_process(name: str) -> None:
    """The exempt module holds none of the functions that stop processes.

    This is what makes the exemption safe rather than a rubber stamp: the one
    module that sees the real process table only reads it, so even a machine
    with a running raid can run it.
    """
    source = (TESTS_DIR / SCANNER_TEST_MODULE).read_text(encoding="utf-8")

    assert name not in source


def test_the_scanner_test_ends_no_process_it_did_not_start() -> None:
    """The exempt module signals only the processes it spawned itself.

    Naming no stopping function is not enough: a single ``terminate()`` on a
    process the real scan reported would reach the developer's running game
    without any of those names appearing.
    """
    assert _foreign_stopping_calls(_parse(TESTS_DIR / SCANNER_TEST_MODULE)) == []


def test_the_stopping_check_flags_a_signal_sent_to_a_scanned_process() -> None:
    """Ending a process the scan handed over is reported by name.

    Without this the assertion above could be satisfied by a check that finds
    nothing at all, and the exemption would be a rubber stamp again.
    """
    source = "for hit in game_processes():\n    psutil.Process(hit.pid).terminate()"

    assert _foreign_stopping_calls(ast.parse(source)) == ["psutil.Process(hit.pid).terminate"]


@pytest.mark.parametrize(
    "source",
    [
        "process = spawn_process(argv)\nprocess.kill()",
        "sleeper = spawn_sleeper()\nsleeper.send_signal(15)",
    ],
    ids=["spawn-process", "spawn-sleeper"],
)
def test_the_stopping_check_allows_ending_a_process_the_test_started(source: str) -> None:
    """A test may end what it spawned itself.

    The spawning fixtures hand out throwaway processes that are torn down with
    the test anyway, so flagging those would force every process test to leave
    its own children behind.
    """
    assert _foreign_stopping_calls(ast.parse(source)) == []
