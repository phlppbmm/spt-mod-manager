"""Tests for ``plan_mod_install`` and for ``import_list`` with ``apply=True`` and ``content``.

Covers the planned order of a dependency and its requested mod, a link
missing from a sync-only catalog and the installs that resolve it, an unknown
mod, an unknown SPT version, and the mods ``import_list`` installs and the
diff it reports for a list handed as content instead of as a file.
"""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx

from fixture_types import MakeArchive
from forge_install_env import (
    SPT_CONSTRAINT,
    SPT_VERSION,
    dep_node,
    echo_dependencies,
    mock_download,
    package_archive,
    seed_target,
    seed_target_without_fetch_versions,
)
from forge_json import mod_json_full, owner_json, page_envelope
from sptmod.core import services
from sptmod.core.errors import ArchiveError, NotAGameDirError
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient
from sptmod.core.models import PlanEntry
from sptmod.core.repo import Repo

# --- plan_mod_install -------------------------------------------------------


def test_plan_mod_install_orders_dependency_before_the_requested_mod(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If plan_mod_install's body is `pass`, this fails: returns None instead
    of an InstallPlan with the dependency ordered before the target."""
    client = seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=101,
        name="Target Mod",
        version="2.0.0",
        version_id=1010,
        link="https://dl.example/target-2.0.0.zip",
        content_length=1000,
    )
    echo_dependencies(
        respx_router,
        [
            dep_node(
                mod_id=102,
                name="Dep Mod",
                version="1.0.0",
                version_id=1020,
                link="https://dl.example/dep-1.0.0.zip",
                content_length=500,
            )
        ],
    )

    plan = services.plan_mod_install(clean_repo.path, "Target Mod", "2.0.0", db_path=db_path, client=client)

    assert plan.spt_version == SPT_VERSION
    assert plan.entries == [
        PlanEntry(
            mod_id=102,
            name="Dep Mod",
            guid=None,
            version="1.0.0",
            version_id=1020,
            link="https://dl.example/dep-1.0.0.zip",
            content_length=500,
            is_dependency=True,
        ),
        PlanEntry(
            mod_id=101,
            name="Target Mod",
            guid="com.example.mod",
            version="2.0.0",
            version_id=1010,
            link="https://dl.example/target-2.0.0.zip",
            content_length=1000,
            is_dependency=False,
        ),
    ]


def test_plan_mod_install_resolves_a_link_missing_from_a_sync_only_catalog(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """``sync`` alone caches only the version *summary* (no link); the plan
    must still carry a usable download link -- the service must call
    ``fetch_versions`` itself for entries whose cached link is ``None``.
    """
    client = seed_target_without_fetch_versions(
        client_factory,
        respx_router,
        db_path,
        mod_id=901,
        name="Link Mod",
        version="1.0.0",
        version_id=9010,
        link="https://dl.example/link-mod-1.0.0.zip",
        content_length=800,
    )
    echo_dependencies(respx_router, [])

    plan = services.plan_mod_install(clean_repo.path, "Link Mod", "1.0.0", db_path=db_path, client=client)

    assert plan.entries[0].link == "https://dl.example/link-mod-1.0.0.zip"
    assert plan.entries[0].content_length == 800


def test_install_mod_succeeds_when_the_catalog_was_only_synced(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """install_mod resolves a missing download link via fetch_versions."""
    archive = package_archive(make_archive, "LinkMod", "Link Mod", "1.0.0")
    link = "https://dl.example/link-mod-1.0.0.zip"
    content_length = len(archive.read_bytes())
    client = seed_target_without_fetch_versions(
        client_factory,
        respx_router,
        db_path,
        mod_id=902,
        name="Link Mod",
        version="1.0.0",
        version_id=9020,
        link=link,
        content_length=content_length,
    )
    echo_dependencies(respx_router, [])
    mock_download(respx_router, link, archive)

    result = services.install_mod(clean_repo.path, "Link Mod", "1.0.0", db_path=db_path, client=client)

    assert [entry.mod.name for entry in result.installed] == ["Link Mod"]
    assert (clean_repo.path / "SPT/user/mods/LinkMod/package.json").is_file()


def test_install_mod_raises_archive_error_when_forge_no_longer_lists_the_version(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """The cached version *summary* never carries a link, so _with_link must
    call fetch_versions to resolve one; when Forge's full /mod/{id}/versions
    listing no longer contains the exact cached version id (deleted between
    sync and install), `_with_link`'s `if fetched is None: return entry`
    leaves the entry link-less. If that guard were removed (KeyError/index
    into an empty match) or `_download_entry`'s `if not link: raise
    ArchiveError(...)` guard were removed, this fails: a crash instead of a
    clear ArchiveError, or an attempted empty download."""
    mod = mod_json_full(
        id=910,
        name="GhostLink",
        slug="ghostlink",
        owner=owner_json(id=9100, name="Alice"),
        detail_url=f"{DEFAULT_BASE_URL}/mods/ghostlink",
        versions=[
            {
                "id": 9100,
                "version": "1.0.0",
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)
    empty_versions = page_envelope([], current_page=1, last_page=1, total=0, next_url=None)
    respx_router.get("/mod/910/versions").mock(return_value=httpx.Response(200, json=empty_versions))
    echo_dependencies(respx_router, [])

    with pytest.raises(ArchiveError):
        services.install_mod(clean_repo.path, "GhostLink", "1.0.0", db_path=db_path, client=client)


def test_install_mod_succeeds_when_the_download_link_ends_in_a_bare_version(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
) -> None:
    """Regression for the WTT - Content Backport bug: a Forge download link
    whose final path segment is a bare version number -- no file extension,
    and (as here) no Content-Disposition header to override it -- becomes the
    local file name via download()'s URL-path fallback. Installation must
    still succeed: extract() detects the archive format from magic bytes
    rather than trusting that name.
    """
    archive = package_archive(make_archive, "BareVersionMod", "Bare Version Mod", "2.0.1")
    link = "https://dl.example/2.0.1"
    content_length = len(archive.read_bytes())
    client = seed_target_without_fetch_versions(
        client_factory,
        respx_router,
        db_path,
        mod_id=903,
        name="Bare Version Mod",
        version="2.0.1",
        version_id=9030,
        link=link,
        content_length=content_length,
    )
    echo_dependencies(respx_router, [])
    mock_download(respx_router, link, archive)

    result = services.install_mod(clean_repo.path, "Bare Version Mod", "2.0.1", db_path=db_path, client=client)

    assert [entry.mod.name for entry in result.installed] == ["Bare Version Mod"]
    assert (clean_repo.path / "SPT/user/mods/BareVersionMod/package.json").is_file()


def test_plan_mod_install_unknown_mod_raises_no_compatible_version_error(
    clean_repo: Repo, client_factory: Callable[..., ForgeClient], db_path: Path
) -> None:
    """If plan_mod_install's body is `pass`, this fails: no exception raised
    for a mod absent from the (empty) catalog."""
    client = client_factory()

    with pytest.raises(NoCompatibleVersionError):
        services.plan_mod_install(clean_repo.path, "Nonexistent Mod", db_path=db_path, client=client)


def test_plan_mod_install_raises_not_a_game_dir_when_spt_version_unknown(
    clean_repo: Repo, client_factory: Callable[..., ForgeClient], db_path: Path
) -> None:
    """If plan_mod_install's body is `pass`, this fails: no exception raised
    when the game dir's core.json carries no readable SPT version."""
    (clean_repo.path / "SPT/SPT_Data/configs/core.json").write_text("not json", encoding="utf-8")
    client = client_factory()

    with pytest.raises(NotAGameDirError):
        services.plan_mod_install(clean_repo.path, "Target Mod", db_path=db_path, client=client)



# --- import_list: apply and content ------------------------------------


def test_import_list_apply_installs_exactly_the_missing_mods(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    make_archive: MakeArchive,
    tmp_path: Path,
) -> None:
    """If import_list's apply path is `pass`/unimplemented, this fails: the
    missing mod is never installed and diff.installed stays empty."""
    archive = package_archive(make_archive, "Import Target", "Import Target", "3.0.0")
    length = mock_download(respx_router, "https://dl.example/import-target-3.0.0.zip", archive)
    client = seed_target(
        client_factory,
        respx_router,
        db_path,
        mod_id=601,
        name="Import Target",
        version="3.0.0",
        version_id=6010,
        link="https://dl.example/import-target-3.0.0.zip",
        content_length=length,
    )
    echo_dependencies(respx_router, [])
    list_file = tmp_path / "mods.txt"
    list_file.write_text("Import Target 3.0.0\n", encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file=list_file, apply=True, db_path=db_path, client=client)

    assert diff.missing == [services.ImportEntry(name="Import Target", wanted_version="3.0.0")]
    assert not diff.different
    assert not diff.extra
    assert len(diff.installed) == 1
    assert [entry.name for entry in diff.installed[0].plan.entries] == ["Import Target"]
    assert diff.installed[0].installed[0].mod.name == "Import Target"
    assert diff.installed[0].installed[0].mod.version == "3.0.0"

    installed_names = [mod.name for mod in services.list_mods(clean_repo.path)]
    assert installed_names == ["Import Target"]


def test_import_list_content_parameter_yields_the_same_diff_as_a_file(clean_repo: Repo, tmp_path: Path) -> None:
    """import_list with content= and list_file= parameters produce identical results.

    Parsing "SomeMod 1.0.0\\n" via content= produces the same ImportDiff as parsing
    from a file; both report missing=[ImportEntry(name='SomeMod', wanted_version='1.0.0')].
    """
    text = "SomeMod 1.0.0\n"
    list_file = tmp_path / "mods.txt"
    list_file.write_text(text, encoding="utf-8")

    from_file = services.import_list(clean_repo.path, list_file=list_file)
    from_content = services.import_list(clean_repo.path, content=text)

    assert from_content == from_file
    assert from_content.missing == [services.ImportEntry(name="SomeMod", wanted_version="1.0.0")]
