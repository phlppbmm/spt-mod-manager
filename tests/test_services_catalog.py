"""Tests for the catalog service glue.

Covers ``sync_catalog``, ``search_catalog`` and ``browse_catalog`` in
``sptmod.core.services``, plus the ``sync``/``search``/``browse`` CLI
commands built on top of them.
"""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from conftest import clean_repo_at, forge_trailers
from fixture_types import InstallCommit
from forge_json import mod_json_full, owner_json, page_envelope
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import CatalogUnavailableError, ForgeUnavailableError
from sptmod.core.forge.catalog import Catalog, CatalogEntry, SyncResult
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient

runner = CliRunner()

# ForgeUnavailableError's own CLI exit code; asserted only here.
_FORGE_UNAVAILABLE_EXIT_CODE = 14


def _mod_json(mod_id: int, name: str, author: str, version: str, spt_constraint: str) -> dict[str, object]:
    """Build a Forge API mod envelope for a single version.

    Creates a complete mod JSON structure as returned by the Forge API,
    suitable for mocking API responses in tests.
    """
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
    """Return HTTP 200 with mods as page 1 of 1, total matching the list length.

    Wraps the mod list in a Forge API pagination envelope (mods key, current_page=1,
    last_page=1, total=len(mods), next_url=None) for use in respx mocking.
    """
    return httpx.Response(
        200,
        json=page_envelope(mods, current_page=1, last_page=1, total=len(mods), next_url=None),
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


# --- sync_catalog ---------------------------------------------------------


def test_sync_catalog_syncs_via_the_injected_client_into_the_injected_db(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
) -> None:
    """If sync_catalog's body is `pass`, this fails: result is None, not a
    SyncResult, and the reopened db_path has no cached mod."""
    clean_repo_at(fake_game_dir)
    respx_router.get("/mods").mock(return_value=_one_page([_mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0")]))
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
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
) -> None:
    """If sync_catalog's body is `pass`, this fails: no exception is raised."""
    clean_repo_at(fake_game_dir)
    respx_router.get("/mods").mock(side_effect=httpx.ConnectError("boom"))
    client = client_factory()

    with pytest.raises(ForgeUnavailableError):
        services.sync_catalog(fake_game_dir, db_path=db_path, client=client)


# --- search_catalog ---------------------------------------------------------


def test_search_catalog_classifies_installed_and_available_mods(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    install_commit: InstallCommit,
) -> None:
    """If search_catalog's body is `pass`, this fails: returns None instead of
    an entry list with the joined installed state."""
    repo = clean_repo_at(fake_game_dir)
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
    install_commit(repo, "Alpha Mod", "1.0.0", mod_type="server", **forge_trailers("1"))

    results = services.search_catalog(fake_game_dir, "", db_path=db_path)

    by_name = {entry.mod.name: entry for entry in results}
    assert by_name["Alpha Mod"].state == "installed"
    assert by_name["Alpha Mod"].installed_version == "1.0.0"
    assert by_name["Beta Mod"].state == "available"
    assert by_name["Beta Mod"].installed_version is None


def test_search_catalog_matches_query_against_name_and_author(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
) -> None:
    """If search_catalog's body is `pass`, this fails: returns None instead of
    the single name-matching entry."""
    clean_repo_at(fake_game_dir)
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


def _search_compatible(path: Path, db_path: Path) -> list[CatalogEntry]:
    """Search the whole catalog, keeping only mods the game directory can run."""
    return services.search_catalog(path, "", compatible_only=True, db_path=db_path)


def _browse_compatible(path: Path, db_path: Path) -> list[CatalogEntry]:
    """Browse the whole catalog, keeping only mods the game directory can run."""
    return services.browse_catalog(path, "all", compatible_only=True, db_path=db_path)


@pytest.mark.parametrize(
    "call",
    [_search_compatible, _browse_compatible],
    ids=["search_catalog", "browse_catalog"],
)
def test_compatible_only_drops_mods_the_installed_spt_version_cannot_run(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    call: Callable[[Path, Path], list[CatalogEntry]],
) -> None:
    """Searching and browsing drop an incompatible mod the same way.

    fake_game_dir's core.json names SPT 4.1.0, so Beta Mod's only version
    (``^3.8.0``) does not fit, while Alpha Mod's ``^4.0.0`` does. Both entry
    points share the compatibility filter, so both have to hide Beta Mod.
    """
    clean_repo_at(fake_game_dir)
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

    results = call(fake_game_dir, db_path)

    assert [entry.mod.name for entry in results] == ["Alpha Mod"]


# --- browse_catalog ---------------------------------------------------------


@pytest.mark.parametrize(
    ("browse_filter", "expected"),
    [
        pytest.param(
            "all",
            [
                ("Alpha Mod", "installed"),
                ("Beta Mod", "available"),
                ("Gamma Mod", "update_available"),
            ],
            id="all",
        ),
        pytest.param(
            "installed",
            [("Alpha Mod", "installed"), ("Gamma Mod", "update_available")],
            id="installed",
        ),
        pytest.param("available", [("Beta Mod", "available")], id="available"),
    ],
)
def test_browse_catalog_filter_selects_the_entries_of_that_list(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    install_commit: InstallCommit,
    browse_filter: services.BrowseFilter,
    expected: list[tuple[str, str]],
) -> None:
    """browse_catalog returns the entries of one frontend list, ordered by name.

    The cache holds three mods: Alpha Mod is installed at the cached version,
    Gamma Mod is installed at an older one than the cache holds, and Beta Mod
    was never installed. ``all`` keeps every one of them, ``installed`` keeps
    the two that match an active mod -- including the one with an update
    waiting -- and ``available`` keeps only the rest.
    """
    repo = clean_repo_at(fake_game_dir)
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
    install_commit(repo, "Alpha Mod", "1.0.0", mod_type="server", **forge_trailers("1"))
    install_commit(
        repo,
        "Gamma Mod",
        "1.0.0",
        mod_type="server",
        minute=2,
        **forge_trailers("3"),
    )

    results = services.browse_catalog(fake_game_dir, browse_filter, db_path=db_path)

    assert [(entry.mod.name, entry.state) for entry in results] == expected


def test_browse_catalog_hides_versions_that_are_not_installable(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
) -> None:
    """Without compatible_only, Beta Mod stays in the list, but its only
    version (^3.8.0) is no version the installer could resolve for the game
    dir's SPT 4.1.0, so no version is offered for it."""
    clean_repo_at(fake_game_dir)
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

    results = services.browse_catalog(fake_game_dir, "all", db_path=db_path)

    assert [entry.mod.name for entry in results] == ["Alpha Mod", "Beta Mod"]
    beta = results[1]
    assert beta.latest_version is None
    assert beta.state == "available"


def _offered_versions(entries: list[CatalogEntry]) -> list[tuple[str, str | None]]:
    """Return the version each entry offers, by mod name, None where none is."""
    return [
        (entry.mod.name, None if entry.latest_version is None else entry.latest_version.version)
        for entry in entries
    ]


@pytest.mark.parametrize(
    ("spt_version", "expected"),
    [
        pytest.param(
            None,
            [("Alpha Mod", "1.0.0"), ("Beta Mod", None)],
            id="the_version_of_the_game_directory",
        ),
        pytest.param(
            "",
            [("Alpha Mod", "1.0.0"), ("Beta Mod", None)],
            id="an_empty_version_falls_back_to_the_installed_one",
        ),
        pytest.param(
            "3.8.0",
            [("Alpha Mod", None), ("Beta Mod", "2.0.0")],
            id="a_version_the_user_picked",
        ),
    ],
)
def test_browse_catalog_judges_the_entries_against_the_chosen_spt_version(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    spt_version: str | None,
    expected: list[tuple[str, str | None]],
) -> None:
    """The game directory runs SPT 4.1.0, which only Alpha Mod's ``^4.0.0``
    fits, while Beta Mod's ``^3.8.0`` fits SPT 3.8.0 instead. Browsing for
    3.8.0 must therefore offer Beta Mod's version and none for Alpha Mod --
    the exact opposite of what the installed version yields -- because the
    filter exists to show what a version the user has not installed yet could
    run. Omitting the version keeps judging against the installed one, and so
    does the empty string the frontend sends for "no choice made" -- an empty
    version is no version to judge against.
    """
    clean_repo_at(fake_game_dir)
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
        fake_game_dir, "all", spt_version=spt_version, db_path=db_path
    )

    assert _offered_versions(results) == expected


@pytest.mark.parametrize(
    ("spt_version", "expected"),
    [
        pytest.param(
            None,
            [("Alpha Mod", "1.0.0"), ("Beta Mod", "2.0.0")],
            id="no_version_to_judge_against",
        ),
        pytest.param(
            "3.8.0",
            [("Alpha Mod", None), ("Beta Mod", "2.0.0")],
            id="a_version_the_user_picked",
        ),
    ],
)
def test_browse_catalog_judges_against_a_chosen_version_without_an_installed_one(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    spt_version: str | None,
    expected: list[tuple[str, str | None]],
) -> None:
    """The game directory names no SPT version at all, so nothing is known to
    judge against and every mod is offered its newest version. A version the
    user picked has to take over that judgement anyway -- otherwise the filter
    would stay dead exactly where it helps most, before the first install.
    """
    core = fake_game_dir / "SPT" / "SPT_Data" / "configs" / "core.json"
    core.write_text(json.dumps({"someOtherSetting": True}), encoding="utf-8")
    clean_repo_at(fake_game_dir)
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
        fake_game_dir, "all", spt_version=spt_version, db_path=db_path
    )

    assert _offered_versions(results) == expected


def test_browse_catalog_keeps_only_the_mods_the_chosen_version_can_run(
    fake_game_dir: Path,
    db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
) -> None:
    """Asking for only the compatible mods judges them against the picked
    version too: Alpha Mod, which the installed SPT 4.1.0 runs, is dropped for
    SPT 3.8.0 and Beta Mod takes its place -- both options have to mean the
    same version, or the list and its filter would disagree.
    """
    clean_repo_at(fake_game_dir)
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
        fake_game_dir, "all", True, spt_version="3.8.0", db_path=db_path
    )

    assert [entry.mod.name for entry in results] == ["Beta Mod"]


# --- CLI ---------------------------------------------------------


def test_cli_sync_exits_zero_and_reports_counts(
    fake_game_dir: Path,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the sync command's handler is `pass`, this fails: exit code stays
    the click default of 0 with no matching report on stdout."""
    clean_repo_at(fake_game_dir)
    respx_router.get("/mods").mock(return_value=_one_page([_mod_json(1, "Alpha Mod", "Alice", "1.0.0", "^4.0.0")]))
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "1 new and 0 updated mods" in result.stdout


def test_cli_search_and_browse_render_after_a_sync(
    fake_game_dir: Path,
    isolated_default_db_path: Path,
    respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    install_commit: InstallCommit,
) -> None:
    """If the search/browse handlers are `pass`, this fails: neither table
    would contain the synced and installed mod's name."""
    repo = clean_repo_at(fake_game_dir)
    respx_router.get("/mods").mock(return_value=_one_page([_mod_json(1, "Cool Mod", "Someone", "1.0.0", "^4.0.0")]))
    monkeypatch.chdir(fake_game_dir)
    sync_result = runner.invoke(app, ["sync"])
    assert sync_result.exit_code == 0
    install_commit(repo, "Cool Mod", "1.0.0", mod_type="server", **forge_trailers("1"))

    search_result = runner.invoke(app, ["search", "cool"])
    browse_result = runner.invoke(app, ["browse", "--installed"])

    assert search_result.exit_code == 0
    assert "Cool Mod" in search_result.stdout
    assert browse_result.exit_code == 0
    assert "Cool Mod" in browse_result.stdout


def test_cli_browse_rejects_more_than_one_filter_flag(fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If _browse_filter's body is `pass` (returns None, no raise), Click
    still exits 0 since no BadParameter is raised, so this fails."""
    clean_repo_at(fake_game_dir)
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
    unhandled.

    Zeroes out the retry backoff so the CLI's un-injected default
    ForgeClient does not spend real wall-clock time retrying.
    """
    monkeypatch.setattr("sptmod.core.forge.client.BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr("sptmod.core.forge.client.BACKOFF_MAX_SECONDS", 0.0)
    clean_repo_at(fake_game_dir)
    respx_router.get("/mods").mock(side_effect=httpx.ConnectError("boom"))
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == _FORGE_UNAVAILABLE_EXIT_CODE


# --- CatalogUnavailableError ------------------------------------------------
# An unwritable/uncreatable catalog cache directory must degrade to a typed
# error, not a raw crash (spt-mod-manager-r3a).


class _ClosedClient(ForgeClient):
    """A ``ForgeClient`` whose HTTP transport is closed on construction.

    ``sync_catalog`` must fail on the unwritable catalog directory long
    before it uses the client, so a closed one is enough here -- and it
    guarantees that no request can escape to the real Forge.
    """

    def __init__(self) -> None:
        super().__init__()
        self.close()


def _blocked_db_path(tmp_path: Path) -> Path:
    """A ``db_path`` whose parent cannot be created: a file sits in its place."""
    blocked = tmp_path / "blocked-config-dir"
    blocked.write_text("not a directory", encoding="utf-8")
    return blocked / "catalog.db"


def _search_catalog(path: Path, db: Path) -> object:
    return services.search_catalog(path, query="", db_path=db)


def _browse_catalog(path: Path, db: Path) -> object:
    return services.browse_catalog(path, db_path=db)


def _sync_catalog(path: Path, db: Path) -> object:
    # A closed client is enough: Catalog(db_path) must fail before the client
    # is ever used to talk to Forge.
    return services.sync_catalog(path, db_path=db, client=_ClosedClient())


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(_search_catalog, id="search_catalog"),
        pytest.param(_browse_catalog, id="browse_catalog"),
        pytest.param(_sync_catalog, id="sync_catalog"),
    ],
)
def test_catalog_reads_raise_catalog_unavailable_not_os_error(
    call: Callable[[Path, Path], object], fake_game_dir: Path, tmp_path: Path
) -> None:
    """search_catalog, browse_catalog and sync_catalog raise CatalogUnavailableError.

    Checks that sync_catalog, search_catalog, and browse_catalog all convert
    filesystem errors (when the catalog directory is blocked) to the typed
    CatalogUnavailableError rather than raising raw OSError.
    """
    clean_repo_at(fake_game_dir)

    with pytest.raises(CatalogUnavailableError):
        call(fake_game_dir, _blocked_db_path(tmp_path))
