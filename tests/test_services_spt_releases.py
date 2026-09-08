"""Behaviour of the SPT release list and of the path logic ``sptrelease.apply`` builds it on.

Covers the version arithmetic (``version_key``, ``compare_versions``,
``same_version_line``), the file set a release brings and replaces
(``release_files``, ``installed_program_files``), the client build a release
needs (``required_client_build``) and ``list_spt_releases`` itself: series
filtering, order, installed/newer flags, downloadability, an unreadable
installed version, the client build, a handed release client and an
unreachable source.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import clean_repo_at
from fixture_types import MakeSptRelease
from fixtures_spt import (
    BUILD,
    INSTALLED,
    NEW,
    NEWEST,
    OLDER,
    OTHER_BUILD,
    OTHER_SERIES,
    SptReleaseSource,
    offer,
    spt_release_members,
    unversioned_game_dir,
)
from pe_builder import write_server_exe
from sptmod.core import services
from sptmod.core.errors import NotAGameDirError, SptReleaseUnavailableError
from sptmod.core.game_dir import GameLayout
from sptmod.core.sptrelease.apply import (
    compare_versions,
    installed_program_files,
    release_files,
    required_client_build,
    same_version_line,
    version_key,
)
from sptmod.core.sptrelease.client import SptRelease, SptReleaseClient

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


def _unpack(root: Path, members: Mapping[str, str | bytes]) -> Path:
    """Write ``members`` below ``root`` the way extracting a release does."""
    for name, content in members.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
    return root


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
        offer(spt_release_source, make_spt_release, version)

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
    path = unversioned_game_dir(fake_game_dir_runtime)
    for version in (OLDER, NEW, OTHER_SERIES):
        offer(spt_release_source, make_spt_release, version)

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
    offer(spt_release_source, make_spt_release, NEW)

    listed = services.list_spt_releases(fake_game_dir_runtime)

    assert listed.client_build == ""
    assert [entry.version for entry in listed.releases] == [NEW]


def test_list_is_empty_when_no_release_of_the_installed_series_is_published(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """list_spt_releases() reports no release and no latest for a series without one."""
    offer(spt_release_source, make_spt_release, OTHER_SERIES)

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
    offer(spt_release_source, make_spt_release, NEW)

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
