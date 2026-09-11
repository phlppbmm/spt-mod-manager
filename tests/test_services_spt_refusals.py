"""Behaviour of ``apply_spt_release`` when it refuses, and of a copy that fails mid-write.

Covers the refusals -- an unknown version, a release list that cannot be
read at all, a version the stored listing does not know yet, a release
without a download, a release of another series, a build mismatch, an
unreadable build, a running game, an unmanaged file, a download that does not arrive whole and a
directory that is no game directory -- and the state a copy failing mid-write
leaves behind for the next change.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from fixture_types import InstallCommit, MakeArchive, MakeSptRelease
from fixtures_spt import (
    BUILD,
    INSTALLED,
    NEW,
    NEWEST,
    OTHER_BUILD,
    OTHER_SERIES,
    SPT_OWN_PLUGIN,
    SptReleaseSource,
    offer,
    release_subjects,
    spt_release_members,
)
from sptmod.core import game_dir, services
from sptmod.core.errors import (
    ArchiveError,
    ClientBuildMismatchError,
    DirtyTreeError,
    GameRunningError,
    NotAGameDirError,
    SptReleaseUnavailableError,
    SptReleaseWriteError,
)
from sptmod.core.repo import Repo

# A release member a custom archive drops to make the installed copy stale.
STALE_MEMBER = "SPT_Runtime/SPT_Data/database/x.json"

# The core configuration of a release archive of the 4.1 layout, which
# names the Escape from Tarkov build the release needs.
RUNTIME_CORE_CONFIG = "SPT_Runtime/SPT_Data/configs/core.json"


# --- apply_spt_release, the refusals -------------------------------------


def test_apply_refuses_a_version_the_source_does_not_publish(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() raises SptReleaseUnavailableError for an unknown version."""
    offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(
        SptReleaseUnavailableError, match="There is no published SPT 4.1.9."
    ):
        services.apply_spt_release(spt_game_dir, "4.1.9")

    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_refuses_the_change_while_no_release_list_can_be_read(
    spt_game_dir: Path, spt_release_source: SptReleaseSource
) -> None:
    """apply_spt_release() names the unreadable source instead of the missing version.

    The source fails and no listing was ever downloaded, so nothing can say
    whether the wanted version exists; the change is refused with what went
    wrong, because an empty list must never read as "there is no such
    release" and lead to a wrong decision.
    """
    spt_release_source.unavailable(503)

    with pytest.raises(SptReleaseUnavailableError, match="not available right now"):
        services.apply_spt_release(spt_game_dir, NEW)

    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_looks_at_the_source_for_a_version_the_stored_listing_lacks(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() finds a release published after the stored listing was downloaded.

    The listing was stored while only 4.1.1 existed and 4.1.2 appeared
    afterwards; the change to 4.1.2 goes through without anyone asking for a
    refresh first, because a version missing from a listing of yesterday says
    nothing about what is published today.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.list_spt_releases(spt_game_dir)
    offer(spt_release_source, make_spt_release, NEWEST)

    result = services.apply_spt_release(spt_game_dir, NEWEST)

    assert result.version == NEWEST
    assert release_subjects(spt_game_dir)[0] == f"spt: {NEWEST}"


def test_apply_names_the_failed_source_when_the_stored_listing_lacks_the_version(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() reports the unreadable source, not a missing release.

    The stored listing knows only 4.1.1 and the source refuses the look-up
    for 4.1.2; the refusal names the source failure and the day the listing
    was downloaded, because "there is no published SPT 4.1.2" would be a
    statement nobody checked.
    """
    offer(spt_release_source, make_spt_release, NEW)
    services.list_spt_releases(spt_game_dir)
    spt_release_source.unavailable(503)

    with pytest.raises(SptReleaseUnavailableError, match="downloaded on") as exc_info:
        services.apply_spt_release(spt_game_dir, NEWEST)

    assert "not available right now" in str(exc_info.value)
    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_asks_the_source_once_for_a_version_nothing_publishes(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() does not read the source twice over a version it just fetched.

    Nothing was stored, so the first look-up already asked the source; the
    refusal comes from that answer instead of a second request, which is what
    keeps a mistyped version from costing two of the sixty requests an hour.
    """
    offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(SptReleaseUnavailableError, match="There is no published SPT"):
        services.apply_spt_release(spt_game_dir, "4.1.9")

    assert spt_release_source.list_calls == 1


def test_apply_refuses_a_release_without_a_download_and_names_the_installer(
    spt_game_dir: Path, spt_release_source: SptReleaseSource
) -> None:
    """apply_spt_release() sends the user to the official installer.

    A release whose archive was withdrawn cannot be fetched, and the message
    says where it can be had instead.
    """
    spt_release_source.announce(NEW)

    with pytest.raises(SptReleaseUnavailableError) as error:
        services.apply_spt_release(spt_game_dir, NEW)

    assert str(error.value) == (
        f"SPT {NEW} publishes no download. Install it with the official SPT "
        f"installer from https://sp-mod.com/installer."
    )


def test_apply_refuses_a_release_of_another_series(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() refuses to leave the installed SPT series.

    Only the official installer changes the series, and the message names
    both versions and where to get it.
    """
    offer(spt_release_source, make_spt_release, OTHER_SERIES)

    with pytest.raises(SptReleaseUnavailableError) as error:
        services.apply_spt_release(spt_game_dir, OTHER_SERIES)

    assert str(error.value) == (
        f"SPT {OTHER_SERIES} is not part of the same SPT series as your "
        f"installed SPT {INSTALLED}. Changing between series needs the "
        f"official SPT installer from https://sp-mod.com/installer."
    )


def test_apply_refuses_a_release_needing_another_game_build(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() raises ClientBuildMismatchError naming both builds.

    Only the official installer changes Escape from Tarkov itself, so a
    release for another build is refused before anything is written.
    """
    offer(spt_release_source, make_spt_release, NEW, client_build=OTHER_BUILD)

    with pytest.raises(ClientBuildMismatchError) as error:
        services.apply_spt_release(spt_game_dir, NEW)

    assert str(error.value) == (
        f"SPT {NEW} needs Escape from Tarkov build {OTHER_BUILD}, but your game "
        f"is build {BUILD}. Change the game version with the official SPT "
        f"installer from https://sp-mod.com/installer first, then try again."
    )
    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_takes_the_build_the_release_names_over_the_one_its_archive_names(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() believes the release entry, not the unpacked archive.

    The release title names another build than the core configuration inside
    its archive; the published title is what the refusal names, because that
    is what the source says the release needs.
    """
    spt_release_source.offer(
        make_spt_release(version=NEW, client_build=BUILD),
        version=NEW,
        client_build=OTHER_BUILD,
    )

    with pytest.raises(ClientBuildMismatchError) as error:
        services.apply_spt_release(spt_game_dir, NEW)

    assert f"needs Escape from Tarkov build {OTHER_BUILD}" in str(error.value)


@pytest.mark.parametrize(
    ("core_member", "core_json", "required_build"),
    [
        pytest.param(
            RUNTIME_CORE_CONFIG,
            '{"compatibleTarkovVersion": "0.16.9.40999"}',
            OTHER_BUILD,
            id="build-named",
        ),
        pytest.param(
            "SPT/SPT_Data/configs/core.json",
            '{"compatibleTarkovVersion": "0.16.9.40999"}',
            OTHER_BUILD,
            id="build-named-in-the-4-0-layout",
        ),
        pytest.param(
            RUNTIME_CORE_CONFIG,
            '{"compatibleTarkovVersion": "0.16.9.x"}',
            "",
            id="no-numeric-build",
        ),
        pytest.param(
            RUNTIME_CORE_CONFIG,
            '{"compatibleTarkovVersion": 40743}',
            "",
            id="build-not-a-string",
        ),
        pytest.param(
            RUNTIME_CORE_CONFIG, '{"projectName": "SPT"}', "", id="no-build-field"
        ),
        pytest.param(RUNTIME_CORE_CONFIG, "not json at all", "", id="unreadable-json"),
        pytest.param(RUNTIME_CORE_CONFIG, "[1, 2]", "", id="not-an-object"),
        pytest.param(
            RUNTIME_CORE_CONFIG, b"\xff\xfe\x00binary", "", id="undecodable-config"
        ),
        pytest.param(RUNTIME_CORE_CONFIG, None, "", id="no-config-at-all"),
    ],
)
def test_apply_reads_the_build_of_a_release_from_its_core_configuration(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_archive: MakeArchive,
    core_member: str,
    core_json: str | bytes | None,
    required_build: str,
) -> None:
    """apply_spt_release() falls back to ``compatibleTarkovVersion`` for the build.

    The release itself names no build, so the core configuration of its
    archive has to answer -- in the 4.1 ``SPT_Runtime`` directory or in the
    4.0 ``SPT`` one. Its trailing component is the build and the change is
    refused when the installed game has another one; a configuration that
    names no number, is no object, is no readable text or is missing
    altogether leaves the build unknown, and then there is nothing to compare
    and the change goes through.
    """
    members: dict[str, str | bytes] = {
        name: content
        for name, content in spt_release_members(NEW, BUILD).items()
        if name != RUNTIME_CORE_CONFIG
    }
    if core_json is not None:
        members[core_member] = core_json
    spt_release_source.offer(
        make_archive(members, "zip"), version=NEW, client_build=""
    )

    if not required_build:
        assert services.apply_spt_release(spt_game_dir, NEW).version == NEW
        return
    with pytest.raises(ClientBuildMismatchError) as error:
        services.apply_spt_release(spt_game_dir, NEW)

    assert str(error.value) == (
        f"SPT {NEW} needs Escape from Tarkov build {required_build}, but your "
        f"game is build {BUILD}. Change the game version with the official SPT "
        f"installer from https://sp-mod.com/installer first, then try again."
    )


def _remove_client_exe(exe: Path) -> None:
    """Take the game client out of the installation."""
    exe.unlink()


def _corrupt_client_exe(exe: Path) -> None:
    """Leave a game client behind that carries no version resource."""
    exe.write_bytes(b"not a PE image")


@pytest.mark.parametrize(
    "break_client",
    [_remove_client_exe, _corrupt_client_exe],
    ids=["client-missing", "client-unreadable"],
)
def test_apply_refuses_when_the_installed_game_build_cannot_be_read(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    break_client: Callable[[Path], None],
) -> None:
    """apply_spt_release() refuses while it cannot read the build of the game.

    An unreadable build is not a match; guessing would write a release the
    installed game cannot run.
    """
    break_client(spt_game_dir / "EscapeFromTarkov.exe")
    offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(ClientBuildMismatchError) as error:
        services.apply_spt_release(spt_game_dir, NEW)

    assert str(error.value) == (
        f"SPT {NEW} needs Escape from Tarkov build {BUILD}, but the build of "
        f"your game could not be read. Change the game version with the "
        f"official SPT installer from https://sp-mod.com/installer first, then try "
        f"again."
    )


def test_apply_skips_the_build_check_when_no_build_is_known_anywhere(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() writes a release that names no build at all.

    Neither the release title nor the core configuration of the archive names
    a build, so there is nothing to compare the installed one against and the
    change goes through.
    """
    offer(spt_release_source, make_spt_release, NEW, client_build="")

    result = services.apply_spt_release(spt_game_dir, NEW)

    assert result.version == NEW
    assert result.commit != ""


def test_apply_refuses_while_the_game_is_running(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply_spt_release() raises GameRunningError and writes nothing.

    Replacing the program set under a running server or client would break
    the running game.
    """
    offer(spt_release_source, make_spt_release, NEW)
    monkeypatch.setattr(
        game_dir, "running_game_processes", lambda: ["EscapeFromTarkov.exe"]
    )

    with pytest.raises(GameRunningError, match="close it first"):
        services.apply_spt_release(spt_game_dir, NEW)

    assert not (spt_game_dir / "winhttp.dll").exists()


def test_apply_refuses_a_game_folder_holding_files_it_does_not_manage(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() raises DirtyTreeError for a manually installed mod.

    Such files cannot be secured in a snapshot, so the version change would
    not be undoable; the message points at adopting them.
    """
    manual = spt_game_dir / "BepInEx/plugins/ManualMod/manual.dll"
    manual.parent.mkdir(parents=True)
    manual.write_text("manual", encoding="utf-8")
    offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(DirtyTreeError, match="BepInEx/plugins/ManualMod"):
        services.apply_spt_release(spt_game_dir, NEW)

    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_refuses_a_download_that_does_not_arrive_whole(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() raises ArchiveError when the archive size differs.

    The release announces more bytes than arrive, so the download is not the
    published archive and nothing of it is written.
    """
    offer(spt_release_source, make_spt_release, NEW, size=999_999)

    with pytest.raises(ArchiveError, match="expected 999999"):
        services.apply_spt_release(spt_game_dir, NEW)

    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_rejects_a_directory_that_is_no_game_directory(tmp_path: Path) -> None:
    """apply_spt_release() raises NotAGameDirError before it reads any release."""
    with pytest.raises(NotAGameDirError, match="not an SPT game directory"):
        services.apply_spt_release(tmp_path, NEW)



def test_a_release_that_could_not_be_written_is_finished_by_the_next_change(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    make_archive: MakeArchive,
    install_commit: InstallCommit,
) -> None:
    """A version change that breaks mid-copy leaves a state the next one secures.

    The first release is in place; the second drops one file and cannot write
    the last one, because the installed copy is read-only. The half-written
    state is reported as changed game files and no commit is made; once the
    obstacle is gone, applying that release again secures what was already
    written and finishes the change, with the installed mod untouched.
    """
    install_commit(
        Repo(spt_game_dir), "ModA", files={"BepInEx/plugins/ModA.dll": "mod"}
    )
    offer(spt_release_source, make_spt_release, NEW)
    spt_release_source.offer(
        make_archive(
            {
                name: content
                for name, content in spt_release_members(NEWEST, BUILD).items()
                if name != STALE_MEMBER
            },
            "zip",
        ),
        version=NEWEST,
        client_build=BUILD,
    )
    services.apply_spt_release(spt_game_dir, NEW)
    loader = spt_game_dir / "winhttp.dll"
    loader.chmod(0o444)

    with pytest.raises(SptReleaseWriteError):
        services.apply_spt_release(spt_game_dir, NEWEST)

    assert release_subjects(spt_game_dir) == [
        f"spt: {NEW}",
        "mod: ModA 1.0.0",
        "chore: clean SPT 4.1.0",
    ]
    assert services.status(spt_game_dir).game_files_changed == [
        Path("BepInEx/core/BepInEx.dll"),
        Path("BepInEx/patchers/spt-prepatch.dll"),
        Path(SPT_OWN_PLUGIN),
        Path("LICENSE-BepInEx.txt"),
        Path("SPT_Runtime/SPT.Server.exe"),
        Path(STALE_MEMBER),
        Path("doorstop_config.ini"),
    ]

    loader.chmod(0o644)
    result = services.apply_spt_release(spt_game_dir, NEWEST)

    assert result.version == NEWEST
    assert release_subjects(spt_game_dir) == [
        f"spt: {NEWEST}",
        "snapshot: game files updated",
        f"spt: {NEW}",
        "mod: ModA 1.0.0",
        "chore: clean SPT 4.1.0",
    ]
    assert loader.read_text(encoding="utf-8") == f"doorstop of {NEWEST}"
    assert not (spt_game_dir / STALE_MEMBER).exists()
    assert [mod.name for mod in services.list_mods(spt_game_dir)] == ["ModA"]
    assert (spt_game_dir / "BepInEx/plugins/ModA.dll").read_text(
        encoding="utf-8"
    ) == "mod"


@pytest.mark.parametrize(
    ("obstacle", "blocked_mode", "restored_mode", "expected_file"),
    [
        pytest.param(
            "SPT_Runtime/SPT_Data/configs/core.json",
            0o444,
            0o644,
            "SPT_Runtime/SPT_Data/configs/core.json",
            id="an installed file that cannot be overwritten",
        ),
        pytest.param(
            "BepInEx/plugins",
            0o555,
            0o755,
            SPT_OWN_PLUGIN,
            id="a folder that cannot take a new file",
        ),
    ],
)
def test_apply_says_which_file_it_could_not_write_and_where_the_previous_ones_are(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    obstacle: str,
    blocked_mode: int,
    restored_mode: int,
    expected_file: str,
) -> None:
    """apply_spt_release() turns a refused write into a plain SptReleaseWriteError.

    The game directory is made unwritable at one place -- a file of the
    program set that may not be overwritten and a folder that may not take a
    new file -- so writing the release stops there. The message has to name
    that file, what the system said and where the previous files are, instead
    of letting the raw operating system error reach the caller, and nothing
    may be committed.
    """
    offer(spt_release_source, make_spt_release, NEW)
    blocked = spt_game_dir / obstacle
    blocked.chmod(blocked_mode)

    try:
        with pytest.raises(SptReleaseWriteError) as failure:
            services.apply_spt_release(spt_game_dir, NEW)
    finally:
        blocked.chmod(restored_mode)

    assert str(failure.value) == (
        "The game files could not be written: Permission denied. The update "
        f"stopped at {expected_file}; your previous files are one snapshot back."
    )
    assert release_subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_the_release_before_a_refused_write_is_one_snapshot_back(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """What the message promises holds: one snapshot back stands the previous release.

    The first release is in place when the second cannot write its loader.
    Securing the half-written state and undoing that one snapshot puts the
    files and the version of the first release back, so the promise the
    message makes is the state the game directory really reaches.
    """
    offer(spt_release_source, make_spt_release, NEW)
    offer(spt_release_source, make_spt_release, NEWEST)
    services.apply_spt_release(spt_game_dir, NEW)
    loader = spt_game_dir / "winhttp.dll"
    loader.chmod(0o444)

    try:
        with pytest.raises(SptReleaseWriteError):
            services.apply_spt_release(spt_game_dir, NEWEST)
    finally:
        loader.chmod(0o644)
    services.snapshot(spt_game_dir, "the half-written update")
    result = services.revert_spt_update(spt_game_dir)

    assert result.version == NEW
    assert loader.read_text(encoding="utf-8") == f"doorstop of {NEW}"
    assert (spt_game_dir / STALE_MEMBER).read_text(encoding="utf-8") == json.dumps(
        {"release": NEW}
    )
