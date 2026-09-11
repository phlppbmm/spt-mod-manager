"""Installing a mod from a local archive file.

The archive is unpacked into a staging directory, checked against the mods that
are already installed and then committed in one go. Everything the manager
learns about the mod -- name, version, type -- comes from the archive itself.
"""

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sptmod.core.archive import (
    ArchiveLayout,
    extract,
    find_anchor,
)
from sptmod.core.conflicts import (
    apply_force,
    check_dependencies,
    check_dll_duplicates,
    check_file_conflicts,
    check_spt_compat,
)
from sptmod.core.errors import (
    ConflictError,
    DependencyError,
    GitError,
)
from sptmod.core.game_dir import (
    GameLayout,
    detect_layout,
    resolve_spt_version,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    Conflict,
    ModInfo,
    ModState,
    ModType,
    Progress,
)
from sptmod.core.modmeta import (
    PACKAGE_JSON_NAME,
    InvalidMetadataError,
    ModMeta,
    build_trailers,
    meta_from_dll,
    read_package_json,
)
from sptmod.core.repo import Repo
from sptmod.core.services.common import (
    DEPENDENCY_KINDS,
    UNSET_DATE,
    InstallStep,
    blocked_message,
    current_mods,
    for_game_layout,
    mod_prefixes,
    prune_empty,
    report_step,
    write_mod_files,
)
from sptmod.core.services.state import (
    ensure_installable,
)
from sptmod.core.workspace import (
    game_lock,
    is_locked,
    staging_dir,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


@dataclass(frozen=True)
class InstallResult:
    """Outcome of :func:`install_from_archive`.

    ``warnings`` holds the non-blocking findings of the conflict checks,
    including the ones ``force`` downgraded from errors.
    """

    mod: ModInfo
    files: list[Path] = field(default_factory=list[Path])
    warnings: list[Conflict] = field(default_factory=list[Conflict])


def install_from_archive(
    path: Path,
    archive: Path,
    name_override: str | None = None,
    version_override: str | None = None,
    force: bool = False,
    progress: Progress | None = None,
    *,
    forge_id: str = "",
    forge_version_id: str = "",
    source: str | None = None,
) -> InstallResult:
    """Install the mod archive ``archive`` into the game directory ``path``.

    The shared install core, also used by the Forge path. Steps, each reported
    through ``progress`` in the order of ``INSTALL_STEPS``:

    ``check``
        The working tree is clean and neither server nor client is running.
    ``extract``
        The archive is unpacked into a temporary directory, which is removed
        again on success and on failure.
    ``anchor``
        The mod trees are located inside the extraction and the mod type is
        determined.
    ``metadata``
        ``package.json`` for server and hybrid mods, DLL file name for
        client-only mods. Trailers are validated pre-copy to prevent
        half-installed states. ``name_override`` and ``version_override``
        override extracted values.
    ``conflicts``
        File, DLL and dependency checks; ``force`` downgrades them to warnings.
    ``copy``
        The anchored files are copied into the game directory.
    ``commit``
        One commit ``mod: <name> <version>`` with the full set of mod trailers,
        ``Mod-State: installed`` and ``Mod-Source`` set to the archive file
        name, or to ``source`` where the caller passes one: the Forge release
        link for a Forge install, the project folder for a local one.

    On commit failure, newly-copied files and empty parent directories are
    rolled back. Pre-existing files that were overwritten are not restored;
    this is acceptable because the game tree remains consistent and invalid
    states can be fixed by later operations.

    ``forge_id`` and ``forge_version_id`` are written as ``Mod-Forge-Id`` and
    ``Mod-Forge-Version-Id``; :func:`install_mod` fills them from the install
    plan. Every other caller installs a mod the Forge is never asked about:
    nothing guesses a Forge mod from the name and the version, so a fork
    cannot inherit the identity of the mod it was forked from.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        DirtyTreeError: the working tree has uncommitted or untracked changes.
        GameRunningError: the SPT server or client is running.
        ArchiveError: the archive is unreadable, unsafe, has no anchor or
            holds no mod file.
        InvalidMetadataError: mandatory mod metadata is missing.
        ConflictError: a file or DLL conflict blocks the install and ``force``
            is not set.
        DependencyError: a required dependency is missing or has an
            incompatible version and ``force`` is not set.
        ModFileWriteError: a file of the mod could not be written into the
            game directory.
        GitError: git failed or metadata contains invalid trailer values.
    """
    game_layout = detect_layout(path)
    repo = Repo(path)
    report_step(
        progress, InstallStep.CHECK, "checking the working tree and running processes"
    )
    if not is_locked(path):
        ensure_installable(repo)

    with tempfile.TemporaryDirectory(prefix="sptmod-", dir=staging_dir()) as temp_name:
        temp = Path(temp_name)
        report_step(progress, InstallStep.EXTRACT, f"extracting {archive.name}")
        extract(archive, temp)

        report_step(progress, InstallStep.ANCHOR, "searching the mod anchor")
        layout = for_game_layout(find_anchor(temp), game_layout)

        report_step(progress, InstallStep.METADATA, "reading the mod metadata")
        meta = read_meta(
            layout, game_layout.mods_dir, name_override, version_override, archive
        )

        with game_lock(path, f"installing {meta.name}", progress):
            ensure_installable(repo)

            report_step(
                progress, InstallStep.CONFLICTS, "checking conflicts and dependencies"
            )
            warnings = _check_install_conflicts(repo, game_layout, layout, meta, force)

            report_step(
                progress, InstallStep.COPY, f"copying {len(layout.files)} files"
            )
            added = [file for file in layout.files if not (path / file).exists()]
            write_mod_files(path, layout)

            report_step(
                progress, InstallStep.COMMIT, f"committing {meta.name} {meta.version}"
            )
            draft = ModInfo(
                name=meta.name,
                version=meta.version,
                mod_type=layout.mod_type,
                author=meta.author,
                spt_range=meta.spt_range,
                state=ModState.INSTALLED,
                commit="",
                date=UNSET_DATE,
                forge_id=forge_id,
                forge_version_id=forge_version_id,
                guid=meta.guid,
                source=archive.name if source is None else source,
            )
            try:
                commit_hash = repo.commit(
                    f"mod: {meta.name} {meta.version}",
                    trailers=build_trailers(draft),
                    paths=layout.files,
                )
            except Exception:
                _remove_copied(path, added, game_layout)
                raise
            committed = repo.log()[0]
    report_step(
        progress,
        InstallStep.COMMIT,
        f"installed {meta.name} {meta.version}",
        percent=100,
    )
    return InstallResult(
        mod=replace(draft, commit=commit_hash, date=committed.date),
        files=list(layout.files),
        warnings=warnings,
    )


def _check_install_conflicts(
    repo: Repo,
    game_layout: GameLayout,
    layout: ArchiveLayout,
    meta: ModMeta,
    force: bool,
) -> list[Conflict]:
    """Seam for the file, DLL and dependency checks of ``core.conflicts``.

    Runs the file, DLL, dependency and SPT-compatibility checks, applies
    ``force`` to their findings and returns the remaining warnings. Blocking
    findings raise ``ConflictError`` for file and DLL problems and
    ``DependencyError`` for dependency problems; both carry the complete list
    of findings, warnings included.
    """
    active = [mod for mod in current_mods(repo) if mod.state in ACTIVE_STATES]
    findings = apply_force(
        [
            *check_file_conflicts(repo, layout.files),
            *check_dll_duplicates(repo.path, layout.files),
            *check_dependencies(active, meta),
            *check_spt_compat(
                resolve_spt_version(game_layout, repo) or "", meta.spt_range
            ),
        ],
        force,
    )
    blocking = [finding for finding in findings if finding.severity == "error"]
    files = [finding for finding in blocking if finding.kind not in DEPENDENCY_KINDS]
    if files:
        raise ConflictError(blocked_message(meta.name, blocking), findings)
    if blocking:
        raise DependencyError(blocked_message(meta.name, blocking), findings)
    return findings


def _remove_copied(root: Path, files: Sequence[Path], layout: GameLayout) -> None:
    """Undo a copy step so a rejected install leaves no files behind."""
    for file in files:
        (root / file).unlink(missing_ok=True)
    for directory in sorted({(root / file).parent for file in files}, reverse=True):
        prune_empty(root, directory, mod_prefixes(layout))


def read_meta(
    layout: ArchiveLayout,
    mods_dir: Path,
    name_override: str | None,
    version_override: str | None,
    archive: Path,
) -> ModMeta:
    """Return the metadata of the extracted archive, with the overrides applied.

    Raises:
        InvalidMetadataError: no metadata source exists, or a value would
            break the git trailers it is written to.
    """
    if layout.mod_type == ModType.CLIENT:
        meta = _dll_meta(layout, name_override, version_override, archive.stem)
    else:
        mod_dir = _server_mod_dir(layout, mods_dir)
        if mod_dir is not None:
            meta = read_package_json(mod_dir)
        else:
            # Real server mods regularly ship no package.json at all, just a
            # DLL and data folders; the DLL name or the overrides carry the
            # metadata then.
            meta = _dll_meta(layout, name_override, version_override, archive.stem)
            if not (name_override or meta.name):
                raise InvalidMetadataError(
                    f"{archive.name} carries no {PACKAGE_JSON_NAME} below "
                    f"{mods_dir.as_posix()} and no DLL to derive a name from; "
                    f"pass a name and a version"
                )
    resolved = replace(
        meta,
        name=name_override or meta.name,
        version=version_override or meta.version,
    )
    reject_multiline_metadata(resolved)
    return resolved


def reject_multiline_metadata(meta: ModMeta) -> None:
    """Reject metadata that git would refuse as a trailer value.

    Runs before anything is copied, so a value the commit would choke on
    cannot leave a half-installed game directory behind.
    """
    for field_name, value in (
        ("name", meta.name),
        ("version", meta.version),
        ("author", meta.author),
        ("sptVersion", meta.spt_range),
        ("guid", meta.guid),
    ):
        if "\n" in value or "\r" in value:
            raise GitError(
                f"mod metadata field {field_name!r} must not contain line breaks"
            )


def _server_mod_dir(layout: ArchiveLayout, mods_dir: Path) -> Path | None:
    """Return the mod folder carrying the ``package.json``, if the archive has one."""
    sources = [
        mapping.source for mapping in layout.mappings if mapping.target == mods_dir
    ]
    for source in sources:
        for child in sorted(source.iterdir()):
            if (child / PACKAGE_JSON_NAME).is_file():
                return child
    return None


def _dll_meta(
    layout: ArchiveLayout,
    name_override: str | None,
    version_override: str | None,
    archive_stem: str,
) -> ModMeta:
    dlls = [path for path in layout.files if path.suffix.lower() == ".dll"]
    if not dlls:
        return ModMeta(name=name_override or "", version=version_override or "")
    dll, derived_name = _preferred_dll(dlls, archive_stem)
    return meta_from_dll(dll, name_override or derived_name, version_override)


def _preferred_dll(dlls: Sequence[Path], archive_stem: str) -> tuple[Path, str]:
    """Pick the DLL naming the mod and the name it yields.

    Plugin folders ship helper assemblies next to the main DLL, so the archive
    file name decides: the longest DLL stem -- or dot-separated segment of one
    -- that occurs in it wins (``Tyfon-UIFixes-6.0.2.zip`` with
    ``Tyfon.UIFixes.dll`` and ``Tyfon.UIFixes.Net.dll`` yields ``UIFixes``).
    Without any match the first DLL and its full stem are used.
    """
    lowered_stem = archive_stem.lower()
    matches = [
        (candidate, dll)
        for dll in dlls
        for candidate in (dll.stem, *dll.stem.split("."))
        if candidate and candidate.lower() in lowered_stem
    ]
    if not matches:
        return dlls[0], dlls[0].stem
    candidate, dll = max(
        matches, key=lambda match: (len(match[0]), -len(match[1].stem))
    )
    return dll, candidate
