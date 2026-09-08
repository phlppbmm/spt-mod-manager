"""Listing, applying and undoing the published SPT releases.

A release is fetched from the release source, unpacked in the staging area and
written over the SPT program set of the game directory in one commit, so going
back to the version before is one step. Player profiles, mod settings and
installed mods are never touched: they lie outside the program set.
"""

import shutil
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from sptmod.core.archive import extract
from sptmod.core.errors import (
    ClientBuildMismatchError,
    GitError,
    SptReleaseUnavailableError,
)
from sptmod.core.forge.client import ForgeClient
from sptmod.core.forge.download import download_cached
from sptmod.core.game_dir import (
    GameLayout,
    detect_layout,
    read_client_build,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.models import CommitInfo, ModFile, Progress
from sptmod.core.repo import Repo
from sptmod.core.services.common import (
    GAME_FILES_SNAPSHOT,
    InstallStep,
    mod_prefixes,
    prefixed_progress,
    prune_empty,
    report_step,
)
from sptmod.core.services.state import ensure_installable
from sptmod.core.sptrelease.apply import (
    ReleaseFile,
    compare_versions,
    installed_program_files,
    release_files,
    required_client_build,
    same_version_line,
    version_key,
)
from sptmod.core.sptrelease.client import SptRelease, SptReleaseClient
from sptmod.core.workspace import game_lock, staging_dir

# The subject an SPT release commit carries; undoing a version change looks
# for it, so it stays free of trailers and of the mod vocabulary.
SPT_COMMIT_PREFIX: Final = "spt: "
CACHE_KEY_PREFIX: Final = "spt-"
# The subject git gives a commit that takes another one back.
UNDO_SUBJECT_PREFIX: Final = 'Revert "'
INSTALLER_URL: Final = "https://sp-mod.com/installer"


@dataclass(frozen=True)
class SptReleaseInfo:
    """One published SPT release, seen from the installed one.

    ``downloadable`` says whether the release still publishes an archive;
    ``installed`` marks the version the game directory holds and ``newer``
    every version above it.
    """

    version: str
    client_build: str
    published_at: datetime
    url: str
    downloadable: bool
    content_length: int | None
    installed: bool
    newer: bool


@dataclass(frozen=True)
class SptReleaseList:
    """What :func:`list_spt_releases` found for a game directory.

    ``current_version`` is the installed SPT version and ``client_build`` the
    Escape from Tarkov build of the game, both empty when they cannot be read.
    ``releases`` holds the versions of the installed series, newest first, and
    ``latest`` is the newest of them.
    """

    current_version: str = ""
    client_build: str = ""
    releases: list[SptReleaseInfo] = field(default_factory=list[SptReleaseInfo])
    latest: str = ""


@dataclass(frozen=True)
class SptApplyResult:
    """Outcome of a version change.

    ``commit`` and ``files`` stay empty when the game directory already held
    exactly that release and nothing had to be written.
    """

    previous_version: str
    version: str
    commit: str = ""
    files: list[ModFile] = field(default_factory=list[ModFile])


def list_spt_releases(
    path: Path, *, client: SptReleaseClient | None = None
) -> SptReleaseList:
    """List the published SPT releases the game directory at ``path`` can use.

    Only the releases of the installed series are listed, newest first,
    because changing between series is the installer's job. While the
    installed version cannot be read, every published release is listed.
    Nothing is downloaded or written.

    Args:
        path: The tracked game directory.
        client: Release source client; a default one is built when omitted.

    Returns:
        The installed version, the build of the game and the releases.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        SptReleaseUnavailableError: the release source cannot be read.
        GitError: git failed.
    """
    if client is not None:
        return _release_list(path, client)
    with SptReleaseClient() as owned:
        return _release_list(path, owned)


def apply_spt_release(
    path: Path,
    version: str,
    progress: Progress | None = None,
    *,
    client: SptReleaseClient | None = None,
) -> SptApplyResult:
    """Put SPT ``version`` into the game directory at ``path``.

    The release is downloaded, unpacked and written over the program set of
    the installation in a single commit ``spt: <version>``: files the release
    no longer ships are deleted, its own files are copied in, and the SPT
    version of the game directory is stored again afterwards. Player
    profiles, mod settings, the BepInEx configuration and every installed mod
    lie outside that set and stay untouched, so one step goes back to the
    version before.

    Before the release is written, the Escape from Tarkov build it needs is
    compared with the build of the installed game; they have to match, because
    only the official SPT installer can change the game itself. A release that
    names no build and ships no readable core configuration is applied without
    that comparison.

    A game directory that already holds exactly this release is left alone:
    the result then carries the version with an empty commit and no files.

    Args:
        path: The tracked game directory.
        version: The SPT version to put in place.
        progress: Optional callback for download and install progress.
        client: Release source client; a default one is built when omitted.

    Returns:
        The version before and after with the commit and its files.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        SptReleaseUnavailableError: the release source cannot be read, names
            no such version, publishes no download for it, or the version
            belongs to another SPT series than the installed one.
        ClientBuildMismatchError: the installed game has another Escape from
            Tarkov build than the release needs, or its build cannot be read.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        ArchiveError: the downloaded release is incomplete or unreadable.
        ForgeUnavailableError: the download cannot be reached.
        OperationInProgressError: another operation holds the game directory.
        GitError: git failed.
    """
    if client is not None:
        return _apply_release(path, version, progress, client)
    with SptReleaseClient() as owned:
        return _apply_release(path, version, progress, owned)


def revert_spt_update(path: Path) -> SptApplyResult:
    """Undo the last SPT version change in the game directory at ``path``.

    Takes back the youngest change of the SPT program set -- a version put in
    place by :func:`apply_spt_release` or the securing of files an update
    outside sptmod rewrote -- and stores the version the installation then
    reports. Installed mods keep their files, because such a change never
    contains any.

    Args:
        path: The tracked game directory.

    Returns:
        The version before and after with the commit and its files.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        OperationInProgressError: another operation holds the game directory.
        GitError: there is no SPT version change to undo, or git failed.
    """
    layout = detect_layout(path)
    repo = Repo(path)
    previous = resolve_spt_version(layout, repo) or ""
    with game_lock(path, "going back to the previous SPT version"):
        change = _last_version_change(repo)
        ensure_installable(repo)
        commit_hash = _revert_change(repo, change.hash)
        version = resolve_spt_version(layout, repo) or ""
        if version:
            store_spt_version(repo, version)
    return SptApplyResult(
        previous_version=previous,
        version=version,
        commit=commit_hash,
        files=repo.show_files(commit_hash),
    )


def _release_list(path: Path, client: SptReleaseClient) -> SptReleaseList:
    """Read the installed state and pair it with the published releases."""
    layout = detect_layout(path)
    current = resolve_spt_version(layout, Repo(path)) or ""
    releases = sorted(
        (
            _release_info(release, current)
            for release in client.releases()
            if not current or same_version_line(release.version, current)
        ),
        key=lambda info: version_key(info.version),
        reverse=True,
    )
    return SptReleaseList(
        current_version=current,
        client_build=read_client_build(layout.root / layout.client_exe),
        releases=releases,
        latest=releases[0].version if releases else "",
    )


def _release_info(release: SptRelease, current: str) -> SptReleaseInfo:
    """Describe one published release against the installed version."""
    order = compare_versions(release.version, current)
    return SptReleaseInfo(
        version=release.version,
        client_build=release.client_build,
        published_at=release.published_at,
        url=release.url,
        downloadable=bool(release.download_url),
        content_length=release.content_length,
        installed=bool(current) and order == 0,
        newer=order > 0,
    )


def _apply_release(
    path: Path, version: str, progress: Progress | None, client: SptReleaseClient
) -> SptApplyResult:
    """Download, check and write the release ``version`` into ``path``."""
    layout = detect_layout(path)
    repo = Repo(path)
    previous = resolve_spt_version(layout, repo) or ""
    ensure_installable(repo)
    release = _resolve_release(client, version, previous)
    reporter = prefixed_progress(progress, f"SPT {release.version}")

    with tempfile.TemporaryDirectory(prefix="sptmod-", dir=staging_dir()) as temp_name:
        unpacked = Path(temp_name) / "unpacked"
        unpacked.mkdir()
        report_step(reporter, InstallStep.CHECK, "downloading the release")
        archive = _download(release, reporter)
        report_step(reporter, InstallStep.EXTRACT, f"extracting {archive.name}")
        extract(archive, unpacked)

        report_step(reporter, InstallStep.CONFLICTS, "checking the game version")
        _check_client_build(layout, release, unpacked)
        files = release_files(unpacked, layout)

        with game_lock(path, f"changing SPT to {release.version}", reporter):
            ensure_installable(repo)
            commit_hash = _write_release(repo, layout, files, release.version, reporter)

    report_step(
        reporter, InstallStep.COMMIT, f"SPT {release.version} is in place", percent=100
    )
    return SptApplyResult(
        previous_version=previous,
        version=release.version,
        commit=commit_hash,
        files=repo.show_files(commit_hash) if commit_hash else [],
    )


def _resolve_release(
    client: SptReleaseClient, version: str, current: str
) -> SptRelease:
    """Return the published release ``version`` a game at ``current`` can use.

    Raises:
        SptReleaseUnavailableError: no such release is published, it belongs
            to another SPT series, or it publishes no download.
    """
    release = next(
        (
            entry
            for entry in client.releases()
            if compare_versions(entry.version, version) == 0
        ),
        None,
    )
    if release is None:
        raise SptReleaseUnavailableError(f"There is no published SPT {version}.")
    if current and not same_version_line(release.version, current):
        raise SptReleaseUnavailableError(
            f"SPT {release.version} is not part of the same SPT series as your "
            f"installed SPT {current}. Changing between series needs the "
            f"official SPT installer from {INSTALLER_URL}."
        )
    if not release.download_url:
        raise SptReleaseUnavailableError(
            f"SPT {release.version} publishes no download. Install it with the "
            f"official SPT installer from {INSTALLER_URL}."
        )
    return release


def _download(release: SptRelease, progress: Progress | None) -> Path:
    """Fetch the archive of ``release``, reusing an earlier download."""
    with ForgeClient() as client:
        return download_cached(
            client,
            release.download_url,
            f"{CACHE_KEY_PREFIX}{release.version}",
            progress=progress,
            expected_length=release.content_length,
        )


def _check_client_build(
    layout: GameLayout, release: SptRelease, unpacked: Path
) -> None:
    """Compare the build ``release`` needs with the build of the installed game.

    Raises:
        ClientBuildMismatchError: the builds differ, or the installed one
            cannot be read.
    """
    required = required_client_build(release, unpacked)
    if not required:
        return
    installed = read_client_build(layout.root / layout.client_exe)
    if installed == required:
        return
    found = (
        f"your game is build {installed}"
        if installed
        else "the build of your game could not be read"
    )
    raise ClientBuildMismatchError(
        f"SPT {release.version} needs Escape from Tarkov build {required}, but "
        f"{found}. Change the game version with the official SPT installer from "
        f"{INSTALLER_URL} first, then try again."
    )


def _write_release(
    repo: Repo,
    layout: GameLayout,
    files: Sequence[ReleaseFile],
    version: str,
    progress: Progress | None,
) -> str:
    """Replace the program set of ``layout`` with ``files`` in one commit.

    Returns the commit, or ``""`` when the release was already in place and
    nothing changed.
    """
    root = layout.root
    targets = {entry.target for entry in files}
    stale = [
        installed
        for installed in installed_program_files(layout)
        if installed not in targets
    ]
    report_step(progress, InstallStep.COPY, f"writing {len(files)} files")
    for removed in stale:
        (root / removed).unlink(missing_ok=True)
    for entry in files:
        destination = root / entry.target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.source, destination)
    for directory in sorted(
        {(root / removed).parent for removed in stale}, reverse=True
    ):
        prune_empty(root, directory, mod_prefixes(layout))

    if repo.is_clean():
        return ""
    report_step(progress, InstallStep.COMMIT, f"saving SPT {version}")
    commit_hash = repo.commit(
        f"{SPT_COMMIT_PREFIX}{version}", paths=[*stale, *sorted(targets)]
    )
    store_spt_version(repo, resolve_spt_version(layout, repo) or version)
    return commit_hash


def _last_version_change(repo: Repo) -> CommitInfo:
    """Return the youngest change of the SPT program set still in place.

    A change a younger commit already took back is passed over, so undoing
    twice reaches the change before instead of the same one again.

    Raises:
        GitError: no such change is left to undo.
    """
    undone: Counter[str] = Counter()
    for commit in repo.log():
        taken_back = _taken_back_subject(commit.subject)
        if taken_back is not None:
            undone[taken_back] += 1
            continue
        if not _is_version_change(commit.subject):
            continue
        if undone[commit.subject]:
            undone[commit.subject] -= 1
            continue
        return commit
    raise GitError("There is no SPT version change to undo.")


def _taken_back_subject(subject: str) -> str | None:
    """Return the subject ``subject`` takes back, or ``None`` if it takes none."""
    if subject.startswith(UNDO_SUBJECT_PREFIX) and subject.endswith('"'):
        return subject[len(UNDO_SUBJECT_PREFIX) : -1]
    return None


def _revert_change(repo: Repo, commit_hash: str) -> str:
    """Take back ``commit_hash``, saying in plain words when that fails.

    Raises:
        GitError: the change could not be taken back.
    """
    try:
        return repo.revert(commit_hash)
    except GitError as error:
        detail = (
            "these files have changed since then: "
            + ", ".join(path.as_posix() for path in error.conflicting_paths)
            if error.conflicting_paths
            else "the game folder no longer matches that state"
        )
        raise GitError(
            f"Going back to the previous SPT version did not work: {detail}",
            error.conflicting_paths,
        ) from error


def _is_version_change(subject: str) -> bool:
    """Return whether ``subject`` belongs to a change of the SPT program set."""
    return subject.startswith(SPT_COMMIT_PREFIX) or subject == GAME_FILES_SNAPSHOT
