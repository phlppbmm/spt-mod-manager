"""Behaviour of the SPT version change: ``list_spt_releases``, ``apply_spt_release``
and ``revert_spt_update``, together with the path logic of ``sptrelease.apply``.

Covers the release list (series filtering, order, installed/newer flags,
downloadability, an unreadable installed version, the client build, an
unreachable source), the apply (auto-snapshot, download and cache,
extraction, the single ``spt:`` commit, the re-resolved SPT version, deleted
stale program files, untouched configs and mods, excluded archive members,
progress reports), its refusals (unknown version, no download, another
series, build mismatch, unreadable build, a running game, an unmanaged file,
a download that does not arrive whole), a copy that fails mid-write and the
state the next change finds, the 4.0 ``SPT/`` layout, the undo of a version
change and of a game-file snapshot, repeated undos stepping back one change
each, the plain refusals when a change no longer fits, and the waiting for the
game lock.
"""

import json
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx

from conftest import (
    DEFAULT_RELEASE_DATE,
    SptReleaseSource,
    clean_repo_at,
    spt_release_members,
    write_client_exe,
)
from fixture_types import InstallCommit, MakeArchive, MakeSptRelease
from pe_builder import write_server_exe
from sptmod.core import game_dir, services
from sptmod.core.errors import (
    ArchiveError,
    ClientBuildMismatchError,
    DirtyTreeError,
    GameRunningError,
    GitError,
    NotAGameDirError,
    SptReleaseUnavailableError,
)
from sptmod.core.game_dir import SPT_VERSION_CONFIG_KEY, GameLayout
from sptmod.core.models import ModFile, Progress
from sptmod.core.repo import Repo
from sptmod.core.sptrelease.apply import (
    compare_versions,
    installed_program_files,
    release_files,
    required_client_build,
    same_version_line,
    version_key,
)
from sptmod.core.sptrelease.client import SptRelease, SptReleaseClient
from sptmod.core.workspace import game_lock

pytestmark = pytest.mark.usefixtures("not_running")

INSTALLED = "4.1.0"
NEW = "4.1.1"
NEWEST = "4.1.2"
OLDER = "4.0.9"
OTHER_SERIES = "4.2.0"
BUILD = "40743"
OTHER_BUILD = "40999"

# What a release writes into a game directory of the 4.1 layout, in the order
# ``git show --name-status`` lists it.
RELEASE_TARGETS = (
    "BepInEx/core/BepInEx.dll",
    "BepInEx/patchers/spt-prepatch.dll",
    "BepInEx/plugins/spt/spt-core.dll",
    "LICENSE-BepInEx.txt",
    "SPT_Runtime/SPT.Server.exe",
    "SPT_Runtime/SPT_Data/configs/core.json",
    "SPT_Runtime/SPT_Data/database/x.json",
    "doorstop_config.ini",
    "winhttp.dll",
)

# Members a release ships that belong to the player, to a mod or to the
# server's own runtime data; none of them may reach the game directory.
RELEASE_MEMBERS_KEPT_OUT = (
    "BepInEx/config/BepInEx.cfg",
    "SPT_Runtime/user/mods/x/package.json",
    "SPT_Runtime/SPT_Data/cache/y",
    "SPT_Runtime/SPT_Data/logs/z.log",
)

PROFILE = "SPT_Runtime/user/profiles/abc.json"

# A file of the program set that lies below a mod path too, so a force-install
# can write over it, and a release member a custom archive drops to make the
# installed copy stale.
SPT_OWN_PLUGIN = "BepInEx/plugins/spt/spt-core.dll"
STALE_MEMBER = "SPT_Runtime/SPT_Data/database/x.json"

# How many characters of a commit hash a message would have to carry for a
# user to see one.
_SHORT_HASH_LENGTH = 7


def _offer(
    source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    version: str,
    *,
    client_build: str = BUILD,
    published_at: str = DEFAULT_RELEASE_DATE,
    size: int | None = None,
) -> respx.Route:
    """Publish ``version`` with a matching archive and return its download route."""
    return source.offer(
        make_spt_release(version=version, client_build=client_build),
        version=version,
        client_build=client_build,
        published_at=published_at,
        size=size,
    )


def _progress() -> tuple[list[tuple[str, int, str]], Progress]:
    """Return a list of reported ``(step, percent, message)`` and the callback filling it."""
    events: list[tuple[str, int, str]] = []

    def _capture(step: str, percent: int, message: str) -> None:
        events.append((step, percent, message))

    return events, _capture


def _unversioned_game_dir(root: Path) -> Path:
    """Turn ``root`` into a tracked game directory whose SPT version is unreadable.

    The 4.1 layout keeps its version in ``SPT.Server.exe`` only, so a game
    directory without that executable and without a stored version genuinely
    has none.
    """
    write_client_exe(root)
    clean_repo_at(root)
    return root


def _unpack(root: Path, members: Mapping[str, str | bytes]) -> Path:
    """Write ``members`` below ``root`` the way extracting a release does."""
    for name, content in members.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
    return root


def _subjects(path: Path) -> list[str]:
    """Return the commit subjects of the game repository at ``path``, newest first."""
    return [commit.subject for commit in services.history(path)]


def _release(client_build: str) -> SptRelease:
    """Return a published release naming ``client_build``, with no download."""
    return SptRelease(
        version=NEW,
        client_build=client_build,
        published_at=datetime(2025, 1, 2, tzinfo=UTC),
        url="https://spt-releases.invalid/tag/4.1.1",
        download_url="",
        archive_name="",
        content_length=None,
    )


# --- version arithmetic --------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("4.1.2", (4, 1, 2)),
        ("v4.1.2", (4, 1, 2)),
        (" 4.1.2 ", (4, 1, 2)),
        ("4.1", (4, 1, 0)),
        ("4", (4, 0, 0)),
        ("4.1.2.3", (4, 1, 2)),
        ("4.1.x", (4, 1, 0)),
        ("", (0, 0, 0)),
    ],
    ids=[
        "three-components",
        "v-prefix",
        "surrounding-space",
        "two-components",
        "one-component",
        "fourth-component-ignored",
        "non-numeric-component",
        "empty",
    ],
)
def test_version_key_reads_three_padded_numeric_components(
    version: str, expected: tuple[int, int, int]
) -> None:
    """version_key() returns the first three components as numbers.

    A missing or non-numeric component counts as zero, which is what makes an
    unknown version sort before every published one.
    """
    assert version_key(version) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("4.1.2", "4.1.1", 1),
        ("4.1.1", "4.1.2", -1),
        ("4.1.2", "4.1.2", 0),
        ("4.1.2", "v4.1.2.9", 0),
        ("4.1.2", "", 1),
    ],
    ids=["newer", "older", "equal", "equal-despite-notation", "against-unknown"],
)
def test_compare_versions_orders_by_the_numeric_components(
    left: str, right: str, expected: int
) -> None:
    """compare_versions() answers -1, 0 or 1 for the order of two versions."""
    assert compare_versions(left, right) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("4.1.2", "4.1.0", True),
        ("4.1.2", "4.2.0", False),
        ("4.1.2", "3.1.2", False),
    ],
    ids=["same-series", "other-minor", "other-major"],
)
def test_same_version_line_compares_major_and_minor_only(
    left: str, right: str, expected: bool
) -> None:
    """same_version_line() answers whether two versions share major and minor."""
    assert same_version_line(left, right) is expected


# --- which files a release brings and replaces ---------------------------


@pytest.mark.parametrize(
    "runtime_dir_name", ["SPT_Runtime", "SPT"], ids=["4.1-layout", "4.0-layout"]
)
def test_release_files_takes_the_program_set_into_the_installed_runtime_directory(
    tmp_path: Path, runtime_dir_name: str
) -> None:
    """release_files() returns the program members, addressed to the runtime dir in use.

    A release always names its runtime directory ``SPT_Runtime``; an
    installation of the 4.0 layout takes the same members into ``SPT/``.
    Player content, mod settings, caches and logs are left in the archive.
    """
    unpacked = _unpack(tmp_path / "unpacked", spt_release_members(NEW, BUILD))
    layout = GameLayout(root=tmp_path / "game", runtime_dir_name=runtime_dir_name)

    files = release_files(unpacked, layout)

    assert [entry.target.as_posix() for entry in files] == sorted(
        target.replace("SPT_Runtime/", f"{runtime_dir_name}/")
        for target in RELEASE_TARGETS
    )
    assert [entry.source.relative_to(unpacked).as_posix() for entry in files] == sorted(
        RELEASE_TARGETS
    )


def test_installed_program_files_lists_the_set_a_release_replaces(
    tmp_path: Path,
) -> None:
    """installed_program_files() returns the installed files a release owns.

    The runtime directory's own files, the whole ``SPT_Data`` tree without its
    caches and logs, SPT's client files below ``BepInEx``, the BepInEx runtime
    and the loader files of the game root -- but neither the game client, nor
    a mod, nor a file in a subdirectory of the runtime directory.
    """
    root = _unpack(
        tmp_path / "game",
        {
            "SPT_Runtime/SPT.Server.exe": "server",
            "SPT_Runtime/SPT_Data/configs/core.json": "{}",
            "SPT_Runtime/SPT_Data/cache/y": "cache",
            "SPT_Runtime/SPT_Data/logs/z.log": "log",
            "SPT_Runtime/user/mods/m/package.json": "{}",
            "SPT_Runtime/plugins/deep.dll": "deep",
            "BepInEx/core/BepInEx.dll": "core",
            "BepInEx/patchers/spt-prepatch.dll": "prepatch",
            "BepInEx/patchers/OtherMod.dll": "other",
            "BepInEx/plugins/spt/spt-core.dll": "spt-core",
            "BepInEx/plugins/ModA.dll": "mod",
            "BepInEx/config/BepInEx.cfg": "config",
            "winhttp.dll": "loader",
            "doorstop_config.ini": "loader config",
            "LICENSE-BepInEx.txt": "licence",
            "EscapeFromTarkov.exe": "client",
        },
    )
    layout = GameLayout(root=root, runtime_dir_name="SPT_Runtime")

    assert [entry.as_posix() for entry in installed_program_files(layout)] == [
        "BepInEx/core/BepInEx.dll",
        "BepInEx/patchers/spt-prepatch.dll",
        "BepInEx/plugins/spt/spt-core.dll",
        "LICENSE-BepInEx.txt",
        "SPT_Runtime/SPT.Server.exe",
        "SPT_Runtime/SPT_Data/configs/core.json",
        "doorstop_config.ini",
        "winhttp.dll",
    ]


def test_installed_program_files_of_an_empty_directory_is_empty(tmp_path: Path) -> None:
    """installed_program_files() returns nothing when no program file is there.

    A directory holding neither the runtime directory nor ``BepInEx`` has
    nothing a release could replace, so nothing is reported as stale.
    """
    layout = GameLayout(root=tmp_path, runtime_dir_name="SPT_Runtime")

    assert installed_program_files(layout) == []


# --- which client build a release needs ----------------------------------


def test_required_client_build_takes_the_build_the_release_itself_names(
    tmp_path: Path,
) -> None:
    """required_client_build() prefers the release's own build over the archive.

    The core configuration of the unpacked release names another build; the
    release entry wins, because it is what the source published.
    """
    unpacked = _unpack(tmp_path / "unpacked", spt_release_members(NEW, BUILD))

    assert required_client_build(_release(OTHER_BUILD), unpacked) == OTHER_BUILD


@pytest.mark.parametrize(
    ("core_json", "expected"),
    [
        ('{"compatibleTarkovVersion": "0.16.9.40743"}', "40743"),
        ('{"compatibleTarkovVersion": "0.16.9.x"}', ""),
        ('{"compatibleTarkovVersion": 40743}', ""),
        ('{"projectName": "SPT"}', ""),
        ("not json at all", ""),
        ("[1, 2]", ""),
    ],
    ids=[
        "build-named",
        "no-numeric-build",
        "build-not-a-string",
        "no-build-field",
        "unreadable-json",
        "not-an-object",
    ],
)
def test_required_client_build_reads_the_core_configuration_of_the_release(
    tmp_path: Path, core_json: str, expected: str
) -> None:
    """required_client_build() falls back to ``compatibleTarkovVersion``.

    Its trailing component is the build; a configuration that does not name
    one leaves the build unknown, which is what makes the comparison optional.
    """
    unpacked = _unpack(
        tmp_path / "unpacked", {"SPT_Runtime/SPT_Data/configs/core.json": core_json}
    )

    assert required_client_build(_release(""), unpacked) == expected


def test_required_client_build_reads_a_core_configuration_of_the_4_0_layout(
    tmp_path: Path,
) -> None:
    """required_client_build() also reads a release that ships ``SPT/`` instead.

    Both runtime directory names are looked at, so a release of the 4.0 series
    names its build as readably as a 4.1 one.
    """
    unpacked = _unpack(
        tmp_path / "unpacked",
        {"SPT/SPT_Data/configs/core.json": '{"compatibleTarkovVersion": "0.16.9.40743"}'},
    )

    assert required_client_build(_release(""), unpacked) == BUILD


def test_required_client_build_is_unknown_without_a_readable_configuration(
    tmp_path: Path,
) -> None:
    """required_client_build() answers empty for a missing or undecodable configuration.

    One release ships no core configuration at all, the other one that is no
    UTF-8 text; neither may be taken for a build number.
    """
    missing = _unpack(tmp_path / "missing", {"SPT_Runtime/SPT.Server.exe": "server"})
    undecodable = _unpack(
        tmp_path / "undecodable",
        {"SPT_Runtime/SPT_Data/configs/core.json": b"\xff\xfe\x00binary"},
    )

    assert required_client_build(_release(""), missing) == ""
    assert required_client_build(_release(""), undecodable) == ""


# --- list_spt_releases ---------------------------------------------------


def test_list_reports_the_series_of_the_installed_version_newest_first(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() lists only the installed series, newest version first.

    Releases of another series need the official installer, so they are left
    out; the installed version is marked, everything above it is newer.
    """
    for version in (OLDER, INSTALLED, NEW, NEWEST, OTHER_SERIES):
        _offer(spt_release_source, make_spt_release, version)

    listed = services.list_spt_releases(spt_game_dir)

    assert listed.current_version == INSTALLED
    assert listed.client_build == BUILD
    assert listed.latest == NEWEST
    assert [
        (entry.version, entry.installed, entry.newer) for entry in listed.releases
    ] == [
        (NEWEST, False, True),
        (NEW, False, True),
        (INSTALLED, True, False),
    ]


def test_list_reports_the_page_and_the_size_of_every_release(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reports the release page, its date and its download.

    A release whose archive was withdrawn stays in the list, marked as not
    downloadable and without a size, because it can still be installed with
    the official installer.
    """
    archive = make_spt_release(version=NEW)
    spt_release_source.offer(
        archive, version=NEW, published_at="2025-03-04T05:06:07Z"
    )
    spt_release_source.announce(NEWEST, published_at="2025-06-07T08:09:10Z")

    listed = services.list_spt_releases(spt_game_dir)

    assert [
        (
            entry.version,
            entry.client_build,
            entry.published_at,
            entry.url,
            entry.downloadable,
            entry.content_length,
        )
        for entry in listed.releases
    ] == [
        (
            NEWEST,
            BUILD,
            datetime(2025, 6, 7, 8, 9, 10, tzinfo=UTC),
            "https://spt-releases.invalid/tag/4.1.2",
            False,
            None,
        ),
        (
            NEW,
            BUILD,
            datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC),
            "https://spt-releases.invalid/tag/4.1.1",
            True,
            archive.stat().st_size,
        ),
    ]


def test_list_shows_every_release_when_the_installed_version_is_unknown(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() drops the series filter when it knows no version.

    Without a readable version nothing can be compared, so every published
    release is offered and none of them is the installed one.
    """
    path = _unversioned_game_dir(fake_game_dir_runtime)
    for version in (OLDER, NEW, OTHER_SERIES):
        _offer(spt_release_source, make_spt_release, version)

    listed = services.list_spt_releases(path)

    assert listed.current_version == ""
    assert listed.latest == OTHER_SERIES
    assert [
        (entry.version, entry.installed, entry.newer) for entry in listed.releases
    ] == [(OTHER_SERIES, False, True), (NEW, False, True), (OLDER, False, True)]


def test_list_reports_no_client_build_when_the_game_client_is_missing(
    fake_game_dir_runtime: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() leaves the client build empty when it cannot be read.

    The game directory holds no ``EscapeFromTarkov.exe``, so the build stays
    unknown while the release list itself is still delivered.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", INSTALLED)
    clean_repo_at(fake_game_dir_runtime)
    _offer(spt_release_source, make_spt_release, NEW)

    listed = services.list_spt_releases(fake_game_dir_runtime)

    assert listed.client_build == ""
    assert [entry.version for entry in listed.releases] == [NEW]


def test_list_is_empty_when_no_release_of_the_installed_series_is_published(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reports no release and no latest for a series without one."""
    _offer(spt_release_source, make_spt_release, OTHER_SERIES)

    listed = services.list_spt_releases(spt_game_dir)

    assert not listed.releases
    assert listed.latest == ""
    assert listed.current_version == INSTALLED


def test_list_uses_a_release_client_it_is_handed(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reads through the client passed to it.

    Callers that already hold a client -- the server process serving several
    requests -- hand it in instead of having one built per call.
    """
    _offer(spt_release_source, make_spt_release, NEW)

    with SptReleaseClient() as client:
        listed = services.list_spt_releases(spt_game_dir, client=client)

    assert [entry.version for entry in listed.releases] == [NEW]


def test_list_reports_an_unreachable_release_source(
    spt_game_dir: Path, spt_release_source: SptReleaseSource
) -> None:
    """list_spt_releases() raises SptReleaseUnavailableError when the source fails."""
    spt_release_source.unavailable(503)

    with pytest.raises(SptReleaseUnavailableError, match="not available right now"):
        services.list_spt_releases(spt_game_dir)


def test_list_rejects_a_directory_that_is_no_game_directory(tmp_path: Path) -> None:
    """list_spt_releases() raises NotAGameDirError before it reads any release."""
    with pytest.raises(NotAGameDirError, match="not an SPT game directory"):
        services.list_spt_releases(tmp_path)


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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)

    services.apply_spt_release(spt_game_dir, NEW)

    assert _subjects(spt_game_dir) == [
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
    _offer(spt_release_source, make_spt_release, NEW)
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
    route = _offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)

    result = services.apply_spt_release(spt_game_dir, NEW)

    assert (result.previous_version, result.version) == (NEW, NEW)
    assert result.commit == ""
    assert not result.files
    assert _subjects(spt_game_dir) == [f"spt: {NEW}", "chore: clean SPT 4.1.0"]
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
    first = _offer(spt_release_source, make_spt_release, NEW)
    second = _offer(spt_release_source, make_spt_release, NEWEST)
    services.apply_spt_release(spt_game_dir, NEW)

    result = services.apply_spt_release(spt_game_dir, NEWEST)

    assert (result.previous_version, result.version) == (NEW, NEWEST)
    assert (first.call_count, second.call_count) == (1, 1)
    assert _subjects(spt_game_dir) == [
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
    _offer(spt_release_source, make_spt_release, NEW)

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
    path = _unversioned_game_dir(fake_game_dir_runtime)
    _offer(spt_release_source, make_spt_release, OTHER_SERIES)

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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW)
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


# --- apply_spt_release, the refusals -------------------------------------


def test_apply_refuses_a_version_the_source_does_not_publish(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() raises SptReleaseUnavailableError for an unknown version."""
    _offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(
        SptReleaseUnavailableError, match="There is no published SPT 4.1.9."
    ):
        services.apply_spt_release(spt_game_dir, "4.1.9")

    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


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
    _offer(spt_release_source, make_spt_release, OTHER_SERIES)

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
    _offer(spt_release_source, make_spt_release, NEW, client_build=OTHER_BUILD)

    with pytest.raises(ClientBuildMismatchError) as error:
        services.apply_spt_release(spt_game_dir, NEW)

    assert str(error.value) == (
        f"SPT {NEW} needs Escape from Tarkov build {OTHER_BUILD}, but your game "
        f"is build {BUILD}. Change the game version with the official SPT "
        f"installer from https://sp-mod.com/installer first, then try again."
    )
    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


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
    _offer(spt_release_source, make_spt_release, NEW)

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
    _offer(spt_release_source, make_spt_release, NEW, client_build="")

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
    _offer(spt_release_source, make_spt_release, NEW)
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
    _offer(spt_release_source, make_spt_release, NEW)

    with pytest.raises(DirtyTreeError, match="BepInEx/plugins/ManualMod"):
        services.apply_spt_release(spt_game_dir, NEW)

    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_refuses_a_download_that_does_not_arrive_whole(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release() raises ArchiveError when the archive size differs.

    The release announces more bytes than arrive, so the download is not the
    published archive and nothing of it is written.
    """
    _offer(spt_release_source, make_spt_release, NEW, size=999_999)

    with pytest.raises(ArchiveError, match="expected 999999"):
        services.apply_spt_release(spt_game_dir, NEW)

    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_apply_rejects_a_directory_that_is_no_game_directory(tmp_path: Path) -> None:
    """apply_spt_release() raises NotAGameDirError before it reads any release."""
    with pytest.raises(NotAGameDirError, match="not an SPT game directory"):
        services.apply_spt_release(tmp_path, NEW)


# --- revert_spt_update ---------------------------------------------------


def test_revert_puts_the_previous_release_back(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    install_commit: InstallCommit,
) -> None:
    """revert_spt_update() undoes the last version change in one commit.

    The program files hold what they held before, the version is the one
    before again, and the installed mods keep their files, because a version
    change never contained any.
    """
    install_commit(Repo(spt_game_dir), "ModA", files={"BepInEx/plugins/ModA.dll": "mod"})
    _offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)

    result = services.revert_spt_update(spt_game_dir)

    assert (result.previous_version, result.version) == (NEW, INSTALLED)
    assert _subjects(spt_game_dir) == [
        f'Revert "spt: {NEW}"',
        f"spt: {NEW}",
        "mod: ModA 1.0.0",
        "chore: clean SPT 4.1.0",
    ]
    assert not (spt_game_dir / "winhttp.dll").exists()
    assert (spt_game_dir / "BepInEx/plugins/ModA.dll").read_text(
        encoding="utf-8"
    ) == "mod"
    assert [mod.name for mod in services.list_mods(spt_game_dir)] == ["ModA"]
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == INSTALLED
    assert result.files == [
        ModFile(path=Path("BepInEx/core/BepInEx.dll"), change="deleted"),
        ModFile(path=Path("BepInEx/patchers/spt-prepatch.dll"), change="deleted"),
        ModFile(path=Path("BepInEx/plugins/spt/spt-core.dll"), change="deleted"),
        ModFile(path=Path("LICENSE-BepInEx.txt"), change="deleted"),
        ModFile(path=Path("SPT_Runtime/SPT.Server.exe"), change="modified"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/configs/core.json"), change="modified"),
        ModFile(path=Path("SPT_Runtime/SPT_Data/database/x.json"), change="deleted"),
        ModFile(path=Path("doorstop_config.ini"), change="deleted"),
        ModFile(path=Path("winhttp.dll"), change="deleted"),
    ]


def test_revert_undoes_the_securing_of_files_an_update_outside_sptmod_wrote(
    fake_game_dir_runtime: Path,
) -> None:
    """revert_spt_update() also takes back a snapshot of changed game files.

    An SPT update run outside sptmod is secured as ``snapshot: game files
    updated``; undoing the version change means undoing that commit, and a
    version that stays unreadable is not stored.
    """
    path = _unversioned_game_dir(fake_game_dir_runtime)
    (path / "SPT_Runtime/SPT_Data/database/new.json").parent.mkdir(exist_ok=True)
    (path / "SPT_Runtime/SPT_Data/database/new.json").write_text("{}", encoding="utf-8")
    services.snapshot(path, "played a raid")

    result = services.revert_spt_update(path)

    assert (result.previous_version, result.version) == ("", "")
    assert _subjects(path)[:2] == [
        'Revert "snapshot: game files updated"',
        "snapshot: game files updated",
    ]
    assert not (path / "SPT_Runtime/SPT_Data/database/new.json").exists()
    assert Repo(path).config_get(SPT_VERSION_CONFIG_KEY) is None


def test_revert_without_a_version_change_says_there_is_nothing_to_undo(
    spt_game_dir: Path,
) -> None:
    """revert_spt_update() raises GitError when no version change was ever made."""
    with pytest.raises(GitError, match="There is no SPT version change to undo."):
        services.revert_spt_update(spt_game_dir)

    assert _subjects(spt_game_dir) == ["chore: clean SPT 4.1.0"]


def test_revert_refuses_while_the_game_is_running(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revert_spt_update() raises GameRunningError and leaves the version in place."""
    _offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: ["SPT.Server.exe"])

    with pytest.raises(GameRunningError, match="close it first"):
        services.revert_spt_update(spt_game_dir)

    assert _subjects(spt_game_dir)[0] == f"spt: {NEW}"


def test_revert_rejects_a_directory_that_is_no_game_directory(tmp_path: Path) -> None:
    """revert_spt_update() raises NotAGameDirError before it touches git."""
    with pytest.raises(NotAGameDirError, match="not an SPT game directory"):
        services.revert_spt_update(tmp_path)


def test_revert_twice_after_one_version_change_finds_nothing_left_to_undo(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """revert_spt_update() called twice after one change refuses the second time.

    One release was applied and taken back; the undo commit takes its subject
    back with it, so the change below it is no candidate any more. The second
    undo therefore says in plain words that there is nothing to undo and
    leaves both the history and the game directory as the first undo left them.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)
    services.revert_spt_update(spt_game_dir)

    with pytest.raises(GitError, match="There is no SPT version change to undo."):
        services.revert_spt_update(spt_game_dir)

    assert _subjects(spt_game_dir) == [
        f'Revert "spt: {NEW}"',
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert not (spt_game_dir / "winhttp.dll").exists()
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == INSTALLED


def test_revert_twice_after_two_version_changes_reaches_the_older_change(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """revert_spt_update() steps back one version change per call.

    Two releases were applied one after the other; each undo cancels exactly
    one of them, so the first lands on the release in between and the second
    on the version the installation started with. The files and the stored
    version follow each step, because that is what "one step back" means.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    _offer(spt_release_source, make_spt_release, NEWEST)
    services.apply_spt_release(spt_game_dir, NEW)
    services.apply_spt_release(spt_game_dir, NEWEST)

    first = services.revert_spt_update(spt_game_dir)
    middle = (spt_game_dir / "winhttp.dll").read_text(encoding="utf-8")
    second = services.revert_spt_update(spt_game_dir)

    assert (first.previous_version, first.version) == (NEWEST, NEW)
    assert middle == f"doorstop of {NEW}"
    assert (second.previous_version, second.version) == (NEW, INSTALLED)
    assert _subjects(spt_game_dir) == [
        f'Revert "spt: {NEW}"',
        f'Revert "spt: {NEWEST}"',
        f"spt: {NEWEST}",
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert not (spt_game_dir / "winhttp.dll").exists()
    assert Repo(spt_game_dir).config_get(SPT_VERSION_CONFIG_KEY) == INSTALLED


def test_revert_twice_takes_back_both_securings_of_changed_game_files(
    spt_game_dir: Path,
) -> None:
    """revert_spt_update() cancels one securing of changed game files per call.

    An SPT update outside sptmod is secured twice under the same subject; one
    undo may therefore not stand for both. The first undo brings the file back
    to the state the first securing held, the second removes it altogether,
    and a third call finds nothing left -- two changes, two undos, no more.
    """
    data = spt_game_dir / "SPT_Runtime/SPT_Data/database/loot.json"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("first update", encoding="utf-8")
    services.snapshot(spt_game_dir, "after the first update")
    data.write_text("second update", encoding="utf-8")
    services.snapshot(spt_game_dir, "after the second update")

    services.revert_spt_update(spt_game_dir)
    after_first = data.read_text(encoding="utf-8")
    second = services.revert_spt_update(spt_game_dir)

    assert after_first == "first update"
    assert not data.exists()
    assert second.files == [
        ModFile(
            path=Path("SPT_Runtime/SPT_Data/database/loot.json"), change="deleted"
        )
    ]
    with pytest.raises(GitError, match="There is no SPT version change to undo."):
        services.revert_spt_update(spt_game_dir)


def _assert_plain_undo_failure(error: GitError, path: Path, detail: str) -> None:
    """Assert ``error`` reports ``detail`` in the words a user gets to read.

    The message reaches the user unchanged, so it may name neither the git
    operation that failed nor any commit of the repository at ``path``.
    """
    message = str(error)
    assert message == f"Going back to the previous SPT version did not work: {detail}"
    assert "revert" not in message.lower()
    assert "commit" not in message.lower()
    assert not any(
        commit.hash[:_SHORT_HASH_LENGTH] in message
        for commit in services.history(path)
    )


def test_revert_names_the_files_that_changed_since_the_version_change(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
    make_archive: MakeArchive,
) -> None:
    """revert_spt_update() reports the files that no longer fit, in plain words.

    A mod was force-installed over a file SPT itself owns, so taking the
    version change back would have to remove a file that now holds the mod's
    content. The undo fails naming that file, and it changes nothing: the
    history and the file stay as they were.
    """
    _offer(spt_release_source, make_spt_release, NEW)
    services.apply_spt_release(spt_game_dir, NEW)
    archive = make_archive({SPT_OWN_PLUGIN: "patched by a mod"}, "zip")
    services.install_from_archive(spt_game_dir, archive, force=True)

    with pytest.raises(GitError) as failure:
        services.revert_spt_update(spt_game_dir)

    _assert_plain_undo_failure(
        failure.value,
        spt_game_dir,
        f"these files have changed since then: {SPT_OWN_PLUGIN}",
    )
    assert failure.value.conflicting_paths == [Path(SPT_OWN_PLUGIN)]
    assert _subjects(spt_game_dir) == [
        "mod: spt-core",
        f"spt: {NEW}",
        "chore: clean SPT 4.1.0",
    ]
    assert (spt_game_dir / SPT_OWN_PLUGIN).read_text(encoding="utf-8") == (
        "patched by a mod"
    )


def test_revert_says_the_folder_no_longer_matches_when_the_change_is_already_gone(
    fake_game_dir_runtime: Path,
    make_archive: MakeArchive,
) -> None:
    """revert_spt_update() fails in plain words when there is nothing left to take back.

    The securing of a changed game file is the youngest change, but a mod
    force-installed afterwards already wrote the earlier content back, so
    undoing it would change nothing at all. The message names no file, because
    none conflicts, and the game directory stays untouched.
    """
    path = fake_game_dir_runtime
    plugin = path / SPT_OWN_PLUGIN
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text("shipped with SPT", encoding="utf-8")
    write_server_exe(path, "SPT_Runtime", INSTALLED)
    write_client_exe(path)
    clean_repo_at(path)
    plugin.write_text("rewritten by an SPT update", encoding="utf-8")
    services.snapshot(path, "after the update")
    services.install_from_archive(
        path, make_archive({SPT_OWN_PLUGIN: "shipped with SPT"}, "zip"), force=True
    )

    with pytest.raises(GitError) as failure:
        services.revert_spt_update(path)

    _assert_plain_undo_failure(
        failure.value, path, "the game folder no longer matches that state"
    )
    assert failure.value.conflicting_paths == []
    assert _subjects(path) == [
        "mod: spt-core",
        "snapshot: game files updated",
        "chore: clean SPT 4.1.0",
    ]
    assert plugin.read_text(encoding="utf-8") == "shipped with SPT"


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
    _offer(spt_release_source, make_spt_release, NEW)
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

    with pytest.raises(PermissionError):
        services.apply_spt_release(spt_game_dir, NEWEST)

    assert _subjects(spt_game_dir) == [
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
    assert _subjects(spt_game_dir) == [
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
