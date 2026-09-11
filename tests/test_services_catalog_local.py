"""Tests for the catalog entries of installed mods the Forge knows nothing about.

``browse_catalog`` joins the cached catalog with the installed mods, so a mod a
player built or adopted themselves -- there is no catalog row for it -- would be
missing from every list the desktop app renders from that call. These tests pin
the entries the service synthesises for such mods: what they carry, which mods
get one, how they are identified, and that nothing offers a Forge update for
them.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
import respx

from conftest import clean_repo_at, forge_trailers
from fixture_types import InstallCommit, JsonDict
from fixtures_forge import seed_catalog
from forge_json import mod_json_with_versions
from sptmod.core import services
from sptmod.core.forge.catalog import CatalogEntry
from sptmod.core.forge.client import ForgeClient

_CACHED_MOD = "Cached Mod"
_OWN_MOD = "My Own Mod"


def _seeded(db_path: Path, mods: list[JsonDict] | None = None) -> None:
    """Fill the catalog cache with one published mod, or with ``mods``."""
    seed_catalog(
        db_path,
        mods
        if mods is not None
        else [mod_json_with_versions(mod_id=11, name=_CACHED_MOD, constraints=["~4.1.0"])],
    )


def _entry(entries: list[CatalogEntry], name: str) -> CatalogEntry:
    """Return the entry named ``name``.

    Fails the test with a readable message instead of an IndexError when the
    list does not hold it.
    """
    matching = [entry for entry in entries if entry.mod.name == name]
    assert matching, f"no entry for {name!r} in {[entry.mod.name for entry in entries]}"
    return matching[0]


def test_an_installed_mod_without_a_catalog_row_gets_an_entry(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """If the synthesis were dropped, this fails: the mod the player built is
    absent from the list the desktop app renders, so it cannot be seen, disabled
    or removed there at all."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    own = _entry(entries, _OWN_MOD)
    assert own.local
    assert own.state == "installed"
    assert own.installed_version == "1.2.0"
    assert own.latest_version is None


def test_the_entry_of_an_own_mod_offers_no_forge_identity(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """Its id must never be taken for a Forge mod id: if it were positive, the
    desktop app would ask the Forge about a mod that does not exist there, and
    the row could collide with a real catalog row."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    own = _entry(services.browse_catalog(fake_game_dir, db_path=db_path), _OWN_MOD)

    assert own.mod.id < 0
    assert own.mod.detail_url == ""
    assert own.mod.thumbnail is None


def test_the_entry_of_an_own_mod_keeps_its_id_between_calls(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """The frontend keys its rows by the entry id, so an id that changed between
    two refreshes would tear the row down and rebuild it. If the id were derived
    from anything but the mod name, this fails with two different ids."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    first = _entry(services.browse_catalog(fake_game_dir, db_path=db_path), _OWN_MOD)
    second = _entry(services.browse_catalog(fake_game_dir, db_path=db_path), _OWN_MOD)

    assert first.mod.id == second.mod.id


def test_two_own_mods_get_two_different_ids(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """If two entries shared an id, the frontend would render one row for both and
    act on the wrong mod."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")
    install_commit(repo, "Another Own Mod", "0.1.0", mod_type="server", minute=2)

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    ids = [entry.mod.id for entry in entries if entry.local]
    assert len(ids) == len(set(ids))


def test_two_own_mods_spelled_the_same_way_keep_two_rows(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """Mod names are compared without regard to case, so two mods whose names
    differ only in their spelling land on the same derived id. If the entries did
    not step around each other, this fails: the two mods would share one id, and
    the desktop app would render one row for both and act on the wrong mod."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, "Own Mod", "1.0.0", mod_type="server")
    install_commit(repo, "OWN MOD", "2.0.0", mod_type="server", minute=2)

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    ids = [entry.mod.id for entry in entries if entry.local]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_a_disabled_mod_without_a_catalog_row_stays_in_the_list(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """Turning a mod off must not make it disappear: if the synthesis used the
    active mods only, this fails and the player could never turn their own mod
    back on from the desktop app."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")
    services.disable_mod(fake_game_dir, _OWN_MOD)

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert _entry(entries, _OWN_MOD).local


def test_a_removed_mod_gets_no_entry(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """A mod the player removed is gone: if every tracked mod were synthesised,
    this fails because the list would keep offering a mod with no files."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")
    services.remove_mod(fake_game_dir, _OWN_MOD)

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert [entry.mod.name for entry in entries if entry.local] == []


def test_a_mod_the_catalog_knows_by_name_gets_no_second_entry(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """If the cached rows were not consulted, this fails: every installed mod
    would appear twice, once from the catalog join and once as a local entry."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _CACHED_MOD, "1.0.0", mod_type="server")

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert [entry.mod.name for entry in entries] == [_CACHED_MOD]
    assert not _entry(entries, _CACHED_MOD).local


def test_a_mod_the_catalog_knows_by_forge_id_gets_no_second_entry(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """The trailer name of a mod regularly differs from its Forge display name,
    so the Forge id is the reliable link: if only names were compared, this fails
    with a duplicate row for a mod the catalog does hold."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(
        repo, "cached-mod-package", "1.0.0", mod_type="server", **forge_trailers("11")
    )

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert [entry.mod.name for entry in entries] == [_CACHED_MOD]


@pytest.mark.parametrize(
    "compatible_only",
    [pytest.param(False, id="every-mod"), pytest.param(True, id="compatible-only")],
)
def test_the_compatibility_filter_never_hides_an_own_mod(
    fake_game_dir: Path,
    db_path: Path,
    install_commit: InstallCommit,
    compatible_only: bool,
) -> None:
    """There is no published SPT constraint to judge a mod the player built by, so
    hiding it as incompatible would hide it forever. If the filter were applied to
    these entries, this fails with an empty list for the compatible case."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    entries = services.browse_catalog(
        fake_game_dir, "all", compatible_only, db_path=db_path
    )

    assert _entry(entries, _OWN_MOD).local


def test_an_own_mod_shows_up_in_the_installed_list(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """The desktop app opens on the installed list: if the entry were reported as
    ``available``, the mod would be filed under mods the player could install
    instead of under the ones they have."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    entries = services.browse_catalog(fake_game_dir, "installed", db_path=db_path)

    assert _entry(entries, _OWN_MOD).installed_version == "1.2.0"


def test_an_own_mod_shows_up_without_any_catalog_cache(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """A player who never synced the Forge still has to see their own mods: if the
    entries came from cached rows only, this fails with an empty list."""
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert [entry.mod.name for entry in entries] == [_OWN_MOD]


def test_updates_never_offer_a_forge_release_for_an_own_mod(
    fake_game_dir: Path,
    db_path: Path,
    install_commit: InstallCommit,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
) -> None:
    """The entry must not make the update check ask the Forge about the mod: it is
    reported as one the Forge does not identify, and the endpoint stays untouched.
    If the synthesised id leaked into the update path, this fails with a call for a
    mod id the Forge never issued."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")
    services.browse_catalog(fake_game_dir, db_path=db_path)
    updates = respx_router.get("/mods/updates")

    report = services.check_updates(fake_game_dir, db_path=db_path, client=client_factory())

    assert report.skipped == [_OWN_MOD]
    assert not report.available
    assert not updates.called


def test_search_reports_the_catalog_only(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """Searching is a search of the Forge catalog: if the local entries leaked into
    it, this fails because a mod the Forge never published would answer a catalog
    search for its name."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, _OWN_MOD, "1.2.0", mod_type="server")

    assert services.search_catalog(fake_game_dir, "Own", db_path=db_path) == []


def test_a_repo_without_a_clean_baseline_is_unaffected(
    fake_game_dir: Path, db_path: Path
) -> None:
    """An installation with no mods at all yields the cached rows and nothing
    else: if the synthesis ran over an empty list wrongly, this fails with a
    phantom entry."""
    clean_repo_at(fake_game_dir)
    _seeded(db_path)

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert [(entry.mod.name, entry.local) for entry in entries] == [(_CACHED_MOD, False)]


def test_an_adopted_mod_of_an_empty_repository_is_listed(
    fake_game_dir: Path, db_path: Path, install_commit: InstallCommit
) -> None:
    """The same rule covers hand-installed mods that were adopted, not only the
    ones built from a local source: nothing in the synthesis asks where a mod
    came from."""
    repo = clean_repo_at(fake_game_dir)
    _seeded(db_path)
    install_commit(repo, "Adopted Mod", "3.0.0", mod_type="client")

    entries = services.browse_catalog(fake_game_dir, db_path=db_path)

    assert _entry(entries, "Adopted Mod").local
