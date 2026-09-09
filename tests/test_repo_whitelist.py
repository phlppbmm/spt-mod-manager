"""Tests for the whitelist semantics implied by the .gitignore written by Repo.init.

Trackable: .gitignore, <runtime>/user/mods/**, <runtime>/user/profiles/*.json,
BepInEx/plugins/**, BepInEx/config/**, BepInEx/patchers/**, BepInEx/core/**,
the root-anchored loader files (/winhttp.dll, /doorstop_config.ini,
/.doorstop_version, /LICENSE-*.txt) and the SPT program set (every file
directly in <runtime>/ plus <runtime>/SPT_Data/**).
Must stay invisible (ignored): <runtime>/user/logs/**, <runtime>/user/cache/**,
<runtime>/user/certs/**, cache and log directories below <runtime>/SPT_Data/,
BepInEx/cache/**, Logs/**, .sptmod-* lock files, EscapeFromTarkov_Data/**,
<runtime>/user/profiles/backups/**, a non-root-anchored copy of a loader file
(e.g. nested/winhttp.dll, nested/LICENSE-X.txt), a root LICENSE.txt with no
version suffix, a loader name spelled differently (WINHTTP.DLL), and anything
else outside the whitelist.

The assertions go through a real git repository (``Repo.status`` on the
``initialized_repo`` fixture), not through the generated pattern text.
"""

from pathlib import Path

import pytest

from conftest import clean_repo_at
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

# The SPT program set as an installed 4.1.x release ships it, relative to the
# runtime directory: executables, native and managed libraries, the JSON and
# XML files next to them, and the static release data below SPT_Data.
_PROGRAM_FILES = (
    "SPT.Server.exe",
    "SPT.Server.Linux",
    "SPT.Server.dll",
    "SPT.Server.runtimeconfig.json",
    "SPTarkov.Server.Core.dll",
    "SPTarkov.Server.Core.xml",
    "libargon2_sharp_core.so",
    "sptLogger.json",
    "SPT_Data/checks.dat",
    "SPT_Data/configs/core.json",
    "SPT_Data/database/locales/en.json",
    "SPT_Data/Launcher/Patches/patch.bin",
)

# What the game and the manager write next to the program files and must never
# track: the caches, logs, certificates and the lock file of a running sptmod.
_IGNORED_NOISE = (
    "{runtime}/user/logs/server.log",
    "{runtime}/user/cache/entry.bin",
    "{runtime}/user/certs/cert.pem",
    "{runtime}/user/profiles/backups/old.json",
    "{runtime}/SPT_Data/cache/entry.bin",
    "{runtime}/SPT_Data/database/logs/server.log",
    "BepInEx/cache/chainloader.dat",
    "Logs/launcher.log",
    "EscapeFromTarkov_Data/foo",
    ".sptmod-lock",
)


def _write(path: Path, content: str = "content") -> None:
    """Create file with content, creating parent directories as needed.

    Calls path.parent.mkdir(parents=True, exist_ok=True) then writes content
    to path as UTF-8 text (default content is "content").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_ignored_dummies_are_not_visible_as_untracked(initialized_repo: Repo) -> None:
    """Files in ignored directories (logs, cache, backups) never appear in status."""
    status = initialized_repo.status()

    ignored = {
        Path("SPT/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT/user/profiles/backups/old.json"),
    }
    assert ignored.isdisjoint(set(status.untracked))
    assert ignored.isdisjoint(set(status.modified))


def test_untracked_under_hides_ignored_prefixes(initialized_repo: Repo) -> None:
    """Repo.untracked_under returns [] for ignored directory prefixes.

    Calls untracked_under on ignored prefixes (SPT/user/logs, EscapeFromTarkov_Data,
    SPT/user/profiles/backups). All must return [] (empty), as these are ignored.
    """
    assert initialized_repo.untracked_under(Path("SPT/user/logs")) == []
    assert initialized_repo.untracked_under(Path("EscapeFromTarkov_Data")) == []
    assert initialized_repo.untracked_under(Path("SPT/user/profiles/backups")) == []


@pytest.mark.parametrize(
    ("relative", "prefix"),
    [
        pytest.param("SPT/user/mods/TestMod/mod.dll", "SPT/user/mods", id="mods-dir"),
        pytest.param("BepInEx/plugins/TestPlugin.dll", "BepInEx", id="bepinex-plugins"),
        pytest.param("BepInEx/config/TestPlugin.cfg", "BepInEx", id="bepinex-config"),
        pytest.param("BepInEx/patchers/Fixer.dll", "BepInEx", id="bepinex-patchers"),
        pytest.param("BepInEx/core/BepInEx.dll", "BepInEx", id="bepinex-core"),
    ],
)
def test_untracked_under_shows_a_new_trackable_file(initialized_repo: Repo, relative: str, prefix: str) -> None:
    """Repo.untracked_under reports every new file the whitelist allows.

    One new file is written below the mod directory, below ``BepInEx/plugins``,
    ``BepInEx/config``, ``BepInEx/patchers`` (e.g. WTT-CommonLib's
    FixPluginTypesSerialization.dll) or ``BepInEx/core`` (the BepInEx runtime an
    SPT release ships); untracked_under on the enclosing prefix reports exactly
    that file, so all five directories are trackable alike.
    """
    _write(initialized_repo.path / relative, "trackable")

    assert initialized_repo.untracked_under(Path(prefix)) == [Path(relative)]


def test_commit_with_paths_none_stages_bepinex_patchers(
    initialized_repo: Repo,
) -> None:
    """Repo.commit(paths=None) stages BepInEx/patchers files (e.g. WTT-CommonLib).

    Creates BepInEx/patchers/Fixer.dll. Commits with paths=None. commit_hash
    show_files must include Path("BepInEx/patchers/Fixer.dll").
    """
    _write(initialized_repo.path / "BepInEx" / "patchers" / "Fixer.dll", "patcher")

    commit_hash = initialized_repo.commit("mod: whitelist patchers", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    assert Path("BepInEx/patchers/Fixer.dll") in files


@pytest.mark.parametrize(
    "relative",
    [
        pytest.param("winhttp.dll", id="winhttp-dll"),
        pytest.param("doorstop_config.ini", id="doorstop-config-ini"),
        pytest.param(".doorstop_version", id="doorstop-version"),
        pytest.param("LICENSE-BepInEx.txt", id="license-glob"),
    ],
)
def test_untracked_under_shows_new_trackable_loader_root_file(
    initialized_repo: Repo, relative: str
) -> None:
    """The root-anchored Doorstop loader files and licences an SPT release
    ships (``/winhttp.dll``, ``/doorstop_config.ini``, ``/.doorstop_version``,
    ``/LICENSE-*.txt``) must be trackable directly in the game root.
    """
    _write(initialized_repo.path / relative, "loader")

    result = initialized_repo.untracked_under(Path("."))

    assert Path(relative) in result


@pytest.mark.parametrize(
    "relative",
    [
        pytest.param("nested/winhttp.dll", id="nested-winhttp-dll"),
        pytest.param("nested/LICENSE-BepInEx.txt", id="nested-license-glob"),
        pytest.param("LICENSE.txt", id="license-without-version-suffix"),
        pytest.param("WINHTTP.DLL", id="loader-file-other-spelling"),
    ],
)
def test_root_loader_whitelist_matches_only_the_exact_root_anchored_names(
    initialized_repo: Repo, relative: str
) -> None:
    """The root-anchored loader patterns must not widen into a directory
    (``nested/winhttp.dll``, ``nested/LICENSE-*.txt``), a licence file with
    no version suffix (``LICENSE.txt``, distinct from ``LICENSE-*.txt``) or
    another spelling of a loader name (``WINHTTP.DLL``); those stay ignored
    like every other file outside the whitelist. The spelling matters because
    ``is_game_program_path`` reads the same names case-sensitively, so a
    differently spelled file belongs to neither the program set nor the
    repository.
    """
    _write(initialized_repo.path / relative, "not a loader file")

    status = initialized_repo.status()

    assert Path(relative) not in status.untracked


def test_commit_with_paths_none_stages_loader_root_files_and_bepinex_core(
    initialized_repo: Repo,
) -> None:
    """Repo.commit(paths=None) stages the loader root files and BepInEx/core.

    Creates winhttp.dll, doorstop_config.ini, .doorstop_version, a
    LICENSE-*.txt file and BepInEx/core/BepInEx.dll, all untracked. Commits
    with paths=None. commit_hash show_files must include every one of them.
    """
    root = initialized_repo.path
    _write(root / "winhttp.dll", "proxy")
    _write(root / "doorstop_config.ini", "config")
    _write(root / ".doorstop_version", "1.0.0")
    _write(root / "LICENSE-BepInEx.txt", "licence")
    _write(root / "BepInEx" / "core" / "BepInEx.dll", "runtime")

    commit_hash = initialized_repo.commit("chore: track loader files", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    assert {
        Path("winhttp.dll"),
        Path("doorstop_config.ini"),
        Path(".doorstop_version"),
        Path("LICENSE-BepInEx.txt"),
        Path("BepInEx/core/BepInEx.dll"),
    } <= files


def test_status_untracked_lists_profile_json_before_first_commit(
    initialized_repo: Repo,
) -> None:
    """Repo.status lists default profile fixture JSON as untracked initially.

    Before first commit, repo.status().untracked must include
    Path("SPT/user/profiles/abc.json") (from initialized_repo fixture).
    """
    status = initialized_repo.status()

    assert Path("SPT/user/profiles/abc.json") in status.untracked


def test_commit_with_paths_none_stages_only_whitelisted_files(
    initialized_repo: Repo,
) -> None:
    """Repo.commit(paths=None) stages all whitelisted files (mods, plugins, config, etc)."""
    _write(initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll")
    _write(initialized_repo.path / "BepInEx" / "plugins" / "TestPlugin.dll", "plugin")
    _write(initialized_repo.path / "BepInEx" / "config" / "TestPlugin.cfg", "cfg")

    commit_hash = initialized_repo.commit("mod: whitelist roundtrip", paths=None)
    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}

    expected = {
        Path(".gitignore"),
        Path("SPT/user/profiles/abc.json"),
        Path("SPT/SPT_Data/configs/core.json"),
        Path("SPT/user/mods/TestMod/mod.dll"),
        Path("BepInEx/plugins/TestPlugin.dll"),
        Path("BepInEx/config/TestPlugin.cfg"),
    }
    assert files == expected


def test_is_clean_after_full_whitelist_commit_ignores_leftover_dummies(
    initialized_repo: Repo,
) -> None:
    """Repo.is_clean ignores leftover ignored files after whitelist commit.

    Creates SPT/user/mods/TestMod/mod.dll and commits with paths=None.
    Ignored dummy files from fake_game_dir still on disk. repo.is_clean()
    must return True (ignores the dummies, they're not tracked).
    """
    _write(initialized_repo.path / "SPT" / "user" / "mods" / "TestMod" / "mod.dll", "dll")
    initialized_repo.commit("mod: whitelist roundtrip", paths=None)

    # The ignored dummy files from fake_game_dir are still on disk but must
    # not make the tree appear dirty.
    assert initialized_repo.is_clean() is True


@pytest.mark.parametrize(
    ("game_dir_fixture", "runtime"),
    [
        pytest.param("fake_game_dir", "SPT", id="spt-4.0-layout"),
        pytest.param("fake_game_dir_runtime", "SPT_Runtime", id="spt-4.1-layout"),
    ],
)
def test_whitelist_tracks_the_spt_program_set(
    game_dir_fixture: str, runtime: str, request: pytest.FixtureRequest
) -> None:
    """The program set of both layouts is committed by a whitelist commit.

    The executables, libraries, JSON and XML files directly in the runtime
    directory and the whole SPT_Data tree are written into a fresh game
    directory and committed with ``paths=None``; git must have staged every one
    of them, because only then is an SPT update one commit and its rollback one
    revert.
    """
    game_dir: Path = request.getfixturevalue(game_dir_fixture)
    expected = {Path(runtime) / relative for relative in _PROGRAM_FILES}
    for path in expected:
        _write(game_dir / path)
    repo = Repo.init(detect_layout(game_dir))

    commit_hash = repo.commit("chore: baseline", paths=None)

    assert expected <= {mod_file.path for mod_file in repo.show_files(commit_hash)}


@pytest.mark.parametrize(
    ("game_dir_fixture", "runtime"),
    [
        pytest.param("fake_game_dir", "SPT", id="spt-4.0-layout"),
        pytest.param("fake_game_dir_runtime", "SPT_Runtime", id="spt-4.1-layout"),
    ],
)
def test_whitelist_keeps_caches_logs_and_lock_files_invisible(
    game_dir_fixture: str, runtime: str, request: pytest.FixtureRequest
) -> None:
    """Caches, logs, certificates and lock files stay invisible in both layouts.

    Tracking the program set must not sweep in the data the game and the
    manager write while running, so none of these paths may show up as
    untracked or land in a whitelist commit.
    """
    game_dir: Path = request.getfixturevalue(game_dir_fixture)
    noise = {Path(template.format(runtime=runtime)) for template in _IGNORED_NOISE}
    for path in noise:
        _write(game_dir / path)
    repo = clean_repo_at(game_dir)

    baseline = repo.log()[0].hash
    assert noise.isdisjoint(set(repo.status().untracked))
    assert noise.isdisjoint({mod_file.path for mod_file in repo.show_files(baseline)})
