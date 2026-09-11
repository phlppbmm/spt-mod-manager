"""Tests for registering, listing and forgetting local mod sources.

Every call passes the ``local_source_store`` fixture (a file under ``tmp_path``)
as the ``store_path`` seam, so the developer's real ``localsources.json`` is
never touched. Covers ``add_local_source``, ``list_local_sources`` and
``remove_local_source`` against a real store file, the rule that resolves a
folder to the build it installs from, the mods a folder's removal uninstalls,
and the malformed-document branches of
the underlying store, reached the way a caller reaches them: through the public
service functions with a hand-written store file.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from localsource_builds import write_archive, write_build, write_plain_file
from sptmod.core import services
from sptmod.core.errors import InvalidPathError, LocalSourceNotFoundError
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo

_OLD = datetime(2026, 9, 1, 12, 0, tzinfo=UTC).timestamp()
_NEW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC).timestamp()


def _project(tmp_path: Path, name: str = "MyMod") -> Path:
    """Create an empty project folder a player would build a mod in."""
    folder = tmp_path / "projects" / name
    folder.mkdir(parents=True)
    return folder


# --- registering ------------------------------------------------------------


def test_list_local_sources_on_an_untouched_store_returns_nothing(
    clean_repo: Repo, local_source_store: Path
) -> None:
    """If the body is `pass`, this fails: the call returns None instead of an
    empty list, or creates the store file before anything was registered."""
    assert services.list_local_sources(clean_repo.path, store_path=local_source_store) == []
    assert not local_source_store.exists()


def test_add_local_source_registers_the_folder_and_reports_the_whole_list(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the body is `pass`, this fails: the answer names no folder and a later
    ``list_local_sources`` still reports none, so nothing was stored."""
    folder = _project(tmp_path)

    added = services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    assert [source.folder for source in added] == [folder]
    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)
    assert [source.folder for source in listed] == [folder]


def test_add_local_source_stores_the_resolved_path_and_an_added_at_stamp(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A folder is identified by its resolved path, and the store records when it
    was registered; without that a relative path would be stored and would mean
    something else in the next session."""
    folder = _project(tmp_path)
    relative = Path(os.path.relpath(folder, Path.cwd()))

    added = services.add_local_source(clean_repo.path, relative, store_path=local_source_store)

    assert added[0].folder == folder.resolve()
    assert added[0].added_at.endswith("Z")


def test_adding_the_same_folder_again_changes_nothing(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the idempotence check were dropped, this fails: the folder appears
    twice, and the second registration overwrites the first stamp."""
    folder = _project(tmp_path)
    first = services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    again = services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    assert [source.folder for source in again] == [folder]
    assert again[0].added_at == first[0].added_at


def test_add_local_source_refuses_a_folder_that_does_not_exist(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the existence check were dropped, this fails: a typo would be stored
    silently and only show up as a failure when the user asks to install."""
    missing = tmp_path / "not-there"

    with pytest.raises(InvalidPathError, match=re.escape(str(missing))):
        services.add_local_source(clean_repo.path, missing, store_path=local_source_store)


def test_add_local_source_refuses_a_file(clean_repo: Repo, local_source_store: Path, tmp_path: Path) -> None:
    """A project folder is a folder; if only existence were checked, this fails
    because a file would be accepted and could never resolve to a build."""
    file_path = tmp_path / "MyMod.zip"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(InvalidPathError, match=re.escape(str(file_path))):
        services.add_local_source(clean_repo.path, file_path, store_path=local_source_store)


# --- forgetting -------------------------------------------------------------


def test_remove_local_source_drops_it_from_the_list(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the body is `pass`, this fails: the removed folder is still reported by
    the answer and by a later listing."""
    kept = _project(tmp_path, "Kept")
    dropped = _project(tmp_path, "Dropped")
    services.add_local_source(clean_repo.path, kept, store_path=local_source_store)
    services.add_local_source(clean_repo.path, dropped, store_path=local_source_store)

    remaining = services.remove_local_source(clean_repo.path, dropped, store_path=local_source_store)

    assert [source.folder for source in remaining] == [kept]


def test_remove_local_source_uninstalls_the_mods_it_installed(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Nothing may be left behind with no record of where it came from: if the
    removal only dropped the folder from the list, this fails because the mod it
    installed would still be installed and its files still in the game
    directory."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
    services.install_local_source(
        clean_repo.path, folder, "MyMod", store_path=local_source_store
    )

    services.remove_local_source(clean_repo.path, folder, store_path=local_source_store)

    assert services.list_mods(clean_repo.path) == []
    assert not (clean_repo.path / "SPT/user/mods/MyMod").exists()


def test_remove_local_source_leaves_the_mods_of_other_folders_alone(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the removal went by name or took every local mod, this fails: the mod
    of the folder that stays registered would be uninstalled too."""
    dropped = _project(tmp_path, "MyMod")
    kept = _project(tmp_path, "OtherMod")
    write_build(dropped, "MyMod-1.0.0.zip", mod_name="MyMod")
    write_build(kept, "OtherMod-1.0.0.zip", mod_name="OtherMod")
    for folder in (dropped, kept):
        services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
        services.install_local_source(
            clean_repo.path, folder, folder.name, store_path=local_source_store
        )

    services.remove_local_source(clean_repo.path, dropped, store_path=local_source_store)

    assert [mod.name for mod in services.list_mods(clean_repo.path)] == ["OtherMod"]


def test_remove_local_source_also_uninstalls_a_mod_no_longer_offered(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A build can disappear from the folder after the install. If the removal
    only went through what the folder offers now, this fails with the mod still
    installed and nothing recording where it came from."""
    folder = _project(tmp_path)
    archive = write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
    services.install_local_source(
        clean_repo.path, folder, "MyMod", store_path=local_source_store
    )
    archive.unlink()

    services.remove_local_source(clean_repo.path, folder, store_path=local_source_store)

    assert services.list_mods(clean_repo.path) == []


def test_a_listing_names_the_mods_a_removal_would_uninstall(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The surfaces confirm a removal by naming those mods. If the listing left
    out a mod the folder no longer offers a build of, this fails and the
    confirmation would understate what the removal does."""
    folder = _project(tmp_path)
    archive = write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
    services.install_local_source(
        clean_repo.path, folder, "MyMod", store_path=local_source_store
    )
    archive.unlink()

    sources = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [mod.name for mod in sources[0].installed] == ["MyMod"]
    assert sources[0].mods == []


def test_a_removed_mod_is_no_longer_named_as_one_a_removal_uninstalls(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Only active mods can be uninstalled: if the listing named a mod that was
    already removed by hand, this fails and the removal would promise to take out
    something that is gone."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
    services.install_local_source(
        clean_repo.path, folder, "MyMod", store_path=local_source_store
    )
    services.remove_mod(clean_repo.path, "MyMod")

    sources = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert sources[0].installed == []


def test_removing_the_mod_leaves_the_folder_registered(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The other half of the same rule: if removing a mod took its folder off the
    list, this fails because the listing would be empty afterwards."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
    services.install_local_source(
        clean_repo.path, folder, "MyMod", store_path=local_source_store
    )

    services.remove_mod(clean_repo.path, "MyMod")

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)
    assert [source.folder for source in listed] == [folder]
    assert [offered.name for offered in listed[0].mods] == ["MyMod"]


def test_remove_local_source_on_an_unregistered_folder_says_so(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the lookup returned None instead of raising, this fails: the call would
    answer as if it had forgotten a folder it never knew."""
    folder = _project(tmp_path)

    with pytest.raises(LocalSourceNotFoundError, match=re.escape(str(folder))):
        services.remove_local_source(clean_repo.path, folder, store_path=local_source_store)


# --- what a folder offers ---------------------------------------------------


def test_a_folder_without_a_build_offers_nothing(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A freshly registered folder is a normal state, not damage: if it raised
    instead, this fails because registering would already report a failure."""
    folder = _project(tmp_path)

    added = services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    assert added[0].mods == []


def test_an_empty_build_folder_offers_nothing(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Sibling of the missing build folder: an empty one must read the same way
    instead of failing on a folder with nothing to look at."""
    folder = _project(tmp_path)
    (folder / "build").mkdir()
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert listed[0].mods == []


def test_a_folder_offers_every_mod_it_has_built(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A project folder regularly builds more than one mod. If a folder resolved
    to a single archive, this fails: one of the two mods would be invisible and
    unreachable, and which one would depend on the build order."""
    folder = _project(tmp_path)
    write_build(folder, "DynamicGoons-2.1.0.zip", mod_name="DynamicGoons", version="2.1.0")
    write_build(
        folder, "BossSkullTooltips-1.0.0.zip", mod_name="BossSkullTooltips", version="1.0.0"
    )
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [(offered.name, offered.version) for offered in listed[0].mods] == [
        ("BossSkullTooltips", "1.0.0"),
        ("DynamicGoons", "2.1.0"),
    ]


def test_the_newest_build_of_a_mod_is_the_one_offered(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Two archives declaring the same mod name are two builds of one mod. If the
    older one were offered, the player would install a build they replaced."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0", mtime=_OLD)
    newest = write_build(
        folder, "MyMod-1.1.0.zip", mod_name="MyMod", version="1.1.0", mtime=_NEW
    )
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [(offered.name, offered.version) for offered in listed[0].mods] == [
        ("MyMod", "1.1.0")
    ]
    assert listed[0].mods[0].archive == newest
    assert listed[0].mods[0].built_at == datetime.fromtimestamp(_NEW, UTC)


def test_rebuilding_one_mod_does_not_change_what_another_one_offers(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The bug this model exists to prevent: with a folder resolved by the newest
    file alone, rebuilding the second mod would silently move the first one's
    build onto the wrong archive."""
    folder = _project(tmp_path)
    goons = write_build(
        folder, "DynamicGoons-2.1.0.zip", mod_name="DynamicGoons", version="2.1.0", mtime=_OLD
    )
    write_build(
        folder,
        "BossSkullTooltips-1.0.0.zip",
        mod_name="BossSkullTooltips",
        version="1.0.0",
        mtime=_NEW,
    )
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    offered = {item.name: item.archive for item in listed[0].mods}
    assert offered["DynamicGoons"] == goons


def test_an_older_build_never_displaces_the_newest_one(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Which build wins is decided by the modification time alone. The archive
    read last here carries the higher version and the older stamp: if the last
    one seen won, the player would be offered a build they had replaced."""
    folder = _project(tmp_path)
    newest = write_build(
        folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0", mtime=_NEW
    )
    write_build(folder, "MyMod-2.0.0.zip", mod_name="MyMod", version="2.0.0", mtime=_OLD)
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [offered.archive for offered in listed[0].mods] == [newest]
    assert listed[0].mods[0].version == "1.0.0"


def test_an_archive_without_a_readable_mod_name_is_not_offered(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """An archive that lands in a mod directory but names no mod -- notes next to
    the plugins, say -- has nothing to install or to link an installed mod by. If
    it were offered anyway, the folder would offer a mod with no name."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    write_archive(folder, "notes.zip", {"BepInEx/plugins/notes.txt": "read me"})
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [offered.name for offered in listed[0].mods] == ["MyMod"]


def test_only_the_archives_directly_in_the_build_folder_count(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """The search is deliberately not recursive: if it descended, this fails
    because what a folder offers would depend on what a build tool leaves in its
    working directories."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    nested = folder / "build" / "nested"
    nested.mkdir()
    write_build(nested, "Hidden-2.0.0.zip", mod_name="Hidden", version="2.0.0")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [offered.name for offered in listed[0].mods] == ["MyMod"]


def test_files_that_are_no_mod_archives_are_ignored(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A build folder holds build output of every kind. If everything in it were
    read as a mod, this fails: the log file would have to be a mod."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0", mtime=_OLD)
    write_plain_file(folder, "build.log", mtime=_NEW)
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [offered.name for offered in listed[0].mods] == ["MyMod"]


def test_an_archive_that_holds_no_mod_is_not_offered(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """An archive of something other than a mod -- a source bundle, say -- must
    not fail the listing: if it did, one stray zip would make the whole folder
    unusable instead of simply not being offered."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    write_archive(folder, "sources.zip", {"README.md": "not a mod"})
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [offered.name for offered in listed[0].mods] == ["MyMod"]


def test_a_build_folder_holding_only_other_files_offers_nothing(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Sibling of the two cases above with nothing readable at all: the folder
    reads as one that has built nothing rather than as a failure."""
    folder = _project(tmp_path)
    write_plain_file(folder, "build.log")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert listed[0].mods == []


def test_a_folder_inside_the_build_folder_is_no_build(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Directories are skipped even when they end in an archive suffix: without
    the file check this fails, because ``build/staging.zip/`` would be read as an
    archive."""
    folder = _project(tmp_path)
    (folder / "build" / "staging.zip").mkdir(parents=True)
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert listed[0].mods == []


def test_the_listing_links_an_offered_mod_to_the_installed_one(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """Nothing about the installed mod is stored, so the link is made by the name
    the archive declares: if it were not, the listing could not say which of the
    offered mods is already in the game directory."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    write_build(folder, "Other-1.0.0.zip", mod_name="Other", version="1.0.0")
    services.add_local_source(clean_repo.path, folder, store_path=local_source_store)
    services.install_local_source(
        clean_repo.path, folder, "MyMod", store_path=local_source_store
    )

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    installed = {offered.name: offered.mod for offered in listed[0].mods}
    assert installed["Other"] is None
    assert installed["MyMod"] is not None
    assert installed["MyMod"].version == "1.0.0"


# --- damaged and unusual store documents ------------------------------------


def _write_store(store: Path, sources: object) -> None:
    """Write a store document whose ``sources`` value is ``sources``."""
    store.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")


@pytest.mark.parametrize(
    ("raw_content", "sources"),
    [
        pytest.param("not json at all", None, id="unparsable"),
        pytest.param('["a list"]', None, id="list-as-root"),
        pytest.param(None, "not a list", id="sources-no-list"),
        pytest.param(None, ["not an object"], id="source-no-object"),
        pytest.param(None, [{"mod_name": "MyMod"}], id="no-folder"),
        pytest.param(None, [{"folder": 7}], id="folder-no-text"),
        pytest.param(None, [{"folder": "/tmp/x", "added_at": 7}], id="added-at-no-text"),
    ],
)
def test_a_damaged_store_is_reported_loudly_and_names_the_file(
    clean_repo: Repo, local_source_store: Path, raw_content: str | None, sources: object
) -> None:
    """A store of the wrong shape is rejected with InvalidMetadataError naming it.

    Writes the store either verbatim (``raw_content``: unparsable text, a JSON
    list as root) or as a document whose ``sources`` value carries the damage.
    Silently reading it as empty would forget the folders the user registered,
    and without the shape guards a TypeError would escape instead.
    """
    if raw_content is None:
        _write_store(local_source_store, sources)
    else:
        local_source_store.write_text(raw_content, encoding="utf-8")

    with pytest.raises(InvalidMetadataError, match=re.escape(str(local_source_store))):
        services.list_local_sources(clean_repo.path, store_path=local_source_store)


def test_a_store_that_cannot_be_read_is_reported_loudly(clean_repo: Repo, local_source_store: Path) -> None:
    """If the OSError guard were dropped, this fails: an OSError propagates
    instead of InvalidMetadataError naming the store file."""
    local_source_store.mkdir()

    with pytest.raises(InvalidMetadataError, match=re.escape(str(local_source_store))):
        services.list_local_sources(clean_repo.path, store_path=local_source_store)


def test_unknown_keys_of_a_well_formed_store_are_ignored(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If an unknown key raised instead of being ignored, this fails: a store
    written by another version of sptmod -- one that recorded the mod a folder
    installed, say -- would become unreadable."""
    folder = _project(tmp_path)
    _write_store(
        local_source_store,
        [{"folder": str(folder), "mod_name": "MyMod", "added_at": "2026-09-11T12:00:00Z", "extra": "x"}],
    )

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [source.folder for source in listed] == [folder]
    assert listed[0].added_at == "2026-09-11T12:00:00Z"


def test_a_source_without_the_optional_keys_is_still_a_folder(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the default were dropped, this fails: a document holding only the
    folder would be rejected instead of reading as a folder whose registration
    date nobody recorded."""
    folder = _project(tmp_path)
    _write_store(local_source_store, [{"folder": str(folder)}])

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)

    assert [source.folder for source in listed] == [folder]
    assert listed[0].added_at == ""


def test_a_failed_write_leaves_the_previously_stored_folders_intact(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the temp-file-then-replace write were not atomic, this fails: the first
    folder would be lost or the document corrupted by the second, failing
    registration."""
    first = _project(tmp_path, "First")
    second = _project(tmp_path, "Second")
    services.add_local_source(clean_repo.path, first, store_path=local_source_store)
    os.chmod(local_source_store.parent, 0o555)
    try:
        with pytest.raises(OSError):
            services.add_local_source(clean_repo.path, second, store_path=local_source_store)
    finally:
        os.chmod(local_source_store.parent, 0o755)

    listed = services.list_local_sources(clean_repo.path, store_path=local_source_store)
    assert [source.folder for source in listed] == [first]
