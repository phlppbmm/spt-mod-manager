"""File, DLL and dependency checks run before an install copies anything."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from sptmod.core.game_dir import PLUGINS_DIR
from sptmod.core.models import TRAILER_MOD, Conflict, ModInfo
from sptmod.core.modmeta import (
    DLL_SUFFIX,
    ModMeta,
    UnsupportedRangeError,
    version_matches,
)
from sptmod.core.repo import Repo


def check_file_conflicts(repo: Repo, target_files: Sequence[Path]) -> list[Conflict]:
    """Report the files of ``target_files`` that already exist in the game dir.

    ``target_files`` are game-directory-relative paths the archive wants to
    write; the game directory is ``repo.path``. Files that do not exist yet
    produce no finding. For an existing file:

    ``tracked-file``
        The mod of the youngest commit touching the file (``Mod`` trailer of
        ``repo.last_touching``) owns it; the message names that mod and
        ``mod`` carries its name. A tracked file whose last commit carries no
        ``Mod`` trailer -- the initial commit, a snapshot -- is reported with
        an empty ``mod`` and a message naming only the file.
    ``untracked-file``
        The file is present but not tracked; the message hints at ``adopt``.

    Both are errors. Findings keep the order of ``target_files``.

    Raises:
        GitError: git failed.
    """
    findings: list[Conflict] = []
    for target in target_files:
        if not (repo.path / target).exists():
            continue
        commit = repo.last_touching(target)
        if commit is None:
            findings.append(
                Conflict(
                    kind="untracked-file",
                    severity="error",
                    message=(
                        f"{target.as_posix()} is manually present and not tracked, "
                        f"adopt it first"
                    ),
                    path=target,
                )
            )
            continue
        owner = commit.trailers.get(TRAILER_MOD, "")
        message = (
            f"{target.as_posix()} is already provided by mod {owner}"
            if owner
            else f"{target.as_posix()} is already tracked in the repository"
        )
        findings.append(
            Conflict(
                kind="tracked-file",
                severity="error",
                message=message,
                path=target,
                mod=owner,
            )
        )
    return findings


def check_dll_duplicates(
    game_dir: Path, target_files: Sequence[Path]
) -> list[Conflict]:
    """Report DLLs of ``target_files`` that already exist in another mod folder.

    Only ``.dll`` files below ``BepInEx/plugins`` are considered. A finding of
    kind ``dll-duplicate`` is produced for every existing DLL anywhere below
    ``BepInEx/plugins`` that has the same file name (compared
    case-insensitively) but sits in a different mod folder, meaning a
    different first path component below ``BepInEx/plugins``; a DLL directly
    in ``BepInEx/plugins`` counts as its own folder. Same folder and same file
    name is an overwrite, not a duplicate, and is left to
    :func:`check_file_conflicts`.

    Shared libraries shipped by several mods are normal, so these findings are
    always warnings. ``path`` is the existing DLL, ``mod`` the mod folder name
    it lives in, empty for a loose DLL directly in ``BepInEx/plugins``.
    """
    findings: list[Conflict] = []
    for target in target_files:
        if target.suffix.lower() != DLL_SUFFIX or not target.is_relative_to(
            PLUGINS_DIR
        ):
            continue
        for existing in sorted((game_dir / PLUGINS_DIR).rglob("*")):
            if existing.suffix.lower() != DLL_SUFFIX or not existing.is_file():
                continue
            relative = existing.relative_to(game_dir)
            folder = _mod_folder(relative)
            if relative.name.casefold() != target.name.casefold():
                continue
            if folder == _mod_folder(target):
                continue
            loose = folder == relative
            findings.append(
                Conflict(
                    kind="dll-duplicate",
                    severity="warning",
                    message=(
                        f"{target.name} is also present in {relative.as_posix()}, "
                        + (
                            "a loose plugin file"
                            if loose
                            else f"shipped by mod folder {folder.name}"
                        )
                    ),
                    path=relative,
                    mod="" if loose else folder.name,
                )
            )
    return findings


def check_dependencies(
    installed_mods: Sequence[ModInfo], package_meta: ModMeta
) -> list[Conflict]:
    """Check ``package_meta.dependencies`` against the installed mods.

    ``installed_mods`` are the mods that count as present, that is the active
    ones; the caller filters, this function does not look at states. Names are
    matched case-insensitively. Per dependency entry (name to semver range):

    ``missing-dependency``
        No mod of that name is in ``installed_mods``. Error.
    ``dependency-version``
        A mod of that name exists but its version is outside the range. The
        message names the required range and the installed version. Error.

    An entry whose range or whose installed version the matcher does not
    understand (``UnsupportedRangeError``) is reported as a
    ``dependency-version`` warning instead of an error: an unevaluable range
    must not block an install. An empty range matches every version.
    """
    by_name = {mod.name.casefold(): mod for mod in installed_mods}
    findings: list[Conflict] = []
    for name, range_spec in package_meta.dependencies.items():
        requirement = f"{name} {range_spec}".strip()
        installed = by_name.get(name.casefold())
        if installed is None:
            findings.append(
                Conflict(
                    kind="missing-dependency",
                    severity="error",
                    message=f"requires {requirement}, which is not installed",
                    mod=name,
                )
            )
            continue
        try:
            satisfied = version_matches(installed.version, range_spec)
        except UnsupportedRangeError:
            findings.append(
                Conflict(
                    kind="dependency-version",
                    severity="warning",
                    message=(
                        f"cannot check {requirement} against the installed "
                        f"version {installed.version}"
                    ),
                    mod=name,
                )
            )
            continue
        if not satisfied:
            findings.append(
                Conflict(
                    kind="dependency-version",
                    severity="error",
                    message=(
                        f"requires {requirement}, installed is {installed.version}"
                    ),
                    mod=name,
                )
            )
    return findings


def check_spt_compat(spt_version: str, spt_range: str) -> list[Conflict]:
    """Check the detected SPT version against the mod's ``sptVersion`` range.

    Returns at most one finding of kind ``spt-version``, always a warning,
    because published ranges are routinely stricter than reality. Returns no
    finding when ``spt_version`` or ``spt_range`` is empty, when the version is
    unknown, or when the version satisfies the range. A range the matcher does
    not understand yields a warning naming the range.
    """
    if not spt_version or not spt_range or not _is_version(spt_version):
        return []
    try:
        satisfied = version_matches(spt_version, spt_range)
    except UnsupportedRangeError:
        return [
            Conflict(
                kind="spt-version",
                severity="warning",
                message=(
                    f"cannot check the SPT range {spt_range!r} against the "
                    f"installed SPT {spt_version}"
                ),
            )
        ]
    if satisfied:
        return []
    return [
        Conflict(
            kind="spt-version",
            severity="warning",
            message=(
                f"the mod declares SPT {spt_range}, installed is SPT {spt_version}"
            ),
        )
    ]


def apply_force(conflicts: Sequence[Conflict], force: bool) -> list[Conflict]:
    """Return ``conflicts`` with every error downgraded to a warning.

    Without ``force`` the findings are returned unchanged. This is the only
    place where ``force`` changes a severity: the check functions themselves
    always report the natural severity of a finding.
    """
    if not force:
        return list(conflicts)
    return [
        replace(finding, severity="warning") if finding.severity == "error" else finding
        for finding in conflicts
    ]


def _mod_folder(plugin_file: Path) -> Path:
    return PLUGINS_DIR / plugin_file.relative_to(PLUGINS_DIR).parts[0]


def _is_version(text: str) -> bool:
    try:
        version_matches(text, "")
    except UnsupportedRangeError:
        return False
    return True
