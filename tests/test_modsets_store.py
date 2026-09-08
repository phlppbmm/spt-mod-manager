"""Tests for the mod set storage CRUD of ``sptmod.core.services.modsets``.

Every call uses the ``mod_set_store`` fixture (a file under ``tmp_path``) as the
``store_path`` seam, never the developer's real ``modsets.json``. Covers
``list_mod_sets``, ``save_mod_set``, ``delete_mod_set``, ``rename_mod_set`` and
``bind_profile_set`` against a real store file, plus the malformed-document
branches of the underlying store, reached the same way a caller would reach
them: through the public service functions with a hand-written store file.
"""

import json
import os
import re
from pathlib import Path

import pytest

from fixture_types import InstallCommit
from sptmod.core import services
from sptmod.core.errors import ModSetExistsError, ModSetNotFoundError
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo

# --- list_mod_sets on an untouched store ------------------------------------


def test_list_mod_sets_on_an_untouched_game_dir_returns_an_empty_overview(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: returns None instead of an overview with
    empty ``sets`` and ``bindings``, and does not create the store file."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)

    assert not overview.sets
    assert not overview.bindings
    assert not mod_set_store.exists()


# --- save / rename / delete round-trip --------------------------------------


def test_save_mod_set_with_explicit_entries_round_trips_through_a_real_store_file(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: list_mod_sets still reports no sets
    instead of the one just saved, with its entries intact."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    entries = [
        services.ModSetEntry(forge_id="1234", name="SAIN", version="3.2.1", enabled=True),
        services.ModSetEntry(forge_id="", name="MyLocalMod", version="1.0", enabled=False),
    ]

    saved = services.save_mod_set(game_dir, "Fika night", entries=entries, store_path=mod_set_store)

    assert saved.name == "Fika night"
    assert saved.entries == entries
    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)
    assert overview.sets == [saved]


def test_save_mod_set_replaces_the_entries_of_an_existing_set_keeping_its_created_at(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the replace-by-name branch were dropped, this fails: two sets named
    ``Fika night`` would appear instead of one with the second entries list and
    the first ``created_at`` stamp."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    first = services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)

    second_entries = [services.ModSetEntry(forge_id="", name="OnlyMod", version="1.0", enabled=True)]
    second = services.save_mod_set(game_dir, "Fika night", entries=second_entries, store_path=mod_set_store)

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)
    assert [item.name for item in overview.sets] == ["Fika night"]
    assert overview.sets[0].entries == second_entries
    assert overview.sets[0].created_at == first.created_at
    assert second.created_at == first.created_at


def test_rename_mod_set_changes_the_name_reported_by_list_mod_sets(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the body is `pass`, this fails: list_mod_sets still reports the old
    name ``Fika night`` instead of ``Squad night``."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)

    renamed = services.rename_mod_set(game_dir, "Fika night", "Squad night", store_path=mod_set_store)

    assert renamed.name == "Squad night"
    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)
    assert [item.name for item in overview.sets] == ["Squad night"]


def test_delete_mod_set_removes_it_from_list_mod_sets(tmp_path: Path, mod_set_store: Path) -> None:
    """If the body is `pass`, this fails: the deleted set is still reported by
    list_mod_sets."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)

    overview = services.delete_mod_set(game_dir, "Fika night", store_path=mod_set_store)

    assert not overview.sets
    assert not services.list_mod_sets(game_dir, store_path=mod_set_store).sets


def test_delete_mod_set_on_an_unknown_name_raises_mod_set_not_found_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the lookup raised nothing on a miss, this fails: no exception is
    raised for a set that was never saved."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(ModSetNotFoundError):
        services.delete_mod_set(game_dir, "No such set", store_path=mod_set_store)


# --- two game directories in one store stay independent ---------------------


def test_two_game_directories_in_one_store_stay_independent(tmp_path: Path, mod_set_store: Path) -> None:
    """If the store key were ignored, this fails: saving a set for the second
    game directory would also appear under the first, or overwrite it."""
    first_game = tmp_path / "first"
    first_game.mkdir()
    second_game = tmp_path / "second"
    second_game.mkdir()
    services.save_mod_set(first_game, "First set", entries=[], store_path=mod_set_store)

    services.save_mod_set(second_game, "Second set", entries=[], store_path=mod_set_store)

    assert [item.name for item in services.list_mod_sets(first_game, store_path=mod_set_store).sets] == [
        "First set"
    ]
    assert [item.name for item in services.list_mod_sets(second_game, store_path=mod_set_store).sets] == [
        "Second set"
    ]


# --- names: case-insensitive lookup, user casing kept, blank rejected -------


def test_a_saved_set_keeps_the_exact_casing_it_was_saved_with(tmp_path: Path, mod_set_store: Path) -> None:
    """If the name were casefolded on save, this fails: the stored name would
    be all lower-case instead of ``FiKa NiGHt``."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    saved = services.save_mod_set(game_dir, "FiKa NiGHt", entries=[], store_path=mod_set_store)

    assert saved.name == "FiKa NiGHt"


def test_a_saved_set_is_found_and_replaced_by_a_name_of_another_casing(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the lookup used exact string equality, this fails: saving again with
    another casing would add a second set instead of replacing the first."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)

    services.save_mod_set(game_dir, "FIKA NIGHT", entries=[], store_path=mod_set_store)

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)
    assert [item.name for item in overview.sets] == ["FIKA NIGHT"]


@pytest.mark.parametrize("blank_name", ["", "   ", "\t\n"], ids=["empty", "spaces", "tab-and-newline"])
def test_save_mod_set_rejects_a_blank_name(tmp_path: Path, mod_set_store: Path, blank_name: str) -> None:
    """If the strip-and-check guard were dropped, this fails: a set with a
    blank name would be stored instead of InvalidMetadataError being raised."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(InvalidMetadataError):
        services.save_mod_set(game_dir, blank_name, entries=[], store_path=mod_set_store)


# --- save_mod_set without entries captures the installed mods --------------


def test_save_mod_set_without_entries_captures_the_installed_mods_sorted_by_name(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path
) -> None:
    """If entries=None fell back to an empty list instead of the installation,
    this fails: the saved set would have no entries instead of one per
    installed mod, sorted case-insensitively and excluding the removed one."""
    install_commit(clean_repo, "zebra", "1.0.0", state="installed")
    install_commit(clean_repo, "Alpha", "2.0.0", state="disabled")
    install_commit(
        clean_repo, "Gone", "1.0.0", state="removed", files={"BepInEx/plugins/Gone.dll": "gone-marker"}
    )

    saved = services.save_mod_set(clean_repo.path, "Current mods", store_path=mod_set_store)

    assert [(entry.name, entry.enabled) for entry in saved.entries] == [
        ("Alpha", False),
        ("zebra", True),
    ]


# --- rename onto a taken name -------------------------------------------------


def test_rename_mod_set_onto_a_name_taken_by_another_set_raises_mod_set_exists_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the collision check were dropped, this fails: no exception is raised
    and 'Squad night' silently disappears into the rename."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)
    services.save_mod_set(game_dir, "Squad night", entries=[], store_path=mod_set_store)

    with pytest.raises(ModSetExistsError):
        services.rename_mod_set(game_dir, "Fika night", "Squad night", store_path=mod_set_store)


def test_rename_mod_set_to_its_own_name_in_another_spelling_is_allowed(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If a set always collided with itself, this fails: renaming ``Fika
    night`` to ``FIKA NIGHT`` would raise ModSetExistsError instead of storing
    the new spelling."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)

    renamed = services.rename_mod_set(game_dir, "Fika night", "FIKA NIGHT", store_path=mod_set_store)

    assert renamed.name == "FIKA NIGHT"


# --- delete removes bindings, rename retargets them --------------------------


def test_delete_mod_set_unbinds_every_profile_that_was_bound_to_it(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If bindings were left dangling, this fails: the binding to the deleted
    set would still be reported by list_mod_sets."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)
    services.bind_profile_set(game_dir, "profile-1", "Fika night", store_path=mod_set_store)

    overview = services.delete_mod_set(game_dir, "Fika night", store_path=mod_set_store)

    assert not overview.bindings
    assert not services.list_mod_sets(game_dir, store_path=mod_set_store).bindings


def test_rename_mod_set_retargets_every_profile_bound_to_it(tmp_path: Path, mod_set_store: Path) -> None:
    """If bindings were not retargeted, this fails: the binding would still
    name the old set ``Fika night`` instead of ``Squad night``."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)
    services.bind_profile_set(game_dir, "profile-1", "Fika night", store_path=mod_set_store)

    services.rename_mod_set(game_dir, "Fika night", "Squad night", store_path=mod_set_store)

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)
    assert overview.bindings == [services.ProfileBinding(profile_id="profile-1", set_name="Squad night")]


# --- bind / unbind, unknown set on bind --------------------------------------


def test_bind_profile_set_records_the_binding(tmp_path: Path, mod_set_store: Path) -> None:
    """If the body is `pass`, this fails: list_mod_sets reports no bindings
    instead of the one just recorded."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)

    overview = services.bind_profile_set(game_dir, "profile-1", "Fika night", store_path=mod_set_store)

    assert overview.bindings == [services.ProfileBinding(profile_id="profile-1", set_name="Fika night")]


def test_bind_profile_set_without_a_set_name_unbinds_the_profile(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the None branch did not filter out the profile, this fails: the
    binding to 'Fika night' would still be reported after unbinding."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)
    services.bind_profile_set(game_dir, "profile-1", "Fika night", store_path=mod_set_store)

    overview = services.bind_profile_set(game_dir, "profile-1", store_path=mod_set_store)

    assert not overview.bindings


def test_bind_profile_set_on_an_unknown_set_name_raises_mod_set_not_found_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the lookup raised nothing on a miss, this fails: no exception is
    raised, silently binding the profile to a set that does not exist."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(ModSetNotFoundError):
        services.bind_profile_set(game_dir, "profile-1", "No such set", store_path=mod_set_store)


# --- malformed store: InvalidMetadataError naming the file -------------------


def _write_store(store: Path, key: str, game_entry: object) -> None:
    """Write a hand-crafted document to ``store`` for the game directory ``key``."""
    store.write_text(json.dumps({"version": 1, "games": {key: game_entry}}), encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"version": 1, "games": "not-a-dict"}),
    ],
    ids=["invalid-json", "document-is-a-list", "games-is-not-a-dict"],
)
def test_list_mod_sets_raises_invalid_metadata_error_for_a_malformed_document(
    tmp_path: Path, mod_set_store: Path, content: str
) -> None:
    """If the shape guard were dropped, this fails: a TypeError/AttributeError
    propagates, or garbage is silently read back as an empty overview, instead
    of InvalidMetadataError naming the store file."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    mod_set_store.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidMetadataError, match=re.escape(str(mod_set_store))):
        services.list_mod_sets(game_dir, store_path=mod_set_store)


@pytest.mark.parametrize(
    "game_entry",
    ["not-an-object"],
    ids=["game-entry-not-an-object"],
)
def test_list_mod_sets_raises_invalid_metadata_error_for_a_malformed_game_entry(
    tmp_path: Path, mod_set_store: Path, game_entry: object
) -> None:
    """If the per-game-directory shape guard were dropped, this fails: an
    AttributeError propagates instead of InvalidMetadataError naming the file."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    _write_store(mod_set_store, key, game_entry)

    with pytest.raises(InvalidMetadataError, match=re.escape(str(mod_set_store))):
        services.list_mod_sets(game_dir, store_path=mod_set_store)


@pytest.mark.parametrize(
    "sets_value",
    ["not-a-list"],
    ids=["sets-not-a-list"],
)
def test_list_mod_sets_raises_invalid_metadata_error_when_sets_is_not_a_list(
    tmp_path: Path, mod_set_store: Path, sets_value: object
) -> None:
    """If the list guard were dropped, this fails: iterating a string yields
    per-character entries instead of raising InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    _write_store(mod_set_store, key, {"sets": sets_value, "bindings": {}})

    with pytest.raises(InvalidMetadataError, match=re.escape(str(mod_set_store))):
        services.list_mod_sets(game_dir, store_path=mod_set_store)


@pytest.mark.parametrize(
    "set_item",
    [
        "not-an-object",
        {"created_at": ""},
        {"name": "Fika night", "entries": "not-a-list"},
        {"name": "Fika night", "entries": ["not-an-object"]},
        {"name": "Fika night", "entries": [{"name": 42}]},
    ],
    ids=[
        "set-not-an-object",
        "set-has-no-name",
        "entries-not-a-list",
        "entry-not-an-object",
        "entry-name-wrong-type",
    ],
)
def test_list_mod_sets_raises_invalid_metadata_error_for_a_malformed_set(
    tmp_path: Path, mod_set_store: Path, set_item: object
) -> None:
    """If any of these shape guards were dropped, this fails: garbage values
    survive into a ModSet/ModSetEntry, or an exception other than
    InvalidMetadataError propagates."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    _write_store(mod_set_store, key, {"sets": [set_item], "bindings": {}})

    with pytest.raises(InvalidMetadataError, match=re.escape(str(mod_set_store))):
        services.list_mod_sets(game_dir, store_path=mod_set_store)


@pytest.mark.parametrize(
    "bindings_value",
    ["not-a-dict", {"profile-1": 42}],
    ids=["bindings-not-a-dict", "binding-value-not-a-string"],
)
def test_list_mod_sets_raises_invalid_metadata_error_for_malformed_bindings(
    tmp_path: Path, mod_set_store: Path, bindings_value: object
) -> None:
    """If the bindings shape guards were dropped, this fails: an
    AttributeError/TypeError propagates instead of InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    _write_store(mod_set_store, key, {"sets": [], "bindings": bindings_value})

    with pytest.raises(InvalidMetadataError, match=re.escape(str(mod_set_store))):
        services.list_mod_sets(game_dir, store_path=mod_set_store)


def test_list_mod_sets_raises_invalid_metadata_error_when_the_store_cannot_be_read(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the OSError guard were dropped, this fails: an OSError propagates
    instead of InvalidMetadataError naming the store file."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    mod_set_store.mkdir()

    with pytest.raises(InvalidMetadataError, match=re.escape(str(mod_set_store))):
        services.list_mod_sets(game_dir, store_path=mod_set_store)


# --- unknown keys tolerated ---------------------------------------------------


def test_list_mod_sets_tolerates_unknown_keys_at_every_level(tmp_path: Path, mod_set_store: Path) -> None:
    """If an unknown key raised instead of being ignored, this fails: reading
    the well-formed set below would raise InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    _write_store(
        mod_set_store,
        key,
        {
            "sets": [
                {
                    "name": "Fika night",
                    "created_at": "2026-09-08T18:04:11Z",
                    "entries": [
                        {"forge_id": "1234", "name": "SAIN", "version": "3.2.1", "enabled": True, "extra": "x"}
                    ],
                    "unknown_set_key": "x",
                }
            ],
            "bindings": {"profile-1": "Fika night"},
            "unknown_game_key": "x",
        },
    )

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)

    assert [item.name for item in overview.sets] == ["Fika night"]
    assert overview.sets[0].entries[0].name == "SAIN"
    assert overview.bindings == [services.ProfileBinding(profile_id="profile-1", set_name="Fika night")]


# --- write leaves the previous document intact on failure --------------------


def test_a_failed_write_leaves_the_previously_saved_document_intact(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the temp-file-then-replace write were not atomic, this fails: the
    first set would be lost or corrupted by the second, failing save."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)
    os.chmod(mod_set_store.parent, 0o555)
    try:
        with pytest.raises(OSError):
            services.save_mod_set(game_dir, "Squad night", entries=[], store_path=mod_set_store)
    finally:
        os.chmod(mod_set_store.parent, 0o755)

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)
    assert [item.name for item in overview.sets] == ["Fika night"]


# --- game entries missing a "sets" or "bindings" key are tolerated ----------


def test_a_game_entry_with_no_sets_key_reports_an_empty_set_list(tmp_path: Path, mod_set_store: Path) -> None:
    """If the missing-key default were dropped, this fails: reading the
    entry's absent 'sets' key would raise instead of yielding an empty list."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    _write_store(mod_set_store, key, {"bindings": {"profile-1": "Fika night"}})

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)

    assert not overview.sets
    assert overview.bindings == [services.ProfileBinding(profile_id="profile-1", set_name="Fika night")]


def test_a_game_entry_with_no_bindings_key_reports_an_empty_binding_list(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the missing-key default were dropped, this fails: reading the
    entry's absent 'bindings' key would raise instead of yielding an empty
    list."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    key = str(game_dir.resolve())
    entries: list[object] = []
    _write_store(
        mod_set_store,
        key,
        {"sets": [{"name": "Fika night", "created_at": "", "entries": entries}]},
    )

    overview = services.list_mod_sets(game_dir, store_path=mod_set_store)

    assert [item.name for item in overview.sets] == ["Fika night"]
    assert not overview.bindings
