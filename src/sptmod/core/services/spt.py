"""Listing, applying and undoing the published SPT releases.

A release is fetched from the release source, unpacked in the staging area and
written over the SPT program set of the game directory in one commit, so going
back to the version before is one step. Player profiles, mod settings and
installed mods are never touched: they lie outside the program set.
"""

import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from sptmod.core.archive import extract
from sptmod.core.errors import (
    CatalogUnavailableError,
    ClientBuildMismatchError,
    GitError,
    SptModsIncompatibleError,
    SptReleaseUnavailableError,
    SptReleaseWriteError,
)
from sptmod.core.forge.catalog import Catalog, CatalogMod, is_compatible
from sptmod.core.forge.client import ForgeClient
from sptmod.core.forge.download import download_cached
from sptmod.core.game_dir import (
    GameLayout,
    detect_layout,
    read_client_build,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.models import CommitInfo, ModFile, ModInfo, Progress
from sptmod.core.repo import Repo
from sptmod.core.services.common import (
    GAME_FILES_SNAPSHOT,
    InstallStep,
    mod_prefixes,
    open_catalog,
    prefixed_progress,
    prune_empty,
    report_step,
)
from sptmod.core.services.state import ensure_installable, list_mods
from sptmod.core.sptrelease.apply import (
    ReleaseFile,
    compare_versions,
    installed_program_files,
    release_files,
    required_client_build,
    same_version_line,
    version_key,
)
from sptmod.core.sptrelease.cache import read_cached_releases, write_cached_releases
from sptmod.core.sptrelease.client import SptRelease, SptReleaseClient
from sptmod.core.workspace import game_lock, staging_dir

# The subject an SPT release commit carries; undoing a version change looks
# for it, so it stays free of trailers and of the mod vocabulary.
SPT_COMMIT_PREFIX: Final = "spt: "
CACHE_KEY_PREFIX: Final = "spt-"
# The subject git gives a commit that takes another one back.
UNDO_SUBJECT_PREFIX: Final = 'Revert "'
INSTALLER_URL: Final = "https://sp-mod.com/installer"
# What the mod check reports instead of an all-clear when the local mod list
# cannot answer for a single mod.
CATALOG_MISSING_NOTICE: Final = (
    "The mod list has not been downloaded yet, so your installed mods could "
    "not be checked. Download it first to have them checked."
)
CATALOG_UNREADABLE_NOTICE: Final = (
    "The mod list on this computer could not be opened, so your installed "
    "mods could not be checked."
)
# The date and time a stored release listing is said to be from, written the
# way every other date of this program is written.
STORED_LIST_TIME_FORMAT: Final = "%Y-%m-%d %H:%M"


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
    Escape from Tarkov build of the game, both read from the game directory
    and empty only when they cannot be read there. ``releases`` holds the
    versions of the installed series, newest first, and ``latest`` is the
    newest of them. ``source_ok`` is false when the release source could not
    be read on this call, and ``notice`` then says so in plain words -- naming
    that the listing shown is the one downloaded earlier, or that there is
    none to show.
    """

    current_version: str = ""
    client_build: str = ""
    releases: list[SptReleaseInfo] = field(default_factory=list[SptReleaseInfo])
    latest: str = ""
    source_ok: bool = True
    notice: str = ""


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


@dataclass(frozen=True)
class SptCompatibilityReport:
    """How the installed mods stand to an SPT version the game could change to.

    ``compatible`` names the mods the mod list has a version of for
    ``spt_version`` and ``incompatible`` the ones it has none for -- those are
    what a version change is refused over. ``skipped`` names the mods the list
    cannot judge because it knows nothing about them; on their own they refuse
    nothing. ``checked`` is false when the mod list could not answer at all,
    and ``notice`` then says why in plain words.
    """

    spt_version: str
    compatible: list[str] = field(default_factory=list[str])
    incompatible: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    checked: bool = True
    notice: str = ""


def list_spt_releases(
    path: Path, *, refresh: bool = False, client: SptReleaseClient | None = None
) -> SptReleaseList:
    """List the published SPT releases the game directory at ``path`` can use.

    Only the releases of the installed series are listed, newest first,
    because changing between series is the installer's job. While the
    installed version cannot be read, every published release is listed.
    Nothing is downloaded or written.

    The listing is read from the stored copy and the release source is only
    asked when there is none, or when ``refresh`` says to fetch a fresh one:
    the source allows sixty anonymous requests an hour, and a listing costs
    one per source. A source that cannot be read never fails this call -- the
    installed version and the build of the game are read from the game
    directory and are reported either way, together with the stored listing
    when there is one, ``source_ok`` false and a notice saying what happened.

    Args:
        path: The tracked game directory.
        refresh: Fetch a fresh listing instead of serving the stored one.
        client: Release source client; a default one is built when omitted.

    Returns:
        The installed version, the build of the game, the releases and how
        the release source answered.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        GitError: git failed.
    """
    if client is not None:
        return _release_list(path, client, refresh)
    with SptReleaseClient() as owned:
        return _release_list(path, owned, refresh)


def check_spt_compatibility(
    path: Path, version: str, *, db_path: Path | None = None
) -> SptCompatibilityReport:
    """Judge the mods installed in ``path`` against the SPT version ``version``.

    Every active mod is matched to its entry in the local mod list -- by its
    Forge ID first, by its name otherwise -- and counts as compatible when
    that entry holds at least one version declared for ``version``. A mod the
    list holds no version of at all cannot be judged and is reported as
    skipped rather than as incompatible, so an entry nobody has downloaded yet
    never stands in the way of a version change. Nothing is downloaded or
    written here.

    A mod list that was never downloaded, and one that cannot be opened, leave
    the report marked as not checked with a notice saying so, instead of an
    empty result that would read as a clean bill of health.

    Args:
        path: The tracked game directory.
        version: The SPT version the installed mods are judged against.
        db_path: Catalog database path; defaults to :func:`default_db_path`.

    Returns:
        The compatible, the incompatible and the unjudgeable mods, or a report
        marked as not checked when the mod list could not answer.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        GitError: git failed.
    """
    try:
        with open_catalog(db_path) as catalog:
            if catalog.last_synced_at() is None:
                return SptCompatibilityReport(
                    spt_version=version,
                    checked=False,
                    notice=CATALOG_MISSING_NOTICE,
                )
            return _judge_mods(catalog, list_mods(path), version)
    except CatalogUnavailableError:
        return SptCompatibilityReport(
            spt_version=version,
            checked=False,
            notice=CATALOG_UNREADABLE_NOTICE,
        )


def apply_spt_release(
    path: Path,
    version: str,
    progress: Progress | None = None,
    *,
    db_path: Path | None = None,
    allow_incompatible_mods: bool = False,
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

    Before anything is downloaded, the installed mods are judged against the
    wanted version: when the local mod list holds versions of a mod but none
    for that version, the change is refused and those mods are named, unless
    ``allow_incompatible_mods`` says to go ahead anyway. Mods the list cannot
    judge refuse nothing.

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
        db_path: Catalog database path; defaults to :func:`default_db_path`.
        allow_incompatible_mods: Change the version even when installed mods
            have no version for it.
        client: Release source client; a default one is built when omitted.

    Returns:
        The version before and after with the commit and its files.

    Raises:
        NotAGameDirError: ``path`` is no SPT game directory.
        SptReleaseUnavailableError: the release source cannot be read, names
            no such version, publishes no download for it, or the version
            belongs to another SPT series than the installed one.
        SptModsIncompatibleError: installed mods have no version for the
            wanted SPT version and ``allow_incompatible_mods`` is not set.
        ClientBuildMismatchError: the installed game has another Escape from
            Tarkov build than the release needs, or its build cannot be read.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        ArchiveError: the downloaded release is incomplete or unreadable.
        SptReleaseWriteError: a file of the release could not be written into
            the game directory.
        ForgeUnavailableError: the download cannot be reached.
        OperationInProgressError: another operation holds the game directory.
        GitError: git failed.
    """
    if client is not None:
        return _apply_release(
            path,
            version,
            progress,
            client,
            db_path=db_path,
            allow_incompatible_mods=allow_incompatible_mods,
        )
    with SptReleaseClient() as owned:
        return _apply_release(
            path,
            version,
            progress,
            owned,
            db_path=db_path,
            allow_incompatible_mods=allow_incompatible_mods,
        )


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


def _release_list(
    path: Path, client: SptReleaseClient, refresh: bool
) -> SptReleaseList:
    """Read the installed state and pair it with the published releases."""
    layout = detect_layout(path)
    current = resolve_spt_version(layout, Repo(path)) or ""
    published = _published_releases(client, refresh)
    releases = sorted(
        (
            _release_info(release, current)
            for release in published.releases
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
        source_ok=not published.notice,
        notice=published.notice,
    )


@dataclass(frozen=True)
class _PublishedReleases:
    """The releases a call got hold of and how it got them.

    ``notice`` is empty while nothing went wrong; ``asked`` says whether the
    release source itself answered, which is what tells a listing that is
    merely old from one that is as current as it can be.
    """

    releases: Sequence[SptRelease]
    notice: str
    asked: bool


def _published_releases(client: SptReleaseClient, refresh: bool) -> _PublishedReleases:
    """Return the published releases and, when the source failed, what to say about it.

    The stored copy answers unless a fresh listing was asked for or there is
    none. A source that refuses leaves the stored copy standing, because a
    listing downloaded earlier is worth more than an empty one.
    """
    stored = read_cached_releases(client.base_urls)
    if stored is not None and not refresh:
        return _PublishedReleases(stored.releases, "", asked=False)
    try:
        fetched = client.releases()
    except SptReleaseUnavailableError as error:
        if stored is None:
            return _PublishedReleases((), str(error), asked=True)
        notice = f"{error} {_stored_list_notice(stored.fetched_at)}"
        return _PublishedReleases(stored.releases, notice, asked=True)
    write_cached_releases(client.base_urls, fetched)
    return _PublishedReleases(fetched, "", asked=True)


def _stored_list_notice(fetched_at: datetime) -> str:
    """Say that the listing on show is the one downloaded at ``fetched_at``."""
    return (
        "The SPT versions shown are the ones downloaded on "
        f"{fetched_at.strftime(STORED_LIST_TIME_FORMAT)}."
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
    path: Path,
    version: str,
    progress: Progress | None,
    client: SptReleaseClient,
    *,
    db_path: Path | None,
    allow_incompatible_mods: bool,
) -> SptApplyResult:
    """Download, check and write the release ``version`` into ``path``."""
    layout = detect_layout(path)
    repo = Repo(path)
    previous = resolve_spt_version(layout, repo) or ""
    ensure_installable(repo)
    release = _resolve_release(client, version, previous)
    if not allow_incompatible_mods:
        _refuse_incompatible_mods(path, release.version, db_path)
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


def _refuse_incompatible_mods(path: Path, version: str, db_path: Path | None) -> None:
    """Stop the change to ``version`` while installed mods have no version for it.

    Raises:
        SptModsIncompatibleError: the mod list holds versions of those mods,
            but none of them for ``version``.
    """
    report = check_spt_compatibility(path, version, db_path=db_path)
    if report.incompatible:
        raise SptModsIncompatibleError(version, report.incompatible)


def _judge_mods(
    catalog: Catalog, installed: Sequence[ModInfo], version: str
) -> SptCompatibilityReport:
    """Sort ``installed`` into the verdicts the mod list gives for ``version``."""
    by_name: dict[str, CatalogMod] = {}
    for cached in catalog.search(""):
        by_name.setdefault(cached.name.casefold(), cached)
    compatible: list[str] = []
    incompatible: list[str] = []
    skipped: list[str] = []
    for mod in installed:
        known = _catalog_mod(catalog, by_name, mod)
        versions = [] if known is None else catalog.get_versions(known.id)
        if not versions:
            skipped.append(mod.name)
        elif any(
            is_compatible(entry.spt_version_constraint, version) for entry in versions
        ):
            compatible.append(mod.name)
        else:
            incompatible.append(mod.name)
    return SptCompatibilityReport(
        spt_version=version,
        compatible=compatible,
        incompatible=incompatible,
        skipped=skipped,
    )


def _catalog_mod(
    catalog: Catalog, by_name: Mapping[str, CatalogMod], mod: ModInfo
) -> CatalogMod | None:
    """Return the mod-list entry of ``mod``, matched by Forge ID before name."""
    if mod.forge_id.isdigit():
        known = catalog.get_mod(int(mod.forge_id))
        if known is not None:
            return known
    return by_name.get(mod.name.casefold())


def _resolve_release(
    client: SptReleaseClient, version: str, current: str
) -> SptRelease:
    """Return the published release ``version`` a game at ``current`` can use.

    A version the stored listing does not know is looked for at the source
    once before it is called unpublished, so a release that appeared after
    the listing was downloaded is installable without anyone having to ask
    for a refresh first.

    Raises:
        SptReleaseUnavailableError: no such release is published, it belongs
            to another SPT series, or it publishes no download.
    """
    published = _published_releases(client, refresh=False)
    release = _named_release(published.releases, version)
    if release is None and not published.asked:
        published = _published_releases(client, refresh=True)
        release = _named_release(published.releases, version)
    if release is None:
        raise SptReleaseUnavailableError(
            published.notice or f"There is no published SPT {version}."
        )
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


def _named_release(
    published: Sequence[SptRelease], version: str
) -> SptRelease | None:
    """Return the release ``version`` names among ``published``, or None for none."""
    return next(
        (
            entry
            for entry in published
            if compare_versions(entry.version, version) == 0
        ),
        None,
    )


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
    _write_files(root, stale, files)
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


def _write_files(
    root: Path, stale: Sequence[Path], files: Sequence[ReleaseFile]
) -> None:
    """Delete the ``stale`` program files below ``root`` and copy ``files`` in.

    Raises:
        SptReleaseWriteError: a file could not be deleted or written; the
            message names the file it stopped at and what the system said.
    """
    reached = Path()
    try:
        for removed in stale:
            reached = removed
            (root / removed).unlink(missing_ok=True)
        for entry in files:
            reached = entry.target
            destination = root / entry.target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.source, destination)
    except OSError as error:
        raise SptReleaseWriteError(
            f"The game files could not be written: {error.strerror}. The "
            f"update stopped at {reached.as_posix()}; your previous files are "
            f"one snapshot back."
        ) from error


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
