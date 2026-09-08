"""Behaviour of ``apply_spt_release``: the version change itself.

Covers the auto-snapshot, the download and its cache, the extraction, the
single ``spt:`` commit, the re-resolved SPT version, deleted stale program
files, untouched configs and mods, excluded archive members, the progress
reports, a handed release client, an installation of unknown version, the 4.0
``SPT/`` layout and the waiting for the game lock.
"""

import json
import threading
import time
from pathlib import Path

from conftest import clean_repo_at
from fixture_types import InstallCommit, MakeArchive, MakeSptRelease
from fixtures_spt import (
    BUILD,
    INSTALLED,
    NEW,
    NEWEST,
    OTHER_SERIES,
    SptReleaseSource,
    offer,
    release_subjects,
    spt_release_members,
    unversioned_game_dir,
    write_client_exe,
)
from sptmod.core import services
from sptmod.core.game_dir import SPT_VERSION_CONFIG_KEY
from sptmod.core.models import ModFile, Progress
from sptmod.core.repo import Repo
from sptmod.core.sptrelease.client import SptReleaseClient
from sptmod.core.workspace import game_lock

# Members a release ships that belong to the player, to a mod or to the
# server's own runtime data; none of them may reach the game directory.
RELEASE_MEMBERS_KEPT_OUT = (
    "BepInEx/config/BepInEx.cfg",
    "SPT_Runtime/user/mods/x/package.json",
    "SPT_Runtime/SPT_Data/cache/y",
    "SPT_Runtime/SPT_Data/logs/z.log",
)

PROFILE = "SPT_Runtime/user/profiles/abc.json"


def _progress() -> tuple[list[tuple[str, int, str]], Progress]:
    """Return a list of reported ``(step, percent, message)`` and the callback filling it."""
    events: list[tuple[str, int, str]] = []

    def _capture(step: str, percent: int, message: str) -> None:
        events.append((step, percent, message))

    return events, _capture


# --- apply_spt_release, the version change itself ------------------------


def test_apply_writes_the_release_over_the_installed_program_set(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() puts the files of the release into the game directory.

    Every program member of the archive is on disk with the content of the
    new release afterwards, and the result names the version before and after.
    """
    offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(spt_game_dir, NEW)

    assert (result.previous_version, result.version) == (INSTALLED, NEW)
    assert (spt_game_dir / "SPT_Runtime/SPT_Data/database/x.json").read_text(
        encoding="utf-8"
    ) == json.dumps({"release": NEW})
    assert (spt_game_dir / "winhttp.dll").read_text(encoding="utf-8") == (
        f"doorstop of {NEW}"
    )
    assert services.status(spt_game_dir).spt_version == NEW


def test_apply_records_the_release_in_one_commit_without_trailers(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() writes exactly one commit ``spt: <version>``.

    The commit carries no trailers at all, because the undo of a version
    change looks for the subject and the mod vocabulary must stay out of it.
    """
    offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(spt_game_dir, NEW)

    commits = services.history(spt_game_dir)
    assert [commit.subject for commit in commits] == [
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert commits[0].hash == result.commit
    assert commits[0].trailers == {}


def test_apply_reports_the_files_it_wrote_and_deleted(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() returns the files of its commit.

    The two files the installation already had are modified, the seven new
    ones are added, and the program file the release no longer ships is
    deleted.
    """
    stale = spt_game_dir / "SPT_Runtime/SPT_Data/legacy/old.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    Repo(spt_game_dir).commit("chore: legacy data", paths=[Path("SPT_Runtime/SPT_Data/legacy/old.json")])
    offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(spt_game_dir, NEW)

    assert result.files == [
        ModFile(path=Path("BepInEx/core/BepInEx.dll"), change="added"),
        ModFile(path=Path("BepInEx/patchers/spt-prepatch.dll"), change="added"),
        ModFile(path=Path("BepInEx/plugins/spt/spt-core.dll"), change="added"),
        ModFile(path=Path("LICENSE-BepInEx.txt"), change="added"),
        ModFile(path=Path("SPT_Runtime/SPT.Server.exe"), change="modified"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/configs/core.json"), change="modified"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/database/x.json"), change="added"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/legacy/old.json"), change="deleted"),
        ModFile(path=Path("doorstop_config.ini"), change="added"),
        ModFile(path=Path("winhttp.dll"), change="added"),
    ]


def test_apply_deletes_program_files_the_release_no_longer_ships(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() removes stale program files and their empty directory.

    A file of the old release that the new one does not bring is gone
    afterwards, and the directory it was alone in does not stay behind empty.
    """
    stale = spt_game_dir / "SPT_Runtime/SPT_Data/legacy/old.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    Repo(spt_game_dir).commit("chore: legacy data", paths=[Path("SPT_Runtime/SPT_Data/legacy/old.json")])
    offer(spt_release_source, make_spt_release, NEW)

    services.apply_spt_release(spt_game_dir, NEW)

    assert not stale.exists()
    assert not stale.parent.exists()


def test_apply_leaves_configs_mods_and_player_content_untouched(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: InstallCommit,
) -> None:
    """apply_spt_release() writes nothing outside the SPT program set.

    The BepInEx configuration, an installed mod and the player profile keep
    their content, and the members of the archive that belong to none of them
    never reach the game directory.
    """
    repo = Repo(spt_game_dir)
    config = spt_game_dir / "BepInEx/config/BepInEx.cfg"
    config.write_text("[Logging]\nmine\n", encoding="utf-8")
    repo.commit("chore: my config", paths=[Path("BepInEx/config/BepInEx.cfg")])
    install_commit(
        repo,
        "ModA",
        files={
            "BepInEx/plugins/ModA.dll": "mod content",
            "SPT_Runtime/user/mods/ModA/package.json": '{"name": "ModA"}',
        },
    )
    offer(spt_release_source, make_spt_release, NEW)

    services.apply_spt_release(spt_game_dir, NEW)

    assert config.read_text(encoding="utf-8") == "[Logging]\nmine\n"
    assert (spt_game_dir / "BepInEx/plugins/ModA.dll").read_text(
        encoding="utf-8"
    ) == "mod content"
    assert (spt_game_dir / "SPT_Runtime/user/mods/ModA/package.json").exists()
    assert [
        member
        for member in RELEASE_MEMBERS_KEPT_OUT
        if (spt_game_dir / member).exists()
    ] == ["BepInEx/config/BepInEx.cfg"]
    assert [mod.name for mod in services.list_mods(spt_game_dir)] == ["ModA"]


def test_apply_stores_the_version_the_installation_now_reports(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() stores the version read from the new server executable.

    The version is resolved again after the files are in place, so the stored
    one describes the installation and not merely what was asked for.
    """
    offer(spt_release_source, make_spt_release, NEW)

    services.apply_spt_release(spt_game_dir, NEW)

    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == NEW


def test_apply_stores_the_asked_version_when_the_release_reports_none(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_archive: MakeArchive,
) -> None:
    """apply_spt_release() falls back to the version it was asked for.

    The server executable of this release carries no version resource, so the
    installation reports none; the version of the release is stored instead.
    """
    members = spt_release_members(NEW, BUILD) | {
        "SPT_Runtime/SPT.Server.exe": b"no version resource in here"
    }
    spt_release_source.offer(
        make_archive(members, "zip"), version=NEW, client_build=BUILD
    )

    result = services.apply_spt_release(spt_game_dir, NEW)

    assert result.version == NEW
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == NEW


def test_apply_secures_a_changed_profile_before_it_changes_the_version(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() snapshots the profile a played raid changed.

    The snapshot is its own commit below the version change, so the state of
    the game folder before the update can be reached again.
    """
    profile = spt_game_dir / PROFILE
    profile.write_text(json.dumps({"Inventory": {"items": [1]}}), encoding="utf-8")
    offer(spt_release_source, make_spt_release, NEW)

    services.apply_spt_release(spt_game_dir, NEW)

    assert release_subjects(spt_game_dir) == [
        f"spt: {NEW}",
        "snapshot: automatic snapshot before changing mods",
        "chore: clean SPT 4.1.0",
    ]
    assert Repo(spt_game_dir).show_file_content(
        services.history(spt_game_dir)[1].hash, Path(PROFILE)
    ) == json.dumps({"Inventory": {"items": [1]}})


def test_apply_reports_its_progress_from_download_to_commit(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() reports every step of the change, named after the release.

    The steps arrive in the order they happen with rising percentages, and
    every message names the SPT version being put in place.
    """
    offer(spt_release_source, make_spt_release, NEW)
    events, progress = _progress()

    services.apply_spt_release(spt_game_dir, NEW, progress)

    assert [(step, percent) for step, percent, _ in events] == [
        ("check", 0),
        ("download", 100),
        ("extract", 10),
        ("conflicts", 60),
        ("copy", 75),
        ("commit", 90),
        ("commit", 100),
    ]
    assert [message for _, _, message in events if not message.startswith(f"SPT {NEW}: ")] == []


def test_reapplying_the_installed_release_changes_nothing_and_reuses_the_download(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() of the release already in place writes no commit.

    The second run finds every file as the release has it, so there is
    nothing to commit, and the archive comes out of the download cache
    instead of over the network a second time.
    """
    route = offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)

    result = services.apply_spt_release(spt_game_dir, NEW)

    assert (result.previous_version, result.version) == (NEW, NEW)
    assert result.commit == ""
    assert not result.files
    assert release_subjects(spt_game_dir) == [f"spt: {NEW}", "chore: clean SPT 4.1.0"]
    assert route.call_count == 1


def test_apply_of_a_further_release_downloads_it_and_commits_again(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() fetches each version once and records each change.

    The cached archive of the first version is no answer for the second one,
    and both changes stay in the history as their own commit.
    """
    first = offer(spt_release_source, make_spt_release, NEW)
    second = offer(spt_release_source, make_spt_release, NEWEST)
    services.apply_spt_release(spt_game_dir, NEW)

    result = services.apply_spt_release(spt_game_dir, NEWEST)

    assert (result.previous_version, result.version) == (NEW, NEWEST)
    assert (first.call_count, second.call_count) == (1, 1)
    assert release_subjects(spt_game_dir) == [
        f"spt: {NEWEST}",
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert (spt_game_dir / "winhttp.dll").read_text(encoding="utf-8") == (
        f"doorstop of {NEWEST}"
    )


def test_apply_uses_a_release_client_it_is_handed(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() resolves the release through the client passed to it."""
    offer(spt_release_source, make_spt_release, NEW)

    with SptReleaseClient() as client:
        result = services.apply_spt_release(spt_game_dir, NEW, client=client)

    assert result.version == NEW
    assert result.commit != ""


def test_apply_ignores_the_series_when_the_installed_version_is_unknown(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() applies any series while it knows no installed version.

    Without a version to compare against there is no series to stay in, so
    the release of another series is written and reported with an empty
    version before.
    """
    path = unversioned_game_dir(fake_game_dir_runtime)
    offer(spt_release_source, make_spt_release, OTHER_SERIES)

    result = services.apply_spt_release(path, OTHER_SERIES)

    assert (result.previous_version, result.version) == ("", OTHER_SERIES)
    assert (path / "SPT_Runtime/SPT.Server.exe").exists()


def test_apply_writes_a_release_of_the_4_0_layout_into_its_runtime_directory(
    fake_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() takes ``SPT_Runtime`` members into the ``SPT`` directory.

    A 4.0 installation keeps its runtime directory in ``SPT/``; the release
    names ``SPT_Runtime`` regardless, and none of it may land under that name.
    """
    write_client_exe(fake_game_dir)
    clean_repo_at(fake_game_dir)
    offer(spt_release_source, make_spt_release, NEW)

    result = services.apply_spt_release(fake_game_dir, NEW)

    assert result.version == NEW
    assert (fake_game_dir / "SPT/SPT_Data/database/x.json").read_text(
        encoding="utf-8"
    ) == json.dumps({"release": NEW})
    assert not (fake_game_dir / "SPT_Runtime").exists()
    assert [entry.path.as_posix() for entry in result.files] == [
        "BepInEx/core/BepInEx.dll",
        "BepInEx/patchers/spt-prepatch.dll",
        "BepInEx/plugins/spt/spt-core.dll",
        "LICENSE-BepInEx.txt",
        "SPT/SPT.Server.exe",
        "SPT/SPT_Data/configs/core.json",
        "SPT/SPT_Data/database/x.json",
        "doorstop_config.ini",
        "winhttp.dll",
    ]


def test_apply_waits_for_an_operation_holding_the_game_directory(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() waits for the game lock and changes the version after it.

    Another operation holds the lock for a moment; the version change blocks
    until it is released and then completes normally.
    """
    offer(spt_release_source, make_spt_release, NEW)
    release_after = 0.3
    holder_ready = threading.Event()

    def hold() -> None:
        with game_lock(spt_game_dir, "installing OtherMod"):
            holder_ready.set()
            time.sleep(release_after)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_ready.wait(timeout=1.0)

    started = time.monotonic()
    result = services.apply_spt_release(spt_game_dir, NEW)
    elapsed = time.monotonic() - started
    holder.join(timeout=1.0)

    assert result.version == NEW
    assert result.commit != ""
    assert elapsed >= release_after * 0.5
