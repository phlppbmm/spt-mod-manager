"""Unit tests for ``sptmod.core.conflicts``.

Covers ``check_file_conflicts`` (tracked/untracked existing files),
``check_dll_duplicates`` (cross-folder DLL name collisions),
``check_dependencies`` (missing/out-of-range/unsupported dependency ranges)
``check_spt_compat`` (sptVersion mismatch as a warning) and ``apply_force``
(error-to-warning downgrade). Written against the public functions of
``sptmod.core.conflicts``; they fail against the current ``[]`` stubs.
"""

import re
from datetime import datetime
from pathlib import Path

import pytest

from fixture_types import InstallCommit
from sptmod.core.conflicts import (
    apply_force,
    check_dependencies,
    check_dll_duplicates,
    check_file_conflicts,
    check_spt_compat,
)
from sptmod.core.errors import GitError
from sptmod.core.models import Conflict, ModInfo, ModState, ModType
from sptmod.core.modmeta import ModMeta
from sptmod.core.repo import Repo


def _mod_info(
    name: str, version: str, state: ModState = ModState.INSTALLED
) -> ModInfo:
    """Construct ModInfo with fixed fields except name, version, and state.

    Sets type='server', author='tester', commit='deadbeef', date=2024-01-01,
    empty spt_range, forge_id, forge_version_id, guid, and source. state
    defaults to 'installed' but can be overridden to 'disabled' or 'removed'.
    """
    return ModInfo(
        name=name,
        version=version,
        mod_type=ModType.SERVER,
        author="tester",
        spt_range="",
        state=state,
        commit="deadbeef",
        date=datetime(2024, 1, 1),
        forge_id="",
        forge_version_id="",
        guid="",
        source="",
    )


# --------------------------------------------------------------------------
# check_file_conflicts
# --------------------------------------------------------------------------


def test_check_file_conflicts_ignores_files_that_do_not_exist_yet(
    clean_repo: Repo,
) -> None:
    """check_file_conflicts returns empty list for new files not on disk.

    Calls check_file_conflicts(clean_repo, [Path("BepInEx/plugins/New.dll")])
    where the file never existed. Must return [] (no findings), as conflicts
    only apply to existing files.
    """
    findings = check_file_conflicts(clean_repo, [Path("BepInEx/plugins/New.dll")])

    assert not findings


def test_check_file_conflicts_reports_tracked_file_naming_owning_mod(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """check_file_conflicts reports tracked files with their owning mod name.

    Installs ModA (version 1.0.0) with BepInEx/plugins/ModA.dll. Calling
    check_file_conflicts(clean_repo, [Path("BepInEx/plugins/ModA.dll")])
    returns a finding with kind='tracked-file', severity='error', path matches,
    mod='ModA', and message includes "ModA".
    """
    install_commit(
        clean_repo,
        "ModA",
        version="1.0.0",
        files={"BepInEx/plugins/ModA.dll": "content"},
    )

    findings = check_file_conflicts(clean_repo, [Path("BepInEx/plugins/ModA.dll")])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "tracked-file"
    assert finding.severity == "error"
    assert finding.path == Path("BepInEx/plugins/ModA.dll")
    assert finding.mod == "ModA"
    assert "ModA" in finding.message


def test_check_file_conflicts_reports_empty_mod_when_last_commit_has_no_mod_trailer(
    clean_repo: Repo,
) -> None:
    """check_file_conflicts reports empty mod name for non-mod commits.

    The baseline commit in clean_repo tracks .gitignore with Spt-Version trailer
    but no Mod trailer. check_file_conflicts(clean_repo, [Path(".gitignore")])
    returns a finding with kind='tracked-file', mod="" (empty string).
    """
    # clean_repo's baseline commit tracks .gitignore via a "Spt-Version"
    # trailer only, with no "Mod" trailer.
    findings = check_file_conflicts(clean_repo, [Path(".gitignore")])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "tracked-file"
    assert finding.severity == "error"
    assert finding.mod == ""


def test_check_file_conflicts_reports_untracked_file_with_adopt_hint(
    clean_repo: Repo,
) -> None:
    """check_file_conflicts reports untracked files with adoption suggestion.

    Creates stray file BepInEx/config/Stray.cfg on disk (never committed).
    check_file_conflicts(clean_repo, [Path("BepInEx/config/Stray.cfg")])
    returns a finding with kind='untracked-file', severity='error', and
    message containing "adopt" (lowercase).
    """
    # Present on disk but never committed.
    stray = clean_repo.path / "BepInEx" / "config" / "Stray.cfg"
    stray.write_text("stray", encoding="utf-8")

    findings = check_file_conflicts(clean_repo, [Path("BepInEx/config/Stray.cfg")])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "untracked-file"
    assert finding.severity == "error"
    assert finding.path == Path("BepInEx/config/Stray.cfg")
    assert "adopt" in finding.message.lower()


def test_check_file_conflicts_keeps_the_order_of_target_files(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """check_file_conflicts preserves input order in returned findings.

    Installs ModA (BepInEx/plugins/ModA.dll). Creates stray BepInEx/config/Stray.cfg.
    check_file_conflicts(clean_repo, [Path("BepInEx/config/Stray.cfg"),
    Path("BepInEx/plugins/ModA.dll")]) returns findings with kind in
    ['untracked-file', 'tracked-file'] order and paths in input order.
    """
    install_commit(
        clean_repo,
        "ModA",
        files={"BepInEx/plugins/ModA.dll": "content"},
    )
    stray = clean_repo.path / "BepInEx" / "config" / "Stray.cfg"
    stray.write_text("stray", encoding="utf-8")

    findings = check_file_conflicts(
        clean_repo,
        [Path("BepInEx/config/Stray.cfg"), Path("BepInEx/plugins/ModA.dll")],
    )

    assert [finding.kind for finding in findings] == ["untracked-file", "tracked-file"]
    assert [finding.path for finding in findings] == [
        Path("BepInEx/config/Stray.cfg"),
        Path("BepInEx/plugins/ModA.dll"),
    ]


def test_check_file_conflicts_propagates_git_error(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """A broken repository surfaces as an error instead of a silent finding."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "content"})
    (clean_repo.path / ".git" / "HEAD").write_text("ref: refs/heads/gone\n", encoding="utf-8")

    with pytest.raises(GitError):
        check_file_conflicts(clean_repo, [Path("BepInEx/plugins/ModA.dll")])


# --------------------------------------------------------------------------
# check_dll_duplicates
# --------------------------------------------------------------------------


def test_check_dll_duplicates_warns_on_same_name_in_different_mod_folder(
    fake_game_dir: Path,
) -> None:
    """check_dll_duplicates warns when same DLL name exists in different mod folders.

    Creates existing BepInEx/plugins/ModA/Newtonsoft.Json.dll. Calls
    check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModC/Newtonsoft.Json.dll")]).
    Returns one finding: kind='dll-duplicate', severity='warning',
    path=existing file, mod='ModA'.
    """
    existing = fake_game_dir / "BepInEx" / "plugins" / "ModA" / "Newtonsoft.Json.dll"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"shared-lib")

    findings = check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModC/Newtonsoft.Json.dll")])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "dll-duplicate"
    assert finding.severity == "warning"
    assert finding.path == Path("BepInEx/plugins/ModA/Newtonsoft.Json.dll")
    assert finding.mod == "ModA"


def test_check_dll_duplicates_matches_case_insensitively(fake_game_dir: Path) -> None:
    """DLL duplicate detection is case-insensitive (Windows behavior)."""
    existing = fake_game_dir / "BepInEx" / "plugins" / "ModA" / "Shared.DLL"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"shared-lib")

    findings = check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModC/shared.dll")])

    assert len(findings) == 1
    assert findings[0].kind == "dll-duplicate"


def test_check_dll_duplicates_ignores_same_folder_same_name_overwrite(
    fake_game_dir: Path,
) -> None:
    """check_dll_duplicates ignores same-folder overwrites (not cross-folder).

    Creates existing BepInEx/plugins/ModA/Own.dll. Calls check_dll_duplicates
    (fake_game_dir, [Path("BepInEx/plugins/ModA/Own.dll")]). Returns [] (empty),
    as reinstalling to the same folder is an overwrite, not a duplicate.
    """
    existing = fake_game_dir / "BepInEx" / "plugins" / "ModA" / "Own.dll"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"own")

    findings = check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModA/Own.dll")])

    assert not findings


def test_check_dll_duplicates_ignores_non_dll_files(fake_game_dir: Path) -> None:
    """check_dll_duplicates ignores non-.dll files (e.g., .json config).

    Creates existing BepInEx/plugins/ModA/data.json. Calls check_dll_duplicates
    (fake_game_dir, [Path("BepInEx/plugins/ModC/data.json")]). Returns []
    (empty), as only .dll extensions trigger duplicate checking.
    """
    existing = fake_game_dir / "BepInEx" / "plugins" / "ModA" / "data.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("{}", encoding="utf-8")

    findings = check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModC/data.json")])

    assert not findings


def test_check_dll_duplicates_ignores_dlls_outside_bepinex_plugins(
    fake_game_dir: Path,
) -> None:
    """Only DLLs in BepInEx/plugins are checked; server-side DLLs are ignored."""
    existing = fake_game_dir / "BepInEx" / "plugins" / "ModA" / "Lib.dll"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"lib")

    findings = check_dll_duplicates(fake_game_dir, [Path("SPT/user/mods/ModC/Lib.dll")])

    assert not findings


def test_check_dll_duplicates_loose_plugin_is_not_described_as_a_mod_folder(
    fake_game_dir: Path,
) -> None:
    """check_dll_duplicates doesn't confuse loose DLL filenames with mod folder names.

    Creates loose BepInEx/plugins/Shared.dll (no mod subfolder). Calls
    check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModC/Shared.dll")]).
    Returns a finding with kind='dll-duplicate'. Message must not contain
    "mod folder Shared.dll" (loose file name ≠ mod folder owning it).
    """
    # Existing DLL directly under BepInEx/plugins/, no mod subfolder.
    existing = fake_game_dir / "BepInEx" / "plugins" / "Shared.dll"
    existing.write_bytes(b"shared-lib")

    findings = check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/ModC/Shared.dll")])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "dll-duplicate"
    assert finding.severity == "warning"
    # The loose file's own name must never be reported as if it were the mod
    # folder that owns it (e.g. "shipped by mod folder Shared.dll").
    assert not re.search(rf"mod folder\s+{re.escape(existing.name)}\b", finding.message, re.IGNORECASE)


def test_check_dll_duplicates_same_loose_path_is_not_a_duplicate(
    fake_game_dir: Path,
) -> None:
    """check_dll_duplicates ignores loose DLL reinstalls to the same path.

    Creates existing loose BepInEx/plugins/Shared.dll. Calls check_dll_duplicates
    (fake_game_dir, [Path("BepInEx/plugins/Shared.dll")]). Returns [] (empty),
    as same-path reinstalls are overwrites, left to check_file_conflicts, not
    cross-folder duplicates.
    """
    # Same loose file targeted at its own existing path: an overwrite, left
    # to check_file_conflicts, not a cross-folder duplicate.
    existing = fake_game_dir / "BepInEx" / "plugins" / "Shared.dll"
    existing.write_bytes(b"shared-lib")

    findings = check_dll_duplicates(fake_game_dir, [Path("BepInEx/plugins/Shared.dll")])

    assert not findings


# --------------------------------------------------------------------------
# check_dependencies
# --------------------------------------------------------------------------


def test_check_dependencies_reports_missing_dependency() -> None:
    """check_dependencies reports missing required dependency as error.

    Calls check_dependencies([], meta_deps({"RequiredMod": ">=1.0.0"}))
    with empty installed list. Returns one finding: kind='missing-dependency',
    severity='error', mod='RequiredMod'.
    """
    findings = check_dependencies([], meta_deps({"RequiredMod": ">=1.0.0"}))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "missing-dependency"
    assert finding.severity == "error"
    assert finding.mod == "RequiredMod"


def test_check_dependencies_reports_out_of_range_version() -> None:
    """check_dependencies reports dependency version mismatch as error.

    Installs RequiredMod version 0.5.0. Calls check_dependencies(installed,
    meta_deps({"RequiredMod": ">=1.0.0"})). Returns one finding:
    kind='dependency-version', severity='error', mod='RequiredMod', message
    includes ">=1.0.0" and "0.5.0".
    """
    installed = [_mod_info("RequiredMod", "0.5.0")]

    findings = check_dependencies(installed, meta_deps({"RequiredMod": ">=1.0.0"}))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "dependency-version"
    assert finding.severity == "error"
    assert finding.mod == "RequiredMod"
    assert ">=1.0.0" in finding.message
    assert "0.5.0" in finding.message


def test_check_dependencies_matches_names_case_insensitively() -> None:
    """check_dependencies matches dependency names case-insensitively.

    Installs "requiredmod" (lowercase) version 2.0.0. Calls check_dependencies
    (installed, meta_deps({"RequiredMod": ">=1.0.0"})). Returns [] (empty),
    proving names are normalized by case.
    """
    installed = [_mod_info("requiredmod", "2.0.0")]

    findings = check_dependencies(installed, meta_deps({"RequiredMod": ">=1.0.0"}))

    assert not findings


def test_check_dependencies_satisfied_range_produces_no_finding() -> None:
    """check_dependencies returns empty list for satisfied dependency range.

    Installs RequiredMod version 1.5.0. Calls check_dependencies(installed,
    meta_deps({"RequiredMod": ">=1.0.0"})). Returns [] (empty), as 1.5.0
    satisfies >=1.0.0.
    """
    installed = [_mod_info("RequiredMod", "1.5.0")]

    findings = check_dependencies(installed, meta_deps({"RequiredMod": ">=1.0.0"}))

    assert not findings


def test_check_dependencies_empty_range_matches_any_installed_version() -> None:
    """Empty version range (unversioned dependency) matches any installed version."""
    installed = [_mod_info("RequiredMod", "0.0.1")]

    findings = check_dependencies(installed, meta_deps({"RequiredMod": ""}))

    assert not findings


def test_check_dependencies_empty_range_still_requires_presence() -> None:
    """check_dependencies requires presence even with empty version range.

    Calls check_dependencies([], meta_deps({"RequiredMod": ""})) with empty
    installed list and empty range (no version constraint). Returns one finding:
    kind='missing-dependency' (mod must be installed, even without version bounds).
    """
    findings = check_dependencies([], meta_deps({"RequiredMod": ""}))

    assert len(findings) == 1
    assert findings[0].kind == "missing-dependency"


def test_check_dependencies_unsupported_range_is_a_warning_not_an_error() -> None:
    """check_dependencies treats unsupported range syntax as warning, not error.

    Installs RequiredMod version 1.0.0. Calls check_dependencies(installed,
    meta_deps({"RequiredMod": "1.0.0 || 2.0.0"})) with unsupported || syntax.
    Returns one finding: kind='dependency-version', severity='warning' (not error).
    """
    installed = [_mod_info("RequiredMod", "1.0.0")]

    findings = check_dependencies(installed, meta_deps({"RequiredMod": "1.0.0 || 2.0.0"}))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "dependency-version"
    assert finding.severity == "warning"


def test_check_dependencies_lists_all_offenders_in_order() -> None:
    """check_dependencies reports all violations preserving order.

    Installs PresentButOld version 0.1.0. Calls check_dependencies(installed,
    meta_deps({"MissingMod": ">=1.0.0", "PresentButOld": ">=1.0.0"})).
    Returns findings with kind=['missing-dependency', 'dependency-version']
    and mod=['MissingMod', 'PresentButOld'] in input order.
    """
    installed = [_mod_info("PresentButOld", "0.1.0")]

    findings = check_dependencies(
        installed,
        meta_deps({"MissingMod": ">=1.0.0", "PresentButOld": ">=1.0.0"}),
    )

    assert [finding.kind for finding in findings] == [
        "missing-dependency",
        "dependency-version",
    ]
    assert [finding.mod for finding in findings] == ["MissingMod", "PresentButOld"]


def meta_deps(dependencies: dict[str, str]) -> ModMeta:
    """Construct ModMeta with fixed name/version and custom dependencies dict.

    Returns ModMeta(name="TestMod", version="1.0.0", dependencies=dependencies).
    """
    return ModMeta(name="TestMod", version="1.0.0", dependencies=dependencies)


# --------------------------------------------------------------------------
# check_spt_compat
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spt_version,spt_range",
    [
        ("", "~4.1.0"),
        ("4.1.0", ""),
        ("unknown", "~4.1.0"),
        ("4.1.5", "~4.1.0"),
    ],
)
def test_check_spt_compat_produces_no_finding(spt_version: str, spt_range: str) -> None:
    """Compatible SPT versions and missing/unknown version/range produce no findings."""
    assert not check_spt_compat(spt_version, spt_range)


def test_check_spt_compat_mismatch_is_a_single_warning() -> None:
    """check_spt_compat reports version mismatch as single warning finding.

    Calls check_spt_compat("4.0.0", "~4.1.0") (mismatch). Returns one finding:
    kind='spt-version', severity='warning' (not error).
    """
    findings = check_spt_compat("4.0.0", "~4.1.0")

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "spt-version"
    assert finding.severity == "warning"


def test_check_spt_compat_unsupported_range_is_a_warning_naming_the_range() -> None:
    """check_spt_compat reports unsupported range syntax as warning with range.

    Calls check_spt_compat("4.1.0", "4.0.0 || 4.1.0") with unsupported || syntax.
    Returns one finding: kind='spt-version', severity='warning', message contains
    "4.0.0 || 4.1.0" (the unsupported range).
    """
    findings = check_spt_compat("4.1.0", "4.0.0 || 4.1.0")

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "spt-version"
    assert finding.severity == "warning"
    assert "4.0.0 || 4.1.0" in finding.message


# --------------------------------------------------------------------------
# apply_force
# --------------------------------------------------------------------------


def test_apply_force_false_leaves_severities_unchanged() -> None:
    """apply_force(conflicts, False) preserves all severity levels unchanged.

    Creates conflicts list with ['error', 'warning']. Calls apply_force(conflicts,
    False). Returned severities match input exactly ['error', 'warning'].
    """
    conflicts = [
        Conflict(kind="tracked-file", severity="error", message="a"),
        Conflict(kind="dll-duplicate", severity="warning", message="b"),
    ]

    result = apply_force(conflicts, False)

    assert [finding.severity for finding in result] == ["error", "warning"]


def test_apply_force_true_downgrades_errors_to_warnings() -> None:
    """apply_force(conflicts, True) downgrades error severity to warning.

    Creates conflicts with severity ['error', 'warning', 'error']. Calls
    apply_force(conflicts, True). All returned severities become 'warning'
    ['warning', 'warning', 'warning'], with kind unchanged.
    """
    conflicts = [
        Conflict(kind="tracked-file", severity="error", message="a"),
        Conflict(kind="dll-duplicate", severity="warning", message="b"),
        Conflict(kind="missing-dependency", severity="error", message="c"),
    ]

    result = apply_force(conflicts, True)

    assert [finding.severity for finding in result] == ["warning", "warning", "warning"]
    assert [finding.kind for finding in result] == [
        "tracked-file",
        "dll-duplicate",
        "missing-dependency",
    ]
