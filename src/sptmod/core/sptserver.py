"""The launcher API of the local SPT server: address, reachability, sign-in.

This is the second module allowed to use ``httpx``: the SPT server is a
different remote service than the Forge, reached with a self-signed
certificate, zlib bodies and PUT writes. Only the calls a launch needs live
here — reachability, the profiles of a Fika host, and sign-in.
"""

import http
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import httpx

from sptmod.core.errors import LaunchError
from sptmod.core.game_dir import GameLayout
from sptmod.core.models import Json

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 6969
REQUEST_TIMEOUT: Final = 10.0
PING_PATH: Final = "/launcher/v2/ping"
LOGIN_PATH: Final = "/launcher/v2/login"
PROFILES_PATH: Final = "/launcher/v2/profiles"
PONG: Final = "Pong!"

# Addresses that mean "every interface" in the server configuration; the game
# and sptmod both reach such a server over the loopback address.
_ANY_HOSTS: Final[tuple[str, ...]] = ("", "0.0.0.0", "::", "[::]")

# Asks the server for a plain JSON answer instead of a zlib-deflated one.
_RAW_RESPONSE_HEADERS: Final[dict[str, str]] = {"responsecompressed": "0"}

type PortState = Literal["free", "spt", "foreign", "timeout"]


@dataclass(frozen=True)
class RemoteProfile:
    """One player profile as another machine's SPT server describes it."""

    profile_id: str
    username: str
    nickname: str
    level: int
    side: str
    has_password: bool



def server_base_url(layout: GameLayout) -> str:
    """Return the base URL the SPT server of ``layout`` listens on.

    Reads ``ip`` and ``port`` from the installation's own HTTP configuration so
    a changed port is honoured. A missing, unreadable or malformed file and an
    address meaning "every interface" both fall back to the loopback default.
    """
    host, port = DEFAULT_HOST, DEFAULT_PORT
    config = _read_http_config(layout.root / layout.http_config_file)
    configured_host = config.get("ip")
    if isinstance(configured_host, str) and configured_host.strip() not in _ANY_HOSTS:
        host = configured_host.strip()
    configured_port = config.get("port")
    if isinstance(configured_port, int) and not isinstance(configured_port, bool):
        port = configured_port
    return f"https://{host}:{port}"


def probe(base_url: str, timeout: float = REQUEST_TIMEOUT) -> PortState:
    """Return who answers at ``base_url``.

    ``"free"`` means nothing accepts a connection there, ``"spt"`` an SPT
    server answered its greeting, ``"timeout"`` that something took the
    connection but did not answer in time, and ``"foreign"`` that something
    else holds the address.
    """
    try:
        response = _request("GET", base_url, PING_PATH, timeout, None)
    except httpx.ConnectError:
        return "free"
    except httpx.TimeoutException:
        return "timeout"
    except httpx.HTTPError:
        return "foreign"
    if response.status_code != http.HTTPStatus.OK:
        return "foreign"
    payload = _decode(response.content)
    if isinstance(payload, dict) and payload.get("Response") == PONG:
        return "spt"
    return "foreign"


def login(base_url: str, username: str, timeout: float = REQUEST_TIMEOUT) -> bool:
    """Sign ``username`` in at the SPT server and return whether it knows it.

    Raises:
        LaunchError: The server stopped answering.
    """
    body = zlib.compress(
        json.dumps({"username": username}).encode("utf-8"), zlib.Z_BEST_COMPRESSION
    )
    try:
        response = _request("PUT", base_url, LOGIN_PATH, timeout, body)
    except httpx.HTTPError as error:
        raise LaunchError(
            f"The SPT server at {base_url} stopped answering: {error}"
        ) from error
    payload = _decode(response.content)
    return isinstance(payload, dict) and payload.get("Response") is True


def host_profiles(base_url: str, timeout: float = REQUEST_TIMEOUT) -> list[RemoteProfile]:
    """Return the player profiles the SPT server at ``base_url`` offers.

    A machine that joins a Fika session has no profile files of its own: the
    profiles it can play belong to the host, so they are read over the network
    in the order the host sends them. Entries the server describes in an
    unexpected shape are left out.

    Raises:
        LaunchError: The server stopped answering.
    """
    try:
        response = _request("GET", base_url, PROFILES_PATH, timeout, None)
    except httpx.HTTPError as error:
        raise LaunchError(
            f"The SPT server at {base_url} stopped answering: {error}"
        ) from error
    payload = _decode(response.content)
    listed = payload.get("Response") if isinstance(payload, dict) else None
    if not isinstance(listed, list):
        return []
    read = (_remote_profile(entry) for entry in listed)
    return [profile for profile in read if profile is not None]


def _remote_profile(entry: Json) -> RemoteProfile | None:
    """Read one profile of a server's profile list, ``None`` when it has no id."""
    if not isinstance(entry, dict):
        return None
    profile_id = entry.get("profileId")
    if not isinstance(profile_id, str) or not profile_id:
        return None
    username = _text(entry.get("username"))
    level = entry.get("currlvl")
    return RemoteProfile(
        profile_id=profile_id,
        username=username,
        nickname=_text(entry.get("nickname")) or username,
        level=level if isinstance(level, int) and not isinstance(level, bool) else 0,
        side=_text(entry.get("side")),
        has_password=entry.get("hasPassword") is True,
    )


def _text(value: Json) -> str:
    """Return a text field of a server answer, empty when it is none."""
    return value if isinstance(value, str) else ""


def _request(
    method: str, base_url: str, path: str, timeout: float, body: bytes | None
) -> httpx.Response:
    """Send one request to an SPT server.

    Certificate validation is off: every installation generates its own
    self-signed certificate, the local one as much as the one of a Fika host
    a player deliberately named.
    """
    with httpx.Client(verify=False, timeout=timeout) as client:
        return client.request(
            method, f"{base_url}{path}", content=body, headers=_RAW_RESPONSE_HEADERS
        )


def _decode(body: bytes) -> Json:
    """Return the JSON document in ``body``, or ``None`` when it holds none.

    The server answers with plain JSON when asked to, but deflates it when it
    ignores that request, so both encodings are accepted.
    """
    try:
        plain: Json = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        pass
    else:
        return plain
    try:
        deflated: Json = json.loads(zlib.decompress(body))
    except (UnicodeDecodeError, ValueError, zlib.error):
        return None
    return deflated


def _read_http_config(config_file: Path) -> dict[str, Json]:
    """Return the parsed HTTP configuration, empty when it cannot be read."""
    try:
        parsed: Json = json.loads(config_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
