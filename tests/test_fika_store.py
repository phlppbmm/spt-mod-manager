"""Tests for the Fika settings store ``sptmod.core.fika``.

Every call goes through the public store functions with a store file under
``tmp_path``, never the developer's real ``fika.json``. Covers the tolerant
reader (missing, damaged and foreign-shaped documents), the round trip
including the base URL derived on load, and the address table
``canonical_base_url`` accepts and refuses.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from sptmod.core.fika import (
    FIKA_FILE_NAME,
    FikaSettings,
    canonical_base_url,
    default_fika_path,
    load_settings,
    save_settings,
)
from sptmod.core.modmeta import InvalidMetadataError

KEY = "/games/spt"
OTHER_KEY = "/games/other-spt"


def _store(tmp_path: Path) -> Path:
    """Return the path of a Fika store file that does not exist yet."""
    return tmp_path / "fika.json"


def _write_document(store: Path, document: str) -> None:
    """Write ``document`` verbatim as the Fika store file."""
    store.write_text(document, encoding="utf-8")


def _entry_document(entry: Any) -> str:
    """Return a store document whose only entry for :data:`KEY` is ``entry``."""
    return json.dumps({"version": 1, "games": {KEY: entry}})


# --- the tolerant reader ----------------------------------------------------


def test_load_settings_without_a_store_file_reports_a_game_that_plays_alone(tmp_path: Path) -> None:
    """If the body is `pass`, this fails: it returns None instead of the
    settings of a directory that plays alone, and the reader must not create
    the store file just by looking at it."""
    store = _store(tmp_path)

    assert load_settings(store, KEY) == FikaSettings(role="none", host_address="", base_url="")
    assert not store.exists()


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("not json at all", id="unparsable-text"),
        pytest.param("{", id="truncated-json"),
        pytest.param("[1, 2, 3]", id="document-is-a-list"),
        pytest.param(json.dumps({"version": 1}), id="no-games-at-all"),
        pytest.param(json.dumps({"version": 1, "games": ["/games/spt"]}), id="games-is-a-list"),
        pytest.param(
            json.dumps({"version": 1, "games": {OTHER_KEY: {"role": "host"}}}), id="another-game-only"
        ),
        pytest.param(_entry_document("client"), id="entry-is-a-string"),
        pytest.param(_entry_document(["client", "pc.local"]), id="entry-is-a-list"),
    ],
)
def test_load_settings_reads_an_unusable_document_as_playing_alone(tmp_path: Path, document: str) -> None:
    """If the shape guards were dropped, this fails: a document damaged or
    shaped differently at any level raises instead of reporting the settings
    of a directory that plays alone, which would stand between a player and
    their game."""
    store = _store(tmp_path)
    _write_document(store, document)

    assert load_settings(store, KEY) == FikaSettings(role="none", host_address="", base_url="")


@pytest.mark.parametrize(
    "role",
    [
        pytest.param("chief", id="unknown-role-name"),
        pytest.param(42, id="role-is-a-number"),
        pytest.param(None, id="role-is-null"),
        pytest.param(["client"], id="role-is-a-list"),
        pytest.param("Client", id="role-in-the-wrong-case"),
    ],
)
def test_load_settings_keeps_the_address_when_the_stored_role_is_unknown(tmp_path: Path, role: Any) -> None:
    """If any value were accepted as a role, this fails: the role would come
    back as stored instead of ``none``. The address survives that fall back,
    so a player does not have to type it again."""
    store = _store(tmp_path)
    _write_document(store, _entry_document({"role": role, "host_address": "pc.local:7000"}))

    assert load_settings(store, KEY) == FikaSettings(
        role="none", host_address="pc.local:7000", base_url="https://pc.local:7000"
    )


@pytest.mark.parametrize(
    "address",
    [
        pytest.param(6969, id="address-is-a-number"),
        pytest.param(None, id="address-is-null"),
        pytest.param({"host": "pc.local"}, id="address-is-an-object"),
    ],
)
def test_load_settings_keeps_the_role_when_the_stored_address_is_not_text(tmp_path: Path, address: Any) -> None:
    """If a non-text address were passed on, this fails: ``host_address``
    would hold that value instead of being empty, and the role a player chose
    would be lost along with it."""
    store = _store(tmp_path)
    _write_document(store, _entry_document({"role": "client", "host_address": address}))

    assert load_settings(store, KEY) == FikaSettings(role="client", host_address="", base_url="")


# --- writing and reading back -----------------------------------------------


def test_saved_settings_come_back_with_the_base_url_derived_from_the_address(tmp_path: Path) -> None:
    """If the base URL were stored instead of derived on load, this fails: the
    wrong URL handed to the writer would come back instead of the one the
    stored address leads to."""
    store = _store(tmp_path)

    save_settings(store, KEY, FikaSettings(role="client", host_address="PC.local:7000", base_url="https://wrong:1"))

    assert load_settings(store, KEY) == FikaSettings(
        role="client", host_address="PC.local:7000", base_url="https://pc.local:7000"
    )


def test_saving_one_game_directory_keeps_what_is_stored_for_the_others(tmp_path: Path) -> None:
    """If a write replaced the whole document, this fails: the first game
    directory would come back playing alone after the second one was
    stored."""
    store = _store(tmp_path)
    save_settings(store, KEY, FikaSettings(role="host", host_address="192.168.1.20"))

    save_settings(store, OTHER_KEY, FikaSettings(role="client", host_address="pc.local"))

    assert load_settings(store, KEY) == FikaSettings(
        role="host", host_address="192.168.1.20", base_url="https://192.168.1.20:6969"
    )
    assert load_settings(store, OTHER_KEY) == FikaSettings(
        role="client", host_address="pc.local", base_url="https://pc.local:6969"
    )


def test_a_stored_address_that_can_no_longer_be_used_keeps_its_text_without_a_base_url(tmp_path: Path) -> None:
    """If the reader raised on an unusable stored address, this fails: reading
    the settings would raise instead of reporting the text a player typed with
    an empty base URL, which is what lets a launch say what is wrong."""
    store = _store(tmp_path)
    save_settings(store, KEY, FikaSettings(role="client", host_address="two words"))

    assert load_settings(store, KEY) == FikaSettings(role="client", host_address="two words", base_url="")


def test_a_write_that_cannot_be_completed_leaves_nothing_behind(tmp_path: Path) -> None:
    """If the temporary file were not cleaned up, this fails: a leftover file
    would sit next to the store after the failed write. A directory in the
    store's place makes the move fail after the temporary file was written."""
    store = tmp_path / "fika.json"
    store.mkdir()

    with pytest.raises(OSError):
        save_settings(store, KEY, FikaSettings(role="host"))

    assert [item.name for item in tmp_path.iterdir()] == ["fika.json"]
    assert not list(store.iterdir())


def test_the_default_store_lives_next_to_the_other_settings_of_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the body is `pass`, this fails: no path is returned at all. The
    configuration directory is redirected, so the assertion names the file the
    app writes rather than the developer's real one."""
    config_dir = tmp_path / "config"

    def user_config_path(*_args: object, **_kwargs: object) -> Path:
        return config_dir

    monkeypatch.setattr("sptmod.core.fika.platformdirs.user_config_path", user_config_path)

    assert default_fika_path() == config_dir / FIKA_FILE_NAME


# --- canonical_base_url -----------------------------------------------------


@pytest.mark.parametrize(
    "address,expected",
    [
        pytest.param("", "", id="empty-address-means-no-host"),
        pytest.param("   ", "", id="blank-address-means-no-host"),
        pytest.param("192.168.1.20", "https://192.168.1.20:6969", id="bare-ipv4-gets-https-and-the-spt-port"),
        pytest.param(" 192.168.1.20 ", "https://192.168.1.20:6969", id="surrounding-blanks-are-ignored"),
        pytest.param("192.168.1.20:7000", "https://192.168.1.20:7000", id="explicit-port-is-kept"),
        pytest.param("http://pc.local", "http://pc.local:6969", id="plain-http-is-kept"),
        pytest.param("https://[fe80::1]:6969/", "https://[fe80::1]:6969", id="ipv6-keeps-its-brackets"),
        pytest.param("HTTP://PC.Local", "http://pc.local:6969", id="scheme-and-host-are-lower-cased"),
        pytest.param("HtTpS://Pc.LOCAL:7000", "https://pc.local:7000", id="mixed-case-https-with-a-port"),
        pytest.param("pc.local:1", "https://pc.local:1", id="lowest-port"),
        pytest.param("pc.local:65535", "https://pc.local:65535", id="highest-port"),
        pytest.param("pc.local:443", "https://pc.local:443", id="the-https-port-is-not-special"),
        pytest.param("pc.local:", "https://pc.local:6969", id="a-colon-without-a-port"),
        pytest.param("https://pc.local/", "https://pc.local:6969", id="a-trailing-slash-is-no-path"),
    ],
)
def test_canonical_base_url_accepts_an_address_a_player_can_type(address: str, expected: str) -> None:
    """If the body returned its argument, this fails: the scheme, the port and
    the lower-cased host would be missing. These are the addresses a player is
    expected to type, and each one has to lead to the same URL the game is
    pointed at."""
    assert canonical_base_url(address) == expected


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("ftp://x", id="another-scheme"),
        pytest.param("a b", id="two-words"),
        pytest.param("pc .local", id="a-blank-inside-the-host"),
        pytest.param(":6969", id="a-port-without-a-host"),
        pytest.param("https://", id="a-scheme-without-a-host"),
        pytest.param("pc.local:0", id="port-zero"),
        pytest.param("pc.local:65536", id="port-above-the-range"),
        pytest.param("pc.local:abc", id="a-port-that-is-not-a-number"),
        pytest.param("player:secret@pc.local", id="a-user-name-in-front-of-the-host"),
        pytest.param("pc.local?profile=1", id="a-query"),
        pytest.param("pc.local#raid", id="a-fragment"),
        pytest.param("pc.local?", id="a-trailing-question-mark-without-a-query"),
        pytest.param("pc.local#", id="a-trailing-hash-without-a-fragment"),
        pytest.param("pc.local/foo", id="a-path"),
        pytest.param("fe80::1", id="ipv6-without-brackets"),
        pytest.param("[fe80::1", id="an-unterminated-bracket"),
    ],
)
def test_canonical_base_url_refuses_an_address_no_spt_server_is_reached_at(address: str) -> None:
    """If the guards were dropped, this fails: an address that cannot be
    reached would be accepted here and only fail when a player wants to play,
    instead of raising while they are looking at what they typed."""
    with pytest.raises(InvalidMetadataError):
        canonical_base_url(address)
