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


@pytest.mark.parametrize(
    ("existing_relpath", "target_relpath"),
    [
        pytest.param("BepInEx/plugins/ModA/Own.dll", "BepInEx/plugins/ModA/Own.dll", id="same-folder-overwrite"),
        pytest.param("BepInEx/plugins/ModA/data.json", "BepInEx/plugins/ModC/data.json", id="non-dll-file"),
        pytest.param("BepInEx/plugins/ModA/Lib.dll", "SPT/user/mods/ModC/Lib.dll", id="outside-bepinex-plugins"),
        pytest.param("BepInEx/plugins/Shared.dll", "BepInEx/plugins/Shared.dll", id="same-loose-path"),
    ],
)
def test_check_dll_duplicates_reports_nothing(fake_game_dir: Path, existing_relpath: str, target_relpath: str) -> None:
    """check_dll_duplicates stays silent unless a DLL name collides across mod folders.

    Creates the existing file and asks about the target path. "same-folder-overwrite"
    and "same-loose-path" reinstall to the very path that already holds the file, which
    is an overwrite handled by check_file_conflicts; "non-dll-file" is not a DLL at all;
    "outside-bepinex-plugins" targets the server-side mod tree. Each returns no finding,
    so the user is not warned about collisions that cannot happen.
    """
    existing = fake_game_dir / existing_relpath
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"payload")

    findings = check_dll_duplicates(fake_game_dir, [Path(target_relpath)])

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


# --------------------------------------------------------------------------
# check_dependencies
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "dependencies", "expected", "message_parts"),
    [
        pytest.param(
            [], {"RequiredMod": ">=1.0.0"}, [("missing-dependency", "error", "RequiredMod")], (), id="missing"
        ),
        pytest.param(
            [("RequiredMod", "0.5.0")],
            {"RequiredMod": ">=1.0.0"},
            [("dependency-version", "error", "RequiredMod")],
            (">=1.0.0", "0.5.0"),
            id="version-out-of-range",
        ),
        pytest.param([("RequiredMod", "1.5.0")], {"RequiredMod": ">=1.0.0"}, [], (), id="satisfied"),
        pytest.param([("requiredmod", "2.0.0")], {"RequiredMod": ">=1.0.0"}, [], (), id="name-case-insensitive"),
        pytest.param([("RequiredMod", "0.0.1")], {"RequiredMod": ""}, [], (), id="empty-range-satisfied"),
        pytest.param(
            [], {"RequiredMod": ""}, [("missing-dependency", "error", "RequiredMod")], (), id="empty-range-missing"
        ),
        pytest.param(
            [("RequiredMod", "1.0.0")],
            {"RequiredMod": "1.0.0 || 2.0.0"},
            [("dependency-version", "warning", "RequiredMod")],
            (),
            id="unsupported-range-warns",
        ),
    ],
)
def test_check_dependencies_judges_one_declared_dependency(
    installed: list[tuple[str, str]],
    dependencies: dict[str, str],
    expected: list[tuple[str, str, str]],
    message_parts: tuple[str, ...],
) -> None:
    """check_dependencies reports a dependency only when it is absent or out of range.

    Runs one declared dependency against the installed list: "missing" and
    "empty-range-missing" have nothing installed and are errors; "version-out-of-range"
    is installed at 0.5.0 against >=1.0.0 and its message names both the range and the
    installed version; "satisfied", "name-case-insensitive" (installed as "requiredmod")
    and "empty-range-satisfied" produce nothing; "unsupported-range-warns" uses the
    unparsable "1.0.0 || 2.0.0" and is downgraded to a warning. Asserted because a false
    error here blocks an install that would have worked.
    """
    findings = check_dependencies([_mod_info(name, version) for name, version in installed], meta_deps(dependencies))

    assert [(finding.kind, finding.severity, finding.mod) for finding in findings] == expected
    for part in message_parts:
        assert part in findings[0].message


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
