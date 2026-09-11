"""SPT release fixtures for the sptmod test suite.

Registered through ``pytest_plugins`` in :mod:`conftest`.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from conftest import clean_repo_at
from fixture_types import JsonDict, MakeArchive, MakeSptRelease
from github_json import asset_json, release_json, release_without_asset_json
from pe_builder import build_synthetic_exe, write_server_exe
from sptmod.core import services
from sptmod.core.sptrelease.client import DEFAULT_RELEASES_URLS

# The mocked release source; every SPT-release test points the client at it
# through SPTMOD_SPT_RELEASE_URL, so the real GitHub host is never reached.
SPT_RELEASE_URL_ENV = "SPTMOD_SPT_RELEASE_URL"
SPT_RELEASE_LIST_URL = "https://spt-releases.invalid/releases"
SPT_RELEASE_DOWNLOAD_URL = "https://spt-releases.invalid/download"
CLIENT_EXE = "EscapeFromTarkov.exe"
DEFAULT_CLIENT_BUILD = "40743"
DEFAULT_RELEASE_DATE = "2025-01-02T03:04:05Z"

INSTALLED = "4.1.0"
NEW = "4.1.1"
NEWEST = "4.1.2"
OLDER = "4.0.9"
OTHER_SERIES = "4.2.0"
BUILD = "40743"
OTHER_BUILD = "40999"

# A file of the program set that lies below a mod path too, so a force-install
# can write over it.
SPT_OWN_PLUGIN = "BepInEx/plugins/spt/spt-core.dll"


def spt_release_members(version: str, client_build: str) -> dict[str, str | bytes]:
    """Return the members an SPT release archive ships, keyed by member name.

    Mirrors what the published archives hold: the runtime directory with the
    server executable, the core configuration naming the Escape from Tarkov
    version and the static database, SPT's own client files below
    ``BepInEx``, the BepInEx runtime and the Doorstop loader files of the
    archive root. The last four members are the ones an installation must
    never take from a release -- the BepInEx configuration, a server mod, a
    cache entry and a log file -- and every test asserting that they stay out
    of the game directory relies on them being in here.

    ``client_build`` empty leaves ``compatibleTarkovVersion`` without a
    numeric build, which is how a release that names no build at all is built.
    """
    return {
        "SPT_Runtime/SPT.Server.exe": build_synthetic_exe(version),
        "SPT_Runtime/SPT_Data/configs/core.json": json.dumps(
            {"projectName": "SPT", "compatibleTarkovVersion": f"0.16.9.{client_build}"}
        ),
        "SPT_Runtime/SPT_Data/database/x.json": json.dumps({"release": version}),
        "BepInEx/plugins/spt/spt-core.dll": f"spt-core of {version}",
        "BepInEx/patchers/spt-prepatch.dll": f"spt-prepatch of {version}",
        "BepInEx/core/BepInEx.dll": f"BepInEx of {version}",
        "winhttp.dll": f"doorstop of {version}",
        "doorstop_config.ini": f"[General]\nrelease={version}\n",
        "LICENSE-BepInEx.txt": f"BepInEx licence, shipped with {version}",
        "BepInEx/config/BepInEx.cfg": f"[Logging]\nfrom the {version} archive\n",
        "SPT_Runtime/user/mods/x/package.json": json.dumps({"name": "x"}),
        "SPT_Runtime/SPT_Data/cache/y": f"cache of {version}",
        "SPT_Runtime/SPT_Data/logs/z.log": f"log of {version}",
    }


@pytest.fixture
def make_spt_release(make_archive: MakeArchive) -> MakeSptRelease:
    """Factory building a release archive with the member shape SPT publishes.

    Returns a callable ``make_spt_release(*, version="4.1.1",
    client_build="40743") -> Path``; the archive is a zip whose members carry
    :func:`spt_release_members`, named as the real ``.7z`` releases are only
    where the download URL says so, because the format is taken from the
    magic bytes.
    """

    def _make(
        *, version: str = "4.1.1", client_build: str = DEFAULT_CLIENT_BUILD
    ) -> Path:
        return make_archive(spt_release_members(version, client_build), "zip")

    return _make


def write_client_exe(root: Path, build: str = DEFAULT_CLIENT_BUILD) -> Path:
    """Write an ``EscapeFromTarkov.exe`` reporting ``build`` into ``root``.

    The build is the second-to-last component of the client's
    ``ProductVersion`` resource once ``-`` counts as a separator, which is the
    shape the real client carries.
    """
    exe = root / CLIENT_EXE
    exe.write_bytes(build_synthetic_exe(f"0.16.9.5-{build}.36018", "ProductVersion"))
    return exe


class SptReleaseSource:
    """The mocked SPT release source: its release list and its archives.

    Every published release joins the list the client reads; ``offer`` puts an
    archive behind the download link of one, ``announce`` publishes a release
    whose archive was withdrawn, and ``unavailable`` lets the list itself fail.
    ``list_calls`` counts how often the list was read, so a stored listing can
    be told from a fetched one.
    """

    def __init__(self, router: respx.MockRouter, url: str) -> None:
        """Mock ``url`` with an empty release list."""
        self._router = router
        self._releases: list[JsonDict] = []
        self._list = router.get(url)
        self._publish()

    def offer(
        self,
        archive: Path,
        *,
        version: str,
        client_build: str = DEFAULT_CLIENT_BUILD,
        published_at: str = DEFAULT_RELEASE_DATE,
        size: int | None = None,
    ) -> respx.Route:
        """Publish ``version`` and serve ``archive`` behind its download link.

        ``size`` overrides the size the release announces, which is how a
        download that does not arrive whole is simulated; it defaults to the
        real size of ``archive``. Returns the download route, so a test can
        tell a fetch from a cache hit by its call count.
        """
        body = archive.read_bytes()
        name = f"SPT-{version}-{client_build}.7z"
        url = f"{SPT_RELEASE_DOWNLOAD_URL}/{version}/{name}"
        self._add(
            release_json(
                tag_name=version,
                name=_spt_release_name(version, client_build),
                html_url=f"https://spt-releases.invalid/tag/{version}",
                published_at=published_at,
                assets=[
                    asset_json(
                        name=name,
                        browser_download_url=url,
                        size=len(body) if size is None else size,
                    )
                ],
            )
        )
        return self._router.get(url).mock(
            return_value=httpx.Response(
                200, content=body, headers={"Content-Length": str(len(body))}
            )
        )

    def announce(
        self,
        version: str,
        *,
        client_build: str = DEFAULT_CLIENT_BUILD,
        published_at: str = DEFAULT_RELEASE_DATE,
    ) -> None:
        """Publish ``version`` without any download."""
        self._add(
            release_without_asset_json(
                tag_name=version,
                name=_spt_release_name(version, client_build),
                html_url=f"https://spt-releases.invalid/tag/{version}",
                published_at=published_at,
            )
        )

    def unavailable(
        self, status_code: int = 503, *, headers: dict[str, str] | None = None
    ) -> None:
        """Let the release list answer with ``status_code`` instead of releases.

        ``headers`` carries what the answer says beyond its status, which is
        how a refusal over the spent request budget is published.
        """
        self._list.mock(return_value=httpx.Response(status_code, headers=headers))

    @property
    def list_calls(self) -> int:
        """Return how often the release list itself was read."""
        return self._list.call_count

    def _add(self, release: JsonDict) -> None:
        self._releases.append(release)
        self._publish()

    def _publish(self) -> None:
        self._list.mock(return_value=httpx.Response(200, json=self._releases))


def _spt_release_name(version: str, client_build: str) -> str:
    """Return the release title, which names the build only when there is one."""
    return f"SPT {version} ({client_build})" if client_build else f"SPT {version}"


@pytest.fixture
def spt_release_source(monkeypatch: pytest.MonkeyPatch) -> Iterator[SptReleaseSource]:
    """The published SPT releases and their archives, mocked over real HTTP.

    Points ``SPTMOD_SPT_RELEASE_URL`` at a host that does not exist, so the
    client a service or the CLI builds for itself lands on this router.
    """
    monkeypatch.setenv(SPT_RELEASE_URL_ENV, SPT_RELEASE_LIST_URL)
    with respx.mock(assert_all_called=False) as router:
        yield SptReleaseSource(router, SPT_RELEASE_LIST_URL)


@pytest.fixture
def spt_release_sources(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[SptReleaseSource, ...]]:
    """Every release source the client reads by itself, mocked, one per default URL.

    Removes ``SPTMOD_SPT_RELEASE_URL``, so a service or the CLI builds the
    client with its own default sources, and mocks each of them separately;
    the first source of the returned tuple is the one read first. Lets a test
    publish different releases per repository -- what the current one carries
    and what the archived one stopped at -- without any of them reaching
    GitHub.
    """
    monkeypatch.delenv(SPT_RELEASE_URL_ENV, raising=False)
    with respx.mock(assert_all_called=False) as router:
        yield tuple(SptReleaseSource(router, url) for url in DEFAULT_RELEASES_URLS)


@pytest.fixture
def spt_game_dir(fake_game_dir_runtime: Path) -> Path:
    """A tracked SPT 4.1.0 installation with a readable client build.

    The 4.1+ ``SPT_Runtime`` layout with a server executable carrying the
    version, an ``EscapeFromTarkov.exe`` carrying build ``40743`` and one
    baseline commit -- the state a version change starts from.
    """
    write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.0")
    write_client_exe(fake_game_dir_runtime)
    clean_repo_at(fake_game_dir_runtime)
    return fake_game_dir_runtime


def offer(
    source: SptReleaseSource,
    make_release: MakeSptRelease,
    version: str,
    *,
    client_build: str = BUILD,
    published_at: str = DEFAULT_RELEASE_DATE,
    size: int | None = None,
) -> respx.Route:
    """Publish ``version`` with a matching archive and return its download route."""
    return source.offer(
        make_release(version=version, client_build=client_build),
        version=version,
        client_build=client_build,
        published_at=published_at,
        size=size,
    )


def unversioned_game_dir(root: Path) -> Path:
    """Turn ``root`` into a tracked game directory whose SPT version is unreadable.

    The 4.1 layout keeps its version in ``SPT.Server.exe`` only, so a game
    directory without that executable and without a stored version genuinely
    has none.
    """
    write_client_exe(root)
    clean_repo_at(root)
    return root


def release_subjects(path: Path) -> list[str]:
    """Return the commit subjects of the game repository at ``path``, newest first."""
    return [commit.subject for commit in services.history(path)]
