"""Tests for installing a mod a local source offers.

Covers ``install_local_source``: the newest build of the named mod lands in the
game directory, a build installed earlier is taken out before the new one goes
in -- whether or not the version changed -- a folder holding several mods
installs the one that was asked for, a folder that has built nothing is refused
in plain language, an installed mod records the folder it came from and is
paired with a build of that folder even after a rename, and -- the property this
whole feature stands on -- a mod installed this way is never linked to a Forge
mod, not even when the cached
catalog holds a mod of exactly that name and version. Every call passes the
``local_source_store`` fixture as its ``store_path``.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx

from fixture_types import ClientFactory
from fixtures_forge import seed_catalog
from forge_json import mod_json_with_versions
from localsource_builds import write_archive, write_build
from sptmod.core import services
from sptmod.core.errors import (
    ConflictError,
    LocalSourceNotBuiltError,
    LocalSourceNotFoundError,
)
from sptmod.core.repo import Repo

_OLD = datetime(2026, 9, 1, 12, 0, tzinfo=UTC).timestamp()
_NEW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC).timestamp()

_MOD_NAME = "MyMod"


def _registered(game_dir: Path, tmp_path: Path, store: Path) -> Path:
    """Create a project folder and register it as a local mod source."""
    folder = tmp_path / "projects" / _MOD_NAME
    folder.mkdir(parents=True)
    services.add_local_source(game_dir, folder, store_path=store)
    return folder


# --- installing a build -----------------------------------------------------


def test_install_local_source_installs_the_newest_build(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the body is `pass`, this fails: the mod's files are missing from the
    game directory and no mod is reported as installed."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    newest = write_build(folder, "MyMod-1.1.0.zip", mod_name=_MOD_NAME, version="1.1.0", mtime=_NEW)

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert result.mod.name == _MOD_NAME
    assert result.mod.version == "1.1.0"
    assert result.archive == newest
    assert not result.replaced
    assert (clean_repo.path / "SPT/user/mods/MyMod/package.json").is_file()


def test_a_rebuild_of_the_same_version_replaces_what_is_installed(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """While a mod is being developed the version string stays put and only the
    content changes. If anything compared versions -- reporting the mod as
    already installed, skipping the install as a no-op or demanding a forced
    install -- this fails: the game directory would still hold the previous
    build's file content."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(
        folder,
        "MyMod-2.1.0.zip",
        mod_name=_MOD_NAME,
        version="2.1.0",
        files={"SPT/user/mods/MyMod/mod.js": "first build"},
        mtime=_OLD,
    )
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    write_build(
        folder,
        "MyMod-2.1.0.zip",
        mod_name=_MOD_NAME,
        version="2.1.0",
        files={"SPT/user/mods/MyMod/mod.js": "second build"},
        mtime=_NEW,
    )

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert result.replaced
    assert result.mod.version == "2.1.0"
    assert (clean_repo.path / "SPT/user/mods/MyMod/mod.js").read_text(
        encoding="utf-8"
    ) == "second build"


def test_a_folder_installs_the_mod_it_was_asked_for(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A project folder builds several mods. If the install took the newest
    archive of the folder instead of the newest build of the named mod, this
    fails with the other mod installed -- the exact mix-up this model exists to
    prevent."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(
        folder, "DynamicGoons-2.1.0.zip", mod_name="DynamicGoons", version="2.1.0", mtime=_OLD
    )
    write_build(
        folder,
        "BossSkullTooltips-1.0.0.zip",
        mod_name="BossSkullTooltips",
        version="1.0.0",
        mtime=_NEW,
    )

    result = services.install_local_source(
        clean_repo.path, folder, "DynamicGoons", store_path=local_source_store
    )

    assert result.mod.name == "DynamicGoons"
    assert [mod.name for mod in services.list_mods(clean_repo.path)] == ["DynamicGoons"]


def test_rebuilding_another_mod_does_not_change_what_a_reinstall_installs(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The same mix-up through the reinstall path: the second mod is rebuilt last,
    so a folder resolved by the newest archive would reinstall that one over the
    first mod's name."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(
        folder, "DynamicGoons-2.1.0.zip", mod_name="DynamicGoons", version="2.1.0", mtime=_OLD
    )
    services.install_local_source(
        clean_repo.path, folder, "DynamicGoons", store_path=local_source_store
    )
    write_build(
        folder,
        "BossSkullTooltips-1.0.0.zip",
        mod_name="BossSkullTooltips",
        version="1.0.0",
        mtime=_NEW,
    )

    result = services.install_local_source(
        clean_repo.path, folder, "DynamicGoons", store_path=local_source_store
    )

    assert result.mod.name == "DynamicGoons"
    assert result.archive.name == "DynamicGoons-2.1.0.zip"


def test_a_mod_of_another_folder_is_no_target_of_this_one(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Each folder answers for its own mods only: if the name were looked up
    anywhere else, this fails because a folder would install a build it does not
    hold."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0")

    with pytest.raises(LocalSourceNotBuiltError, match="Stranger"):
        services.install_local_source(
            clean_repo.path, folder, "Stranger", store_path=local_source_store
        )


def test_installing_a_newer_build_replaces_the_previous_one_completely(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A rebuild is one step for the user and must not leave anything of the old
    build behind: if the mod were installed on top of itself, this fails because
    the file the new build no longer ships would still be there (or the install
    would be refused as a conflict)."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(
        folder,
        "MyMod-1.0.0.zip",
        mod_name=_MOD_NAME,
        version="1.0.0",
        files={"SPT/user/mods/MyMod/dropped.js": "old"},
        mtime=_OLD,
    )
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    write_build(
        folder,
        "MyMod-2.0.0.zip",
        mod_name=_MOD_NAME,
        version="2.0.0",
        files={"SPT/user/mods/MyMod/added.js": "new"},
        mtime=_NEW,
    )

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert result.replaced
    assert result.mod.version == "2.0.0"
    assert (clean_repo.path / "SPT/user/mods/MyMod/added.js").is_file()
    assert not (clean_repo.path / "SPT/user/mods/MyMod/dropped.js").exists()


def test_reinstalling_leaves_exactly_one_active_mod_of_that_name(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the removal and the install were not one step, this fails: the mod would
    be listed twice, or the removal would be the last word on it."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    write_build(folder, "MyMod-2.0.0.zip", mod_name=_MOD_NAME, version="2.0.0", mtime=_NEW)

    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    active = services.list_mods(clean_repo.path)
    assert [(mod.name, mod.version) for mod in active] == [(_MOD_NAME, "2.0.0")]


def test_installing_again_after_the_mod_was_removed_needs_no_removal(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the removal ran for a mod that is not active, this fails: removing an
    already removed mod raises instead of installing the build."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    services.remove_mod(clean_repo.path, _MOD_NAME)
    write_build(folder, "MyMod-2.0.0.zip", mod_name=_MOD_NAME, version="2.0.0", mtime=_NEW)

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert not result.replaced
    assert [mod.version for mod in services.list_mods(clean_repo.path)] == ["2.0.0"]


def test_installing_over_a_disabled_mod_needs_no_removal(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A disabled mod has no files in the game directory, so nothing has to be
    taken out: if the removal ran for it anyway, this fails because removing a
    mod that is not active raises."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    services.disable_mod(clean_repo.path, _MOD_NAME)
    write_build(folder, "MyMod-2.0.0.zip", mod_name=_MOD_NAME, version="2.0.0", mtime=_NEW)

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert not result.replaced
    assert result.mod.version == "2.0.0"


def _conflicting_mod(clean_repo: Repo, tmp_path: Path) -> None:
    """Install a second mod owning the plugin the rebuilt mod below also ships."""
    other = tmp_path / "other"
    other.mkdir()
    archive = write_archive(other, "Shared-1.0.0.zip", {"BepInEx/plugins/Shared.dll": "dll"})
    services.install_from_archive(clean_repo.path, archive)


def test_a_build_that_cannot_be_installed_leaves_the_mod_taken_out(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Pins what a failed reinstall leaves behind.

    The old build is taken out before the new one goes in, so an install that
    fails afterwards -- here because the new build ships a file another mod
    already owns -- leaves the mod removed rather than restoring the previous
    build. The player gets the failure, keeps every file in the repository's
    history, and installs again once the build is fixed; this test states that
    outcome so a change to it is a deliberate one.
    """
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    _conflicting_mod(clean_repo, tmp_path)
    write_build(
        folder,
        "MyMod-2.0.0.zip",
        mod_name=_MOD_NAME,
        version="2.0.0",
        files={"BepInEx/plugins/Shared.dll": "other dll"},
        mtime=_NEW,
    )

    with pytest.raises(ConflictError):
        services.install_local_source(
            clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
        )

    assert [mod.name for mod in services.list_mods(clean_repo.path)] == ["Shared"]
    assert not (clean_repo.path / "SPT/user/mods/MyMod/package.json").exists()


def test_the_folder_still_offers_the_mod_after_a_failed_reinstall(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The way back from the failure above is another install, so the folder has
    to keep offering the mod: if the failure changed what the folder offers, the
    player would be left with no way to put their mod back."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    _conflicting_mod(clean_repo, tmp_path)
    write_build(
        folder,
        "MyMod-2.0.0.zip",
        mod_name=_MOD_NAME,
        version="2.0.0",
        files={"BepInEx/plugins/Shared.dll": "other dll"},
        mtime=_NEW,
    )

    with pytest.raises(ConflictError):
        services.install_local_source(
            clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
        )

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)
    assert [offered.name for offered in listed[0].mods] == [_MOD_NAME]


def test_install_local_source_reports_progress_up_to_a_finished_install(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If progress were swallowed on the way through, this fails: the desktop app
    would show a frozen bar for the whole install."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME)
    calls: list[tuple[str, int, str]] = []

    services.install_local_source(
        clean_repo.path,
        folder,
        _MOD_NAME,
        progress=lambda step, percent, message: calls.append((step, percent, message)),
        store_path=local_source_store,
    )

    steps = [step for step, _percent, _message in calls]
    assert list(dict.fromkeys(steps)) == list(services.INSTALL_STEPS)
    assert calls[-1][1] == 100


# --- refusals ---------------------------------------------------------------


def test_a_folder_that_has_built_nothing_yet_is_refused_in_plain_language(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Registering a folder without a build is fine; installing from it is not.
    If it fell through to the install, this fails with an archive error about a
    path instead of a sentence naming the folder and its build folder."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)

    with pytest.raises(LocalSourceNotBuiltError, match=re.escape(str(folder))) as failure:
        services.install_local_source(
            clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
        )

    assert "build" in str(failure.value)


def test_installing_from_an_unregistered_folder_says_it_is_not_registered(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If any folder could be installed from, this fails: the desktop app and the
    command line would install from folders nobody registered."""
    folder = tmp_path / "projects" / "Stranger"
    folder.mkdir(parents=True)
    write_build(folder, "Stranger-1.0.0.zip", mod_name="Stranger")

    with pytest.raises(LocalSourceNotFoundError, match=re.escape(str(folder))):
        services.install_local_source(
            clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
        )


# --- the folder a mod came from ---------------------------------------------


def test_an_installed_mod_records_the_folder_it_came_from(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The folder is the only record of where such a mod came from: if the
    install kept the archive file name instead, this fails and removing the
    folder could not find its mods again."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0")

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert result.mod.source == str(folder.resolve())


def test_a_build_that_renamed_the_mod_replaces_the_mod_installed_before(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A mod under development gets renamed. If the install paired the build with
    an installed mod by name, this fails: the old name would stay installed next
    to the new one, twice the same mod."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    (folder / "build" / "MyMod-1.0.0.zip").unlink()
    write_build(folder, "Renamed-1.1.0.zip", mod_name="Renamed", version="1.1.0", mtime=_NEW)

    result = services.install_local_source(
        clean_repo.path, folder, "Renamed", store_path=local_source_store
    )

    assert result.replaced
    assert [mod.name for mod in services.list_mods(clean_repo.path)] == ["Renamed"]


def test_a_renamed_mod_is_listed_as_the_installed_state_of_its_build(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The listing pairs the same way the install does: if it went by name, this
    fails because the renamed build would read as not installed while its mod
    sits in the game directory."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0", mtime=_OLD)
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    (folder / "build" / "MyMod-1.0.0.zip").unlink()
    write_build(folder, "Renamed-1.1.0.zip", mod_name="Renamed", version="1.1.0", mtime=_NEW)

    sources = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    offered = sources[0].mods[0]
    assert offered.name == "Renamed"
    assert offered.mod is not None
    assert offered.mod.name == _MOD_NAME


def test_a_mod_of_the_same_name_from_elsewhere_is_no_build_of_this_folder(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Only what this folder installed is its own: if the listing paired by name
    alone, this fails because a mod installed from an archive would read as the
    installed state of this folder's build."""
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0")
    other = tmp_path / "downloads"
    other.mkdir()
    services.install_from_archive(
        clean_repo.path,
        write_build(other, "MyMod-9.9.9.zip", mod_name=_MOD_NAME, version="9.9.9"),
    )

    sources = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert sources[0].mods[0].mod is None
    assert sources[0].installed == []


# --- no Forge linkage -------------------------------------------------------


def _seed_matching_catalog(db_path: Path) -> None:
    """Cache a Forge mod whose name and version match the build below."""
    seed_catalog(db_path, [mod_json_with_versions(mod_id=901, name=_MOD_NAME, constraints=["~4.1.0"])])


def test_a_locally_built_mod_is_never_linked_to_a_forge_mod(
    clean_repo: Repo,
    local_source_store: Path,
    tmp_path: Path,
    isolated_default_db_path: Path,
) -> None:
    """The cached catalog holds a mod of exactly this name and version, so a name
    match would find it. If anything linked the install to that entry, this fails
    with Forge IDs on the installed mod -- and a later update would offer to
    overwrite the player's own fork with the release it was forked from."""
    _seed_matching_catalog(isolated_default_db_path)
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0")

    result = services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


def test_the_same_archive_through_the_plain_archive_install_stays_unlinked_too(
    clean_repo: Repo, tmp_path: Path, isolated_default_db_path: Path
) -> None:
    """The sibling of the test above through the other install path: ``sptmod
    install --file`` records no Forge ids either, from the same archive and the
    same matching catalog. Nothing in the manager turns a name into a Forge
    identity, so which path a build came in through cannot change what a later
    update does with it."""
    _seed_matching_catalog(isolated_default_db_path)
    folder = tmp_path / "projects" / _MOD_NAME
    folder.mkdir(parents=True)
    archive = write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0")

    result = services.install_from_archive(clean_repo.path, archive)

    assert result.mod.forge_id == ""
    assert result.mod.forge_version_id == ""


def test_updates_skip_a_locally_built_mod_instead_of_offering_the_forge_release(
    clean_repo: Repo,
    local_source_store: Path,
    tmp_path: Path,
    isolated_default_db_path: Path,
    db_path: Path,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
) -> None:
    """The consequence of the missing link, through the update check: the mod is
    reported as one Forge does not know, and Forge is not asked about it at all.
    If the install recorded a Forge ID, this fails because the mod would be
    queried and the upstream release offered for it."""
    _seed_matching_catalog(isolated_default_db_path)
    folder = _registered(clean_repo.path, tmp_path, local_source_store)
    write_build(folder, "MyMod-1.0.0.zip", mod_name=_MOD_NAME, version="1.0.0")
    services.install_local_source(
        clean_repo.path, folder, _MOD_NAME, store_path=local_source_store
    )
    updates = respx_router.get("/mods/updates")

    report = services.check_updates(clean_repo.path, db_path=db_path, client=client_factory())

    assert report.skipped == [_MOD_NAME]
    assert not report.available
    assert not updates.called
