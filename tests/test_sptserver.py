"""Tests for ``sptmod.core.sptserver``: address derivation, the port probe,
sign-in and the profile list of a server's launcher API. All HTTP is mocked
with respx; no test opens a real socket.
"""

import json
import zlib
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from sptmod.core.errors import LaunchError
from sptmod.core.game_dir import GameLayout
from sptmod.core.sptserver import (
    LOGIN_PATH,
    PING_PATH,
    PROFILES_PATH,
    RemoteProfile,
    host_profiles,
    login,
    probe,
    server_base_url,
)

BASE_URL = "https://127.0.0.1:6969"


def _layout(root: Path) -> GameLayout:
    """Build a GameLayout for SPT_Runtime directory structure.

    Creates a layout with root at the given path and runtime_dir_name="SPT_Runtime",
    used for server_base_url testing.
    """
    return GameLayout(root=root, runtime_dir_name="SPT_Runtime")


def _write_http_config(root: Path, config: object) -> None:
    """Write http.json file to SPT_Runtime/SPT_Data/configs for server config.

    Creates parent directories and writes config as JSON string (if dict) or raw
    string (if already string). Used to set up server_base_url test fixtures.
    """
    config_dir = root / "SPT_Runtime" / "SPT_Data" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "http.json").write_text(config if isinstance(config, str) else json.dumps(config), encoding="utf-8")


# --------------------------------------------------------------------------
# server_base_url
# --------------------------------------------------------------------------


def test_server_base_url_reads_ip_and_port_from_http_json(tmp_path: Path) -> None:
    """server_base_url extracts ip and port from http.json and builds HTTPS URL.

    http.json contains ip="10.0.0.5", port=7000; server_base_url returns
    "https://10.0.0.5:7000".
    """
    _write_http_config(tmp_path, {"ip": "10.0.0.5", "port": 7000})

    assert server_base_url(_layout(tmp_path)) == "https://10.0.0.5:7000"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "", "  "])
def test_server_base_url_maps_any_interface_hosts_to_loopback(tmp_path: Path, host: str) -> None:
    """server_base_url() maps any-interface addresses (0.0.0.0, ::, etc.) to loopback.

    Any-interface placeholders (0.0.0.0, ::, [::]empty string, whitespace) are
    replaced with 127.0.0.1 so local connections work.
    """
    _write_http_config(tmp_path, {"ip": host, "port": 6969})

    assert server_base_url(_layout(tmp_path)) == "https://127.0.0.1:6969"


def test_server_base_url_falls_back_when_the_file_is_missing(tmp_path: Path) -> None:
    """server_base_url() uses defaults (127.0.0.1:6969) when http.json is missing."""
    assert server_base_url(_layout(tmp_path)) == "https://127.0.0.1:6969"


def test_server_base_url_falls_back_when_the_file_is_invalid_json(
    tmp_path: Path,
) -> None:
    """server_base_url returns defaults when http.json is not valid JSON.

    http.json contains "not json"; parse fails. server_base_url returns
    "https://127.0.0.1:6969" (defaults).
    """
    _write_http_config(tmp_path, "not json")

    assert server_base_url(_layout(tmp_path)) == "https://127.0.0.1:6969"


def test_server_base_url_falls_back_when_the_file_is_not_a_json_object(
    tmp_path: Path,
) -> None:
    """server_base_url returns defaults when http.json is a JSON array not object.

    http.json contains [1, 2, 3]; not an object. server_base_url returns
    "https://127.0.0.1:6969" (defaults).
    """
    _write_http_config(tmp_path, [1, 2, 3])

    assert server_base_url(_layout(tmp_path)) == "https://127.0.0.1:6969"


def test_server_base_url_ignores_a_non_string_ip_field(tmp_path: Path) -> None:
    """server_base_url ignores ip when not a string type.

    http.json has ip=12345 (int, not string); server_base_url uses default
    IP 127.0.0.1 but keeps port=7000, returning "https://127.0.0.1:7000".
    """
    _write_http_config(tmp_path, {"ip": 12345, "port": 7000})

    assert server_base_url(_layout(tmp_path)) == "https://127.0.0.1:7000"


def test_server_base_url_ignores_a_non_int_port_field(tmp_path: Path) -> None:
    """server_base_url ignores port when not an int type.

    http.json has ip="10.0.0.5", port="7000" (string, not int); server_base_url
    uses port default 6969, returning "https://10.0.0.5:6969".
    """
    _write_http_config(tmp_path, {"ip": "10.0.0.5", "port": "7000"})

    assert server_base_url(_layout(tmp_path)) == "https://10.0.0.5:6969"


def test_server_base_url_ignores_a_boolean_port_field(tmp_path: Path) -> None:
    """server_base_url() explicitly rejects bool (despite int subclass) as port."""
    # bool is an int subclass in Python; the guard must reject it explicitly.
    _write_http_config(tmp_path, {"ip": "10.0.0.5", "port": True})

    assert server_base_url(_layout(tmp_path)) == "https://10.0.0.5:6969"


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------


def test_probe_reports_free_when_nothing_accepts_the_connection() -> None:
    """probe returns 'free' when connection to server port is refused.

    GET PING_PATH raises httpx.ConnectError (connection refused); probe() catches
    it and returns "free" (port not listening).
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=httpx.ConnectError("refused"))

        assert probe(BASE_URL) == "free"


def test_probe_reports_spt_for_a_raw_json_pong() -> None:
    """probe returns 'spt' when server responds with valid JSON pong.

    GET PING_PATH returns HTTP 200 with {"Response": "Pong!"}; probe() parses
    and returns "spt" (valid SPT server detected).
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PING_PATH).respond(200, json={"Response": "Pong!"})

        assert probe(BASE_URL) == "spt"


def test_probe_reports_spt_for_a_zlib_compressed_pong() -> None:
    """probe returns 'spt' even when server ignores responsecompressed:0 header.

    Server responds with zlib-compressed pong despite responsecompressed:0 header.
    probe() fallback decompresses the body and recognizes the pong, returns "spt".
    """
    # Covers the _decode fallback: a server that ignores responsecompressed:0.
    body = zlib.compress(json.dumps({"Response": "Pong!"}).encode())
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PING_PATH).respond(200, content=body)

        assert probe(BASE_URL) == "spt"


def test_probe_sends_the_raw_response_header() -> None:
    """probe() sets the responsecompressed:0 header to request uncompressed responses."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        route = router.get(PING_PATH).respond(200, json={"Response": "Pong!"})

        probe(BASE_URL)

    assert route.calls.last.request.headers["responsecompressed"] == "0"


@pytest.mark.parametrize(
    "status_code,body_kwargs",
    [
        (200, {"json": {"Response": "not-pong"}}),
        (200, {"content": b"not json at all"}),
        (500, {"json": {"Response": "Pong!"}}),
    ],
)
def test_probe_reports_foreign_for_a_stranger(status_code: int, body_kwargs: dict[str, Any]) -> None:
    """probe() returns 'foreign' for non-SPT responses: wrong response, non-JSON, bad status.

    Parameterized cases: wrong response text, non-JSON body, and bad HTTP status.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PING_PATH).respond(status_code, **body_kwargs)

        assert probe(BASE_URL) == "foreign"


def test_probe_reports_foreign_on_a_non_connect_non_timeout_transport_error() -> None:
    """probe returns 'foreign' for protocol errors excluding connect/timeout.

    GET PING_PATH raises httpx.RemoteProtocolError (bad response data). probe()
    catches it and returns "foreign" (something answered but wasn't SPT).
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=httpx.RemoteProtocolError("garbled response"))

        assert probe(BASE_URL) == "foreign"


def test_probe_reports_timeout_for_a_connection_that_never_answers() -> None:
    """probe returns 'timeout' when connection is slow and httpx times out.

    GET PING_PATH raises httpx.ReadTimeout; probe() catches it and returns
    "timeout" (server not answering in time).
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=httpx.ReadTimeout("slow"))

        assert probe(BASE_URL) == "timeout"


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


def test_login_sends_a_zlib_compressed_put_body_and_the_raw_response_header() -> None:
    """login() sends zlib-compressed username and responsecompressed:0 header."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        route = router.put(LOGIN_PATH).respond(200, json={"Response": True})

        result = login(BASE_URL, "0Sullivan")

    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.headers["responsecompressed"] == "0"
    assert json.loads(zlib.decompress(request.content)) == {"username": "0Sullivan"}
    assert result is True


def test_login_returns_false_when_the_server_does_not_know_the_profile() -> None:
    """login() returns False when the server rejects the profile (Response: false)."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.put(LOGIN_PATH).respond(200, json={"Response": False})

        assert login(BASE_URL, "nobody") is False


def test_login_accepts_a_zlib_compressed_response() -> None:
    """login decompresses zlib responses in addition to raw JSON.

    Server PUT LOGIN_PATH returns zlib-compressed {"Response": true}; login()
    decompresses and parses it, returns True.
    """
    body = zlib.compress(json.dumps({"Response": True}).encode())
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.put(LOGIN_PATH).respond(200, content=body)

        assert login(BASE_URL, "0Sullivan") is True


def test_login_returns_false_for_an_undecodable_response() -> None:
    """login returns False when response is not valid JSON or zlib.

    Server PUT LOGIN_PATH returns HTTP 200 with raw garbage bytes (not JSON or
    zlib). login() fails to parse and returns False (not a valid SPT response).
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.put(LOGIN_PATH).respond(200, content=b"garbage")

        assert login(BASE_URL, "0Sullivan") is False


def test_login_raises_launch_error_when_the_server_stops_answering() -> None:
    """login() raises LaunchError when the server connection fails (not profile rejection)."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.put(LOGIN_PATH).mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(LaunchError):
            login(BASE_URL, "0Sullivan")


# --------------------------------------------------------------------------
# host_profiles
# --------------------------------------------------------------------------


def _mini_profile(profile_id: str, **overrides: Any) -> dict[str, Any]:
    """Build one mini profile as the SPT launcher API lists it."""
    entry: dict[str, Any] = {
        "profileId": profile_id,
        "username": "0Sullivan",
        "nickname": "TesterNick",
        "currlvl": 12,
        "side": "Usec",
        "edition": "Edge Of Darkness",
        "hasPassword": False,
    }
    entry.update(overrides)
    return entry


def test_host_profiles_reports_every_field_of_the_listed_profiles_in_order() -> None:
    """host_profiles maps the launcher API's mini profiles field by field.

    The server answers with two profiles; both come back as RemoteProfile in
    the order the server sent them, with profileId, username, nickname,
    currlvl, side and hasPassword mapped onto the dataclass. If the body were
    `pass`, nothing would be returned at all -- a joining machine, which has
    no profile files, would have nothing to play.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(
            200,
            json={
                "Response": [
                    _mini_profile("aaa111", nickname="First", currlvl=42),
                    _mini_profile("bbb222", username="Nikita", nickname="Second", side="Bear", hasPassword=True),
                ]
            },
        )

        profiles = host_profiles(BASE_URL)

    assert profiles == [
        RemoteProfile(
            profile_id="aaa111", username="0Sullivan", nickname="First", level=42, side="Usec", has_password=False
        ),
        RemoteProfile(
            profile_id="bbb222", username="Nikita", nickname="Second", level=12, side="Bear", has_password=True
        ),
    ]


def test_host_profiles_sends_a_get_with_the_raw_response_header() -> None:
    """host_profiles asks the launcher API for the profile list uncompressed.

    The recorded request must be a GET to PROFILES_PATH carrying
    responsecompressed: 0, the header every call of this module sends; without
    it the server answers with a deflated body.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        route = router.get(PROFILES_PATH).respond(200, json={"Response": []})

        host_profiles(BASE_URL)

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["responsecompressed"] == "0"


def test_host_profiles_accepts_a_zlib_compressed_answer() -> None:
    """host_profiles reads a deflated body, like probe and login do.

    A server that ignores responsecompressed: 0 answers with a zlib body; the
    profile in it must still be reported instead of the list coming back
    empty.
    """
    body = zlib.compress(json.dumps({"Response": [_mini_profile("aaa111")]}).encode())
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(200, content=body)

        assert [profile.profile_id for profile in host_profiles(BASE_URL)] == ["aaa111"]


def test_host_profiles_falls_back_to_the_account_name_without_a_nickname() -> None:
    """A profile without a nickname is reported under its account name.

    The server lists a profile whose nickname is empty; the mapped profile
    carries the username instead, because a nameless entry cannot be picked
    from a list.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(200, json={"Response": [_mini_profile("aaa111", nickname="")]})

        assert [profile.nickname for profile in host_profiles(BASE_URL)] == ["0Sullivan"]


def test_host_profiles_reports_a_profile_the_server_describes_with_nothing_but_an_id() -> None:
    """Fields of an unexpected type are reported as empty, not passed on.

    The server lists an entry whose level, side and password flag have the
    wrong types; the profile is still offered, with the empty values of its
    dataclass, so one odd field does not cost a player their profile.
    """
    entry = {"profileId": "aaa111", "currlvl": True, "side": 5, "hasPassword": "yes"}
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(200, json={"Response": [entry]})

        assert host_profiles(BASE_URL) == [
            RemoteProfile(profile_id="aaa111", username="", nickname="", level=0, side="", has_password=False)
        ]


@pytest.mark.parametrize(
    "listed",
    [
        pytest.param([{"username": "Nameless"}], id="entry-without-a-profile-id"),
        pytest.param([{"profileId": ""}], id="entry-with-an-empty-profile-id"),
        pytest.param([{"profileId": 42}], id="profile-id-is-a-number"),
        pytest.param(["aaa111"], id="entry-is-a-string"),
        pytest.param([["aaa111"]], id="entry-is-a-list"),
    ],
)
def test_host_profiles_leaves_out_an_entry_it_cannot_identify(listed: list[Any]) -> None:
    """An entry without a usable profile id is left out of the list.

    The profile id is the token the game is started with, so an entry the
    server describes without one cannot be played and must not be offered.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(200, json={"Response": listed})

        assert host_profiles(BASE_URL) == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"Response": "not a list"}, id="response-is-a-string"),
        pytest.param({"Response": {"aaa111": {}}}, id="response-is-an-object"),
        pytest.param({}, id="no-response-key"),
        pytest.param([1, 2, 3], id="answer-is-not-an-object"),
    ],
)
def test_host_profiles_reports_no_profiles_for_an_answer_it_cannot_read(payload: Any) -> None:
    """An answer that holds no profile list is reported as no profiles.

    Anything but a list under "Response" -- a string, an object, a missing key
    or an answer that is no object at all -- yields an empty list instead of
    raising, because the caller shows "no profiles" rather than a stack trace.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(200, json=payload)

        assert host_profiles(BASE_URL) == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("refused"), id="connection-refused"),
        pytest.param(httpx.ReadTimeout("slow"), id="no-answer-in-time"),
        pytest.param(httpx.RemoteProtocolError("garbled"), id="garbled-answer"),
    ],
)
def test_host_profiles_raises_launch_error_when_the_server_stops_answering(failure: Exception) -> None:
    """A transport failure is raised as LaunchError naming the server.

    Every way the request can fail -- refused, timed out, garbled -- ends in
    the same error as login, so the launch reports one unreachable host
    instead of an httpx exception.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(PROFILES_PATH).mock(side_effect=failure)

        with pytest.raises(LaunchError, match=BASE_URL):
            host_profiles(BASE_URL)
