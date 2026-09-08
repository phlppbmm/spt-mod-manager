"""Tests for the Fika service functions of ``sptmod.core.services``.

``get_fika_settings`` and ``set_fika_settings`` always run against the
``fika_store`` fixture (a file under ``tmp_path``), never the developer's real
``fika.json``; every answer of a host is simulated with respx against the
documentation address ``192.0.2.10``, so no test opens a socket.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from sptmod.core import services
from sptmod.core.errors import FikaHostUnreachableError
from sptmod.core.fika import FikaSettings, save_settings
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.modsets import store_key
from sptmod.core.sptserver import PROFILES_PATH

HOST_URL = "https://192.0.2.10:6969"


@pytest.fixture
def game(tmp_path: Path) -> Path:
    """A game directory the Fika settings are stored for."""
    directory = tmp_path / "game"
    directory.mkdir()
    return directory


def _profile_json(
    profile_id: str,
    *,
    username: str = "0Sullivan",
    nickname: str = "TesterNick",
    level: int = 12,
    side: str = "Usec",
    has_password: bool = False,
) -> dict[str, Any]:
    """Build one mini profile as an SPT server lists it."""
    return {
        "profileId": profile_id,
        "username": username,
        "nickname": nickname,
        "currlvl": level,
        "side": side,
        "hasPassword": has_password,
        "edition": "Edge Of Darkness",
    }


def _profiles_route(router: respx.MockRouter, entries: Any) -> respx.Route:
    """Answer the host's profile list with ``entries``."""
    return router.get(PROFILES_PATH).respond(200, json={"Response": entries})


# --- get_fika_settings / set_fika_settings ----------------------------------


def test_a_game_directory_nothing_is_stored_about_plays_alone(game: Path, fika_store: Path) -> None:
    """If the body is `pass`, this fails: nothing is returned instead of the
    settings of a directory that plays alone, and reading them must not create
    the settings file."""
    assert services.get_fika_settings(game, fika_path=fika_store) == services.FikaSettings(
        role="none", host_address="", base_url=""
    )
    assert not fika_store.exists()


def test_a_stored_role_and_address_come_back_with_the_url_the_game_is_pointed_at(game: Path, fika_store: Path) -> None:
    """If the write were dropped, this fails: the later read reports a
    directory that plays alone instead of the client and its host."""
    stored = services.set_fika_settings(game, "client", "192.168.1.20:7000", fika_path=fika_store)

    assert stored == services.FikaSettings(
        role="client", host_address="192.168.1.20:7000", base_url="https://192.168.1.20:7000"
    )
    assert services.get_fika_settings(game, fika_path=fika_store) == stored


def test_the_typed_address_is_stripped_and_led_to_one_url(game: Path, fika_store: Path) -> None:
    """If the address were passed on as typed, this fails: the surrounding
    blanks would survive in ``host_address`` and the upper-case host would
    stay in the URL the game is pointed at."""
    stored = services.set_fika_settings(game, "client", "  HOST.local  ", fika_path=fika_store)

    assert stored.host_address == "HOST.local"
    assert stored.base_url == "https://host.local:6969"


@pytest.mark.parametrize(
    "role",
    [pytest.param("none", id="playing-alone"), pytest.param("host", id="hosting-the-session")],
)
def test_a_directory_that_needs_no_host_is_stored_without_an_address(
    game: Path, fika_store: Path, role: services.FikaRole
) -> None:
    """If an address were demanded of every role, this fails: choosing to play
    alone or to host would raise instead of being stored."""
    stored = services.set_fika_settings(game, role, fika_path=fika_store)

    assert stored == services.FikaSettings(role=role, host_address="", base_url="")
    assert services.get_fika_settings(game, fika_path=fika_store).role == role


@pytest.mark.parametrize(
    "address",
    [pytest.param("", id="no-address-at-all"), pytest.param("   ", id="blanks-only")],
)
def test_joining_a_host_without_naming_one_is_refused(game: Path, fika_store: Path, address: str) -> None:
    """If the guard were dropped, this fails: a client without a host would be
    stored and only fail when the player wants to play. Nothing is written, so
    the previous choice survives."""
    with pytest.raises(InvalidMetadataError, match="needs the address"):
        services.set_fika_settings(game, "client", address, fika_path=fika_store)

    assert services.get_fika_settings(game, fika_path=fika_store).role == "none"


def test_an_address_no_server_is_reached_at_is_refused_and_stores_nothing(game: Path, fika_store: Path) -> None:
    """If the address were stored unchecked, this fails: the unusable address
    would be written and reported back instead of raising while the player is
    looking at it."""
    services.set_fika_settings(game, "client", "192.168.1.20", fika_path=fika_store)

    with pytest.raises(InvalidMetadataError):
        services.set_fika_settings(game, "client", "ftp://192.168.1.20", fika_path=fika_store)

    assert services.get_fika_settings(game, fika_path=fika_store).host_address == "192.168.1.20"


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("ftp://x", id="a-scheme-no-server-is-reached-over"),
        pytest.param("pc.local?", id="a-trailing-question-mark"),
        pytest.param("two words", id="two-words"),
    ],
)
def test_playing_alone_is_stored_even_when_an_unusable_address_is_passed(
    game: Path, fika_store: Path, address: str
) -> None:
    """If the address were checked for every role, this fails: choosing to play
    alone would raise because of an address left over from a Fika role, so a
    damaged setting would stand between a player and their game. The address is
    dropped rather than kept, and the same is read back afterwards."""
    stored = services.set_fika_settings(game, "none", address, fika_path=fika_store)

    assert stored == services.FikaSettings(role="none", host_address="", base_url="")
    assert services.get_fika_settings(game, fika_path=fika_store) == services.FikaSettings(
        role="none", host_address="", base_url=""
    )


# --- list_host_profiles -----------------------------------------------------


def test_the_profiles_of_a_host_are_reported_in_the_order_the_host_sends_them(game: Path, fika_store: Path) -> None:
    """If the body is `pass`, this fails: no profiles are returned at all.
    Field by field this pins what a joining machine, which has no profiles of
    its own, shows its player."""
    services.set_fika_settings(game, "client", "192.0.2.10", fika_path=fika_store)

    with respx.mock(base_url=HOST_URL, assert_all_called=False) as router:
        _profiles_route(
            router,
            [
                _profile_json("aaa111", username="0Sullivan", nickname="TesterNick", level=42, side="Usec"),
                _profile_json("bbb222", username="Nikita", nickname="Boss", level=1, side="Bear", has_password=True),
            ],
        )

        profiles = services.list_host_profiles(game, fika_path=fika_store)

    assert profiles == [
        services.HostProfile(
            profile_id="aaa111",
            username="0Sullivan",
            nickname="TesterNick",
            level=42,
            side="Usec",
            has_password=False,
        ),
        services.HostProfile(
            profile_id="bbb222",
            username="Nikita",
            nickname="Boss",
            level=1,
            side="Bear",
            has_password=True,
        ),
    ]


def test_an_entry_the_host_describes_in_an_unexpected_shape_is_left_out(game: Path, fika_store: Path) -> None:
    """If the host's answer were passed on unfiltered, this fails: reading it
    raises or offers a profile that cannot be played instead of the two the
    host really has. One odd entry must not block a joining player. The wire
    shapes themselves are covered in ``tests/test_sptserver.py``."""
    services.set_fika_settings(game, "client", "192.0.2.10", fika_path=fika_store)

    with respx.mock(base_url=HOST_URL, assert_all_called=False) as router:
        _profiles_route(router, [_profile_json("aaa111"), "junk", _profile_json("bbb222")])

        profiles = services.list_host_profiles(game, fika_path=fika_store)

    assert [profile.profile_id for profile in profiles] == ["aaa111", "bbb222"]


def test_listing_the_profiles_without_a_host_says_no_address_is_set(game: Path, fika_store: Path) -> None:
    """If the guard were dropped, this fails: a request would go to nowhere
    and the player would be told about a connection instead of about the
    setting they have not made yet."""
    with pytest.raises(FikaHostUnreachableError, match="No usable address is set for the Fika host"):
        services.list_host_profiles(game, fika_path=fika_store)


def test_listing_the_profiles_of_a_host_whose_address_became_unusable_says_so(
    game: Path, fika_store: Path
) -> None:
    """If an empty base URL were passed to the request, this fails: the call
    would end in a connection error instead of the message about the missing
    address. The stored address bypasses ``set_fika_settings`` because that is
    what an address stored by an older version, or by hand, looks like."""
    save_settings(fika_store, store_key(game), FikaSettings(role="client", host_address="two words"))

    with pytest.raises(FikaHostUnreachableError, match="No usable address is set for the Fika host"):
        services.list_host_profiles(game, fika_path=fika_store)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("refused"), id="the-host-refuses-the-connection"),
        pytest.param(httpx.ReadTimeout("slow"), id="the-host-does-not-answer-in-time"),
        pytest.param(httpx.RemoteProtocolError("garbled"), id="the-answer-is-garbled"),
    ],
)
def test_a_host_that_does_not_answer_is_reported_as_unreachable(
    game: Path, fika_store: Path, failure: Exception
) -> None:
    """If the error were passed on as it is, this fails: the caller sees a
    launch error instead of the Fika-specific one the app shows with a retry
    button. However the connection fails, the player reads the same single
    sentence: the host named once, and the two things to check -- never the
    wording of the HTTP library, which means nothing to a player."""
    services.set_fika_settings(game, "client", "192.0.2.10", fika_path=fika_store)

    with respx.mock(base_url=HOST_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).mock(side_effect=failure)

        with pytest.raises(FikaHostUnreachableError) as unreachable:
            services.list_host_profiles(game, fika_path=fika_store)

    message = str(unreachable.value)
    assert message == (
        f"The host at {HOST_URL} did not answer. "
        f"Check the address and that the host's server is running."
    )
    assert message.count(HOST_URL) == 1
    assert str(failure) not in message
    assert type(failure).__name__ not in message
