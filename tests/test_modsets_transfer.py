"""Tests for the mod set interchange format: ``export_mod_set``/``import_mod_set``
of ``sptmod.core.services.modsets``.

Every call uses the ``mod_set_store`` fixture as ``store_path``, never the
developer's real store. Only ``test_a_mod_set_exported_as_export_mods_json_is_rejected``
needs a real git repository, to produce a genuine ``export_mods`` (the
unrelated ``{"mods": [...]}`` format) document to feed back in.
"""

import base64
import json
import zlib
from pathlib import Path

import pytest

from fixture_types import InstallCommit
from sptmod.core import services
from sptmod.core.errors import ModSetExistsError, ModSetNotFoundError
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo


def _saved_entries() -> list[services.ModSetEntry]:
    return [
        services.ModSetEntry(forge_id="1234", name="SAIN", version="3.2.1", enabled=True),
        services.ModSetEntry(forge_id="", name="MyLocalMod", version="1.0", enabled=False),
    ]


# --- export content: stable JSON under "mod_set" ----------------------------


def test_export_mod_set_content_is_stable_json_under_mod_set_with_a_trailing_newline(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the wrapper key or shape changed, this fails: the parsed document
    would not have a top-level 'mod_set' object with 'name' and 'entries'."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=_saved_entries(), store_path=mod_set_store)

    export = services.export_mod_set(game_dir, "Fika night", store_path=mod_set_store)

    assert export.content.endswith("\n")
    document = json.loads(export.content)
    assert document == {
        "mod_set": {
            "name": "Fika night",
            "entries": [
                {"forge_id": "1234", "name": "SAIN", "version": "3.2.1", "enabled": True},
                {"forge_id": "", "name": "MyLocalMod", "version": "1.0", "enabled": False},
            ],
        }
    }


def test_export_mod_set_content_is_identical_on_repeated_exports(tmp_path: Path, mod_set_store: Path) -> None:
    """If rendering depended on dict/set ordering, this fails: the two
    renders would differ even though nothing changed in between."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=_saved_entries(), store_path=mod_set_store)

    first = services.export_mod_set(game_dir, "Fika night", store_path=mod_set_store)
    second = services.export_mod_set(game_dir, "Fika night", store_path=mod_set_store)

    assert first.content == second.content
    assert first.code == second.code


def test_export_mod_set_on_an_unknown_name_raises_mod_set_not_found_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the lookup raised nothing on a miss, this fails: no exception is
    raised for a set that was never saved."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(ModSetNotFoundError):
        services.export_mod_set(game_dir, "No such set", store_path=mod_set_store)


# --- share code and file round-trip -----------------------------------------


def test_a_share_code_round_trips_through_import_and_keeps_the_entries(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the compress/encode or decode/decompress step dropped data, this
    fails: the imported entries would differ from the exported ones."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    services.save_mod_set(source_dir, "Fika night", entries=_saved_entries(), store_path=mod_set_store)
    export = services.export_mod_set(source_dir, "Fika night", store_path=mod_set_store)

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    imported = services.import_mod_set(target_dir, content=export.code, store_path=mod_set_store)

    assert imported.name == "Fika night"
    assert imported.entries == _saved_entries()
    overview = services.list_mod_sets(target_dir, store_path=mod_set_store)
    assert [item.name for item in overview.sets] == ["Fika night"]


def test_an_exported_file_round_trips_through_import(tmp_path: Path, mod_set_store: Path) -> None:
    """If set_file were read incorrectly, this fails: the imported entries
    would be empty or differ from the exported ones."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    services.save_mod_set(source_dir, "Fika night", entries=_saved_entries(), store_path=mod_set_store)
    export = services.export_mod_set(source_dir, "Fika night", store_path=mod_set_store)
    export_file = tmp_path / "fika-night.sptmodset.json"
    export_file.write_text(export.content, encoding="utf-8")

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    imported = services.import_mod_set(target_dir, set_file=export_file, store_path=mod_set_store)

    assert imported.name == "Fika night"
    assert imported.entries == _saved_entries()


# --- import_mod_set: name override, taken name ------------------------------


def test_import_mod_set_with_an_explicit_name_overrides_the_embedded_one(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the name override were ignored, this fails: the stored set would be
    named 'Fika night' (the embedded name) instead of 'My Copy'."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    services.save_mod_set(source_dir, "Fika night", entries=_saved_entries(), store_path=mod_set_store)
    export = services.export_mod_set(source_dir, "Fika night", store_path=mod_set_store)

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    imported = services.import_mod_set(target_dir, content=export.content, name="My Copy", store_path=mod_set_store)

    assert imported.name == "My Copy"


def test_import_mod_set_onto_a_taken_name_raises_mod_set_exists_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the collision check were dropped, this fails: no exception is
    raised and the existing 'Fika night' set is silently replaced."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    services.save_mod_set(game_dir, "Fika night", entries=[], store_path=mod_set_store)
    content = json.dumps({"mod_set": {"name": "Fika night", "entries": []}})

    with pytest.raises(ModSetExistsError):
        services.import_mod_set(game_dir, content=content, store_path=mod_set_store)


# --- neither / both of set_file and content ---------------------------------


def test_import_mod_set_with_neither_set_file_nor_content_raises_invalid_metadata_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the exactly-one-source guard were dropped, this fails: a TypeError
    (None has no attribute) propagates instead of InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(game_dir, store_path=mod_set_store)


def test_import_mod_set_with_both_set_file_and_content_raises_invalid_metadata_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the exactly-one-source guard were dropped, this fails: content would
    silently win (or the file would) instead of InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    some_file = tmp_path / "set.json"
    some_file.write_text(json.dumps({"mod_set": {"name": "X", "entries": []}}), encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(
            game_dir,
            set_file=some_file,
            content=json.dumps({"mod_set": {"name": "X", "entries": []}}),
            store_path=mod_set_store,
        )


# --- bad code / unreadable file ----------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("SPTMODSET1-not-valid-base64!!!", id="damaged-share-code"),
        pytest.param("not a mod set at all", id="neither-json-nor-share-code"),
        pytest.param("{not valid json", id="invalid-json"),
    ],
)
def test_import_mod_set_rejects_undecodable_content(tmp_path: Path, mod_set_store: Path, content: str) -> None:
    """Content that is neither a readable share code nor readable JSON is refused.

    Passes a share code whose payload is not decodable base64 ("damaged-share-code"),
    a string that carries neither the share-code prefix nor JSON punctuation
    ("neither-json-nor-share-code") and text that starts like JSON but does not parse
    ("invalid-json"). Each raises InvalidMetadataError instead of letting a
    binascii.Error, zlib.error or json.JSONDecodeError escape to the user.
    """
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(game_dir, content=content, store_path=mod_set_store)


def test_import_mod_set_from_a_missing_file_raises_invalid_metadata_error(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the OSError guard on set_file were dropped, this fails: a
    FileNotFoundError propagates instead of InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(game_dir, set_file=tmp_path / "no-such-file.json", store_path=mod_set_store)


# --- a mod set exported as export_mods' unrelated {"mods": [...]} format ----


def test_a_mod_set_exported_as_export_mods_json_is_rejected(
    clean_repo: Repo, install_commit: InstallCommit, mod_set_store: Path, tmp_path: Path
) -> None:
    """If the wrapper key check were dropped, this fails: the export_mods
    document would be silently read as a mod set with no entries instead of
    raising InvalidMetadataError."""
    install_commit(clean_repo, "SomeMod", "1.0.0")
    unrelated_content = services.export_mods(clean_repo.path, "json", db_path=tmp_path / "catalog.db")

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(clean_repo.path, content=unrelated_content, store_path=mod_set_store)


# --- malformed mod set shape inside otherwise-valid JSON --------------------


@pytest.mark.parametrize(
    "mod_set_payload",
    [
        {"entries": []},
        {"name": 42, "entries": []},
        {"name": "X", "entries": "not-a-list"},
        {"name": "X", "entries": ["not-an-object"]},
        {"name": "X", "entries": [{"name": 42}]},
    ],
    ids=[
        "missing-name",
        "name-wrong-type",
        "entries-not-a-list",
        "entry-not-an-object",
        "entry-name-wrong-type",
    ],
)
def test_import_mod_set_rejects_a_malformed_mod_set_shape(
    tmp_path: Path, mod_set_store: Path, mod_set_payload: object
) -> None:
    """If any of these shape guards were dropped, this fails: garbage values
    survive into a ModSet/ModSetEntry, or an exception other than
    InvalidMetadataError propagates."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    content = json.dumps({"mod_set": mod_set_payload})

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(game_dir, content=content, store_path=mod_set_store)


# --- a file that holds no text ----------------------------------------------


def test_import_mod_set_from_a_file_that_is_not_text_is_refused_in_plain_language(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the decode error were not caught, this fails: a UnicodeDecodeError
    propagates instead of InvalidMetadataError. If the raw decoder message were
    passed on, this fails too: the reported text names the offending file but
    no codec, byte position or Python type."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    binary_file = tmp_path / "set.bin"
    binary_file.write_bytes(b"\xff\xfe\x00\x9c\x80mod set")

    with pytest.raises(InvalidMetadataError) as exc_info:
        services.import_mod_set(game_dir, set_file=binary_file, store_path=mod_set_store)

    message = str(exc_info.value)
    assert str(binary_file) in message
    assert not any(word in message.lower() for word in ("codec", "utf", "unicode", "byte", "0x", "position"))


# --- share code size bound ---------------------------------------------------


def _share_code(document: object) -> str:
    """Render ``document`` in the published share code format.

    Built here from the format alone -- prefix, zlib, url-safe base64 without
    padding -- so the bound is exercised without asking the module under test
    to produce the code it is supposed to refuse.
    """
    packed = zlib.compress(json.dumps(document).encode("utf-8"), 9)
    return "SPTMODSET1-" + base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def _padded_set_document(payload_size: int) -> dict[str, object]:
    """A mod set document whose JSON is roughly ``payload_size`` bytes long."""
    return {"mod_set": {"name": "A" * payload_size, "entries": []}}


def test_a_share_code_carrying_more_than_four_mebibytes_is_refused(tmp_path: Path, mod_set_store: Path) -> None:
    """If the unpack bound were dropped, this fails: the 5 MiB payload of a
    5 kB code is unpacked and stored as a mod set instead of raising
    InvalidMetadataError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    code = _share_code(_padded_set_document(5 * 1024 * 1024))

    with pytest.raises(InvalidMetadataError):
        services.import_mod_set(game_dir, content=code, store_path=mod_set_store)

    assert not services.list_mod_sets(game_dir, store_path=mod_set_store).sets


def test_a_share_code_just_below_the_bound_still_imports_its_whole_payload(
    tmp_path: Path, mod_set_store: Path
) -> None:
    """If the bound cut the payload short or refused everything large, this
    fails: the imported name is truncated or no set is stored at all."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    payload_size = 4 * 1024 * 1024 - 4096
    code = _share_code(_padded_set_document(payload_size))

    imported = services.import_mod_set(game_dir, content=code, store_path=mod_set_store)

    assert imported.name == "A" * payload_size
