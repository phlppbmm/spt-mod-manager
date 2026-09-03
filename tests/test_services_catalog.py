"""Tests for the catalog service glue.

Covers ``sync_catalog``, ``search_catalog`` and ``browse_catalog`` in
``sptmod.core.services``, plus the ``sync``/``search``/``browse`` CLI
commands built on top of them.
"""

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from sptmod.cli import EXIT_CODES, app
from sptmod.core import services
from sptmod.core.errors import CatalogUnavailableError, ForgeUnavailableError
from sptmod.core.forge.catalog import DB_FILE_NAME, Catalog, SyncResult
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo
from test_forge_catalog_fixtures import (
    client_factory,
    db_path,
    fake_clock,
    mod_json_full,
    owner_json,
    page_envelope,
    recording_sleep,
    respx_router,
)

__all__ = [
    "client_factory",
    "db_path",
    "fake_clock",
    "recording_sleep",
    "respx_router",
]

runner = CliRunner()

# RED contract: ForgeUnavailableError is not yet in cli.EXIT_CODES. Pinned
# here so the CLI test fails clearly against the stub until it is wired.
_FORGE_UNAVAILABLE_EXIT_CODE = 14


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(detect_layout(path))
    repo.commit(
        "chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None
    )
    return repo


def _mod_json(
    mod_id: int, name: str, author: str, version: str, spt_constraint: str
) -> dict[str, object]:
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name=author),
        detail_url=f"{DEFAULT_BASE_URL}/mods/{name.lower().replace(' ', '-')}",
        versions=[
            {
                "id": mod_id * 100,
                "version": version,
                "spt_version_constraint": spt_constraint,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )


def _one_page(mods: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200,
        json=page_envelope(
            mods, current_page=1, last_page=1, total=len(mods), next_url=None
        ),
    )


def _seed(
    db_path: Path,
    client: ForgeClient,
    respx_router: respx.MockRouter,
    mods: list[dict[str, object]],
) -> None:
    """Populate ``db_path`` through the real sync path, mocking one /mods page."""
    respx_router.get("/mods").mock(return_value=_one_page(mods))
    with Catalog(db_path) as cat:
        cat.sync(client, full=True)


@pytest.fixture
def isolated_default_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``default_db_path()`` at an isolated location for CLI tests.

    The CLI commands never pass ``db_path`` explicitly, so they resolve
    through ``forge.catalog.default_db_path()``, which asks platformdirs for
    the user's config directory. Patching that call keeps CLI catalog tests
    off the developer's real config directory.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        "sptmod.core.forge.catalog.platformdirs.user_config_path",
        lambda *args, **kwargs: config_dir,
    )
    return config_dir / DB_FILE_NAME


# --- sync_catalog ---------------------------------------------------------


def test_sync_catalog_syncs_via_the_injected_client_into_the_injected_db(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If sync_catalog's body is `pass`, this fails: result is None, not a
    SyncResult, and the reopened db_path has no cached mod."""
    _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(
        return_value=_one_page([_mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0")])
    )
    client = client_factory()

    result = services.sync_catalog(fake_game_dir, db_path=db_path, client=client)

    assert isinstance(result, SyncResult)
    assert result.new_mods == 1
    assert result.pages == 1

    with Catalog(db_path) as reopened:
        cached = reopened.get_mod(1)
    assert cached is not None
    assert cached.name == "Alpha Mod"


def test_sync_catalog_raises_forge_unavailable_on_a_network_error(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If sync_catalog's body is `pass`, this fails: no exception is raised."""
    _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(side_effect=httpx.ConnectError("boom"))
    client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        services.sync_catalog(fake_game_dir, db_path=db_path, client=client)


# --- search_catalog ---------------------------------------------------------


def test_search_catalog_classifies_installed_and_available_mods(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    install_commit,
) -> None:
    """If search_catalog's body is `pass`, this fails: returns None instead of
    an entry list with the joined installed state."""
    repo = _clean_repo(fake_game_dir)
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
        ],
    )
    install_commit(
        repo, "Alpha Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"}
    )

    results = services.search_catalog(fake_game_dir, "", db_path=db_path)

    by_name = {entry.mod.name: entry for entry in results}
    assert by_name["Alpha Mod"].state == "installed"
    assert by_name["Alpha Mod"].installed_version == "1.0.0"
    assert by_name["Beta Mod"].state == "available"
    assert by_name["Beta Mod"].installed_version is None


def test_search_catalog_matches_query_against_name_and_author(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If search_catalog's body is `pass`, this fails: returns None instead of
    the single name-matching entry."""
    _clean_repo(fake_game_dir)
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
        ],
    )

    results = services.search_catalog(fake_game_dir, "alpha", db_path=db_path)

    assert [entry.mod.name for entry in results] == ["Alpha Mod"]


def test_search_catalog_compatible_only_filters_by_the_game_dirs_spt_version(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If search_catalog's body is `pass`, this fails: Beta Mod (^3.8.0, not
    compatible with core.json's sptVersion 4.1.0) would not be dropped."""
    _clean_repo(fake_game_dir)  # fake_game_dir's core.json sptVersion is "4.1.0"
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
        ],
    )

    results = services.search_catalog(
        fake_game_dir, "", compatible_only=True, db_path=db_path
    )

    assert [entry.mod.name for entry in results] == ["Alpha Mod"]


# --- browse_catalog ---------------------------------------------------------


def test_browse_catalog_all_returns_every_cached_mod_ordered_by_name(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    install_commit,
) -> None:
    """If browse_catalog's body is `pass`, this fails: returns None instead of
    both cached mods ordered by name."""
    repo = _clean_repo(fake_game_dir)
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
        ],
    )
    install_commit(
        repo, "Alpha Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"}
    )

    results = services.browse_catalog(fake_game_dir, "all", db_path=db_path)

    assert [entry.mod.name for entry in results] == ["Alpha Mod", "Beta Mod"]


def test_browse_catalog_installed_filter_keeps_installed_and_update_available(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    install_commit,
) -> None:
    """If browse_catalog's body is `pass`, this fails: Beta Mod (never
    installed) would not be excluded from the "installed" filter."""
    repo = _clean_repo(fake_game_dir)
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
            # Gamma Mod: cache holds 2.0.0 but the game dir has 1.0.0 installed.
            _mod_json(3, "Gamma Mod", "Carol", "2.0.0", "^4.0.0"),
        ],
    )
    install_commit(
        repo, "Alpha Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"}
    )
    install_commit(
        repo,
        "Gamma Mod",
        "1.0.0",
        mod_type="server",
        minute=2,
        **{"Mod-Forge-Id": "3"},
    )

    results = services.browse_catalog(fake_game_dir, "installed", db_path=db_path)

    by_name = {entry.mod.name: entry.state for entry in results}
    assert by_name == {"Alpha Mod": "installed", "Gamma Mod": "update_available"}


def test_browse_catalog_available_filter_keeps_only_non_installed_mods(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
    install_commit,
) -> None:
    """If browse_catalog's body is `pass`, this fails: the installed Alpha Mod
    would not be excluded from the "available" filter."""
    repo = _clean_repo(fake_game_dir)
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
        ],
    )
    install_commit(
        repo, "Alpha Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"}
    )

    results = services.browse_catalog(fake_game_dir, "available", db_path=db_path)

    assert [entry.mod.name for entry in results] == ["Beta Mod"]


def test_browse_catalog_compatible_only_drops_incompatible_mods(
    fake_game_dir: Path,
    db_path: Path,
    client_factory,
    respx_router: respx.MockRouter,
) -> None:
    """If browse_catalog's body is `pass`, this fails: Beta Mod (^3.8.0) would
    not be dropped for the game dir's sptVersion 4.1.0."""
    _clean_repo(fake_game_dir)
    client = client_factory()
    _seed(
        db_path,
        client,
        respx_router,
        [
            _mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0"),
            _mod_json(2, "Beta Mod", "Bob", "2.0.0", "^3.8.0"),
        ],
    )

    results = services.browse_catalog(
        fake_game_dir, "all", compatible_only=True, db_path=db_path
    )

    assert [entry.mod.name for entry in results] == ["Alpha Mod"]


# --- CLI ---------------------------------------------------------


def test_cli_sync_exits_zero_and_reports_counts(
    fake_game_dir: Path,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the sync command's handler is `pass`, this fails: exit code stays
    the click default of 0 with no matching report on stdout."""
    _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(
        return_value=_one_page([_mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0")])
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "1 new and 0 updated mods" in result.stdout


def test_cli_search_and_browse_render_after_a_sync(
    fake_game_dir: Path,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    install_commit,
) -> None:
    """If the search/browse handlers are `pass`, this fails: neither table
    would contain the synced and installed mod's name."""
    repo = _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(
        return_value=_one_page([_mod_json(1, "Cool Mod", "Someone", "1.0.0", "^4.0.0")])
    )
    monkeypatch.chdir(fake_game_dir)
    sync_result = runner.invoke(app, ["sync"])
    assert sync_result.exit_code == 0
    install_commit(
        repo, "Cool Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"}
    )

    search_result = runner.invoke(app, ["search", "cool"])
    browse_result = runner.invoke(app, ["browse", "--installed"])

    assert search_result.exit_code == 0
    assert "Cool Mod" in search_result.stdout
    assert browse_result.exit_code == 0
    assert "Cool Mod" in browse_result.stdout


def test_cli_browse_rejects_more_than_one_filter_flag(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If _browse_filter's body is `pass` (returns None, no raise), Click
    still exits 0 since no BadParameter is raised, so this fails."""
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["browse", "--installed", "--available"])

    assert result.exit_code == 2


def test_cli_sync_maps_forge_unavailable_to_its_own_exit_code(
    fake_game_dir: Path,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the exit-code contract for ForgeUnavailableError: it must get its
    own code (14), not fall through to GENERIC_EXIT_CODE (1) or crash
    unhandled. Fails both while sync_catalog is a stub and while
    ForgeUnavailableError is absent from cli.EXIT_CODES.

    Zeroes out the retry backoff so the CLI's un-injected default
    ForgeClient does not spend real wall-clock time retrying.
    """
    monkeypatch.setattr("sptmod.core.forge.client.BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("sptmod.core.forge.client.BACKOFF_MAX_SECONDS", 0.0)
    _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(side_effect=httpx.ConnectError("boom"))
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["sync"])

    assert EXIT_CODES.get(ForgeUnavailableError) == _FORGE_UNAVAILABLE_EXIT_CODE
    assert result.exit_code == _FORGE_UNAVAILABLE_EXIT_CODE


# --- CatalogUnavailableError ------------------------------------------------
# An unwritable/uncreatable catalog cache directory must degrade to a typed
# error, not a raw crash (spt-mod-manager-r3a).


def _blocked_db_path(tmp_path: Path) -> Path:
    """A ``db_path`` whose parent cannot be created: a file sits in its place."""
    blocked = tmp_path / "blocked-config-dir"
    blocked.write_text("not a directory", encoding="utf-8")
    return blocked / "catalog.db"


def test_search_catalog_raises_catalog_unavailable_not_os_error(
    fake_game_dir: Path, tmp_path: Path
) -> None:
    _clean_repo(fake_game_dir)

    with pytest.raises(CatalogUnavailableError):
        services.search_catalog(
            fake_game_dir, query="", db_path=_blocked_db_path(tmp_path)
        )


def test_browse_catalog_raises_catalog_unavailable_not_os_error(
    fake_game_dir: Path, tmp_path: Path
) -> None:
    _clean_repo(fake_game_dir)

    with pytest.raises(CatalogUnavailableError):
        services.browse_catalog(fake_game_dir, db_path=_blocked_db_path(tmp_path))


def test_sync_catalog_raises_catalog_unavailable_not_os_error(
    fake_game_dir: Path, tmp_path: Path
) -> None:
    # A dummy client is enough: Catalog(db_path) must fail before the client
    # is ever used to talk to Forge.
    _clean_repo(fake_game_dir)

    with pytest.raises(CatalogUnavailableError):
        services.sync_catalog(
            fake_game_dir, db_path=_blocked_db_path(tmp_path), client=object()
        )
