"""Tests for ``clear_catalog_cache`` and for the download cache the Forge installs share.

Covers what clearing the catalog removes and reports, with and without the
cached archives, and the reuse of a cached archive across a reinstall, a
re-download when the cached size no longer matches, an update to a cached and
to an uncached version, the staging directories an install leaves behind and
the vocabulary of a cache-hit message.
"""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx

from conftest import forge_trailers
from fixture_types import InstallCommit, MakeArchive
from forge_install_env import (
    SPT_VERSION,
    echo_dependencies,
    mock_download,
    mock_download_route,
    package_archive,
    seed_download_cache_entry,
    seed_target,
)
from forge_json import current_version_json, recommended_version_json, success_envelope, updates_payload
from sptmod.core import services
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient
from sptmod.core.forge.download import CACHE_HIT_MESSAGE
from sptmod.core.repo import Repo
from sptmod.core.workspace import download_cache_dir, staging_dir

# --- clear_catalog_cache --------------------------------------------------


def test_clear_catalog_cache_removes_the_db_file_and_reports_removed(
    tmp_path: Path,
) -> None:
    """If clear_catalog_cache's body is `pass`, this fails: the file survives
    and the return value is None instead of ClearCacheResult(removed=True)."""
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    assert db.exists()

    result = services.clear_catalog_cache(db_path=db)

    assert result == services.ClearCacheResult(db_path=db, removed=True)
    assert not db.exists()


def test_clear_catalog_cache_second_call_reports_not_removed(tmp_path: Path) -> None:
    """clear_catalog_cache on already-deleted cache returns ClearCacheResult(removed=False).

    After the first clear_catalog_cache call deletes db_path, calling it again
    returns removed=False (no file to remove).
    """
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    services.clear_catalog_cache(db_path=db)

    result = services.clear_catalog_cache(db_path=db)

    assert result == services.ClearCacheResult(db_path=db, removed=False)


def test_clear_catalog_cache_with_downloads_clears_cached_archives_and_reports_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``downloads=True`` still behaved like the default, this fails: the
    cached archive would survive and ``downloads_freed_bytes`` would stay 0.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    cache_dir = download_cache_dir()
    payload = b"cached archive bytes"
    entry = seed_download_cache_entry(cache_dir, 555, "mod.zip", payload)
    meta_size = (entry / "meta.json").stat().st_size
    expected_freed = len(payload) + meta_size

    result = services.clear_catalog_cache(db_path=db, downloads=True)

    assert result.db_path == db
    assert result.removed is True
    assert result.downloads_path == cache_dir
    assert result.downloads_freed_bytes == expected_freed
    assert not entry.exists()


def test_clear_catalog_cache_without_downloads_leaves_cached_archives_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``downloads=False`` default must not touch the archive cache at
    all -- if it did, the seeded archive below would disappear and
    ``downloads_freed_bytes`` would report the freed bytes instead of 0.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    cache_dir = download_cache_dir()
    entry = seed_download_cache_entry(cache_dir, 556, "mod.zip", b"cached bytes")

    result = services.clear_catalog_cache(db_path=db)

    assert result.downloads_path is None
    assert result.downloads_freed_bytes == 0
    assert (entry / "mod.zip").is_file()



# --- download cache: install_mod / update_mod reuse and staging cleanup ----


def test_reinstalling_a_removed_mod_reuses_the_cached_archive_and_skips_the_download(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If ``_download_entry`` re-downloaded even when a matching archive is
    already cached, this fails: the download route would be hit twice
    instead of once, and no progress message would report the cache hit.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = package_archive(make_archive, "CacheMod", "Cache Mod", "1.0.0")
    link = "https://dl.example/cache-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=701,
        name="Cache Mod",
        version="1.0.0",
        version_id=7010,
        link=link,
        content_length=content_length,
    )
    echo_dependencies(respx_router, [])
    route = mock_download_route(respx_router, link, archive)

    first = services.install_mod(clean_repo.path, "Cache Mod", "1.0.0", db_path=db_path, client=client)
    assert route.call_count == 1
    services.remove_mod(clean_repo.path, "Cache Mod")

    messages: list[str] = []
    second = services.install_mod(
        clean_repo.path,
        "Cache Mod",
        "1.0.0",
        db_path=db_path,
        client=client,
        progress=lambda step, percent, message: messages.append(message),
    )

    assert route.call_count == 1
    assert any(message.endswith(CACHE_HIT_MESSAGE) for message in messages)
    assert [entry.mod.name for entry in second.installed] == ["Cache Mod"]
    assert second.installed[0].mod.version == first.installed[0].mod.version
    assert (clean_repo.path / "SPT/user/mods/CacheMod/package.json").is_file()


def test_install_mod_redownloads_when_the_cached_archive_size_does_not_match(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cache entry whose stored size differs from the plan's
    ``content_length`` must not be trusted: install_mod must still fetch a
    fresh copy over HTTP -- exactly once -- rather than installing the stale
    bytes, and the install must still succeed.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = package_archive(make_archive, "StaleCacheMod", "Stale Cache Mod", "1.0.0")
    link = "https://dl.example/stale-cache-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=702,
        name="Stale Cache Mod",
        version="1.0.0",
        version_id=7020,
        link=link,
        content_length=content_length,
    )
    echo_dependencies(respx_router, [])
    route = mock_download_route(respx_router, link, archive)
    stale_payload = b"not the real archive; deliberately the wrong size"
    assert len(stale_payload) != content_length
    seed_download_cache_entry(download_cache_dir(), 7020, "stale.zip", stale_payload)

    result = services.install_mod(clean_repo.path, "Stale Cache Mod", "1.0.0", db_path=db_path, client=client)

    assert route.call_count == 1
    assert result.installed[0].mod.name == "Stale Cache Mod"
    assert (clean_repo.path / "SPT/user/mods/StaleCacheMod/package.json").is_file()


def _mock_updates_single(
    respx_router: respx.MockRouter,
    *,
    current_id: int,
    mod_id: int,
    name: str,
    slug: str,
    current_version: str,
    new_version_id: int,
    new_version: str,
    link: str,
    content_length: int,
) -> None:
    """Mock a Forge update check response with a single update available."""
    payload = updates_payload(
        SPT_VERSION,
        updates=[
            {
                "current_version": current_version_json(
                    version_id=current_id,
                    mod_id=mod_id,
                    name=name,
                    version=current_version,
                    slug=slug,
                ),
                "recommended_version": recommended_version_json(
                    version_id=new_version_id,
                    version=new_version,
                    link=link,
                    content_length=content_length,
                    spt_version=SPT_VERSION,
                ),
                "update_reason": "newer version available",
            }
        ],
    )
    respx_router.get("/mods/updates").mock(return_value=httpx.Response(200, json=success_envelope(payload)))


def test_update_mod_to_a_cached_version_skips_the_download(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If update_mod always downloaded the recommended version, this fails:
    the download route would be called even though a matching archive is
    already cached for the recommended version id.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    install_commit(
        clean_repo,
        "UpdateCacheMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/UpdateCacheMod/package.json": json.dumps(
                {"name": "UpdateCacheMod", "version": "1.0.0", "author": "Acme"}
            )
        },
        **forge_trailers("10", "100"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/UpdateCacheMod/package.json": json.dumps(
                {"name": "UpdateCacheMod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/update-cache-mod-2.0.0.zip"
    _mock_updates_single(
        respx_router,
        current_id=100,
        mod_id=10,
        name="UpdateCacheMod",
        slug="update-cache-mod",
        current_version="1.0.0",
        new_version_id=200,
        new_version="2.0.0",
        link=link,
        content_length=len(body),
    )
    route = mock_download_route(respx_router, link, new_archive)
    seed_download_cache_entry(download_cache_dir(), 200, "cached.zip", body)
    client = client_factory()

    result = services.update_mod(clean_repo.path, "UpdateCacheMod", db_path=db_path, client=client)

    assert route.call_count == 0
    assert result.version == "2.0.0"
    assert result.notice == ""


def test_update_mod_to_an_uncached_version_downloads_once_and_caches_it(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If update_mod's download were not cached afterwards, this fails: no
    archive would be left under the recommended version id in the download
    cache directory.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    install_commit(
        clean_repo,
        "FreshCacheMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/FreshCacheMod/package.json": json.dumps(
                {"name": "FreshCacheMod", "version": "1.0.0", "author": "Acme"}
            )
        },
        **forge_trailers("11", "110"),
    )
    new_archive = make_archive(
        {
            "SPT/user/mods/FreshCacheMod/package.json": json.dumps(
                {"name": "FreshCacheMod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/fresh-cache-mod-2.0.0.zip"
    _mock_updates_single(
        respx_router,
        current_id=110,
        mod_id=11,
        name="FreshCacheMod",
        slug="fresh-cache-mod",
        current_version="1.0.0",
        new_version_id=210,
        new_version="2.0.0",
        link=link,
        content_length=len(body),
    )
    route = mock_download_route(respx_router, link, new_archive)
    client = client_factory()

    result = services.update_mod(clean_repo.path, "FreshCacheMod", db_path=db_path, client=client)

    assert route.call_count == 1
    assert result.version == "2.0.0"
    cached_entry = download_cache_dir() / "210"
    cached_files = [path for path in cached_entry.iterdir() if path.name != "meta.json"]
    assert len(cached_files) == 1
    assert cached_files[0].read_bytes() == body


def test_install_mod_leaves_no_leftover_staging_directories(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If ``install_mod`` no longer cleaned up its per-entry temp directory
    (dropped when the plan-level wrapper was replaced with a per-entry one),
    this fails: a ``sptmod-*`` directory would be left behind in the shared
    staging area.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = package_archive(make_archive, "StagingMod", "Staging Mod", "1.0.0")
    link = "https://dl.example/staging-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=703,
        name="Staging Mod",
        version="1.0.0",
        version_id=7030,
        link=link,
        content_length=content_length,
    )
    echo_dependencies(respx_router, [])
    mock_download(respx_router, link, archive)

    services.install_mod(clean_repo.path, "Staging Mod", "1.0.0", db_path=db_path, client=client)

    assert not list(staging_dir().glob("sptmod-*"))


def test_no_download_progress_message_during_a_cache_hit_mentions_git_vocabulary(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The download-cache path must speak about files, not about the git
    internals it happens to sit next to: a cache hit is not a "commit" and
    the cache is not a "repository". Scoped to the ``download`` step's own
    messages, which is what the caching feature actually touched -- the
    unrelated ``commit`` install step ("committing X Y") is untouched by
    this feature and out of scope here.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    archive = package_archive(make_archive, "PlainWordsMod", "Plain Words Mod", "1.0.0")
    link = "https://dl.example/plain-words-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=704,
        name="Plain Words Mod",
        version="1.0.0",
        version_id=7040,
        link=link,
        content_length=content_length,
    )
    echo_dependencies(respx_router, [])
    mock_download(respx_router, link, archive)
    services.install_mod(clean_repo.path, "Plain Words Mod", "1.0.0", db_path=db_path, client=client)
    services.remove_mod(clean_repo.path, "Plain Words Mod")

    download_messages: list[str] = []

    def _progress(step: str, percent: int, message: str) -> None:
        # The parameter names are part of the Progress protocol.
        del percent
        if step == "download":
            download_messages.append(message)

    services.install_mod(
        clean_repo.path,
        "Plain Words Mod",
        "1.0.0",
        db_path=db_path,
        client=client,
        progress=_progress,
    )

    assert download_messages  # sanity: the cache-hit path did report progress
    forbidden = ("git", "commit", "repository", "revert", "cherry")
    for message in download_messages:
        lowered = message.lower()
        for word in forbidden:
            assert word not in lowered, f"{word!r} leaked into {message!r}"
