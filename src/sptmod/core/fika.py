"""The Fika role of one game directory: hosting a co-op session or joining one.

Fika lets several players share one SPT server: one of them runs it, the others
point their game at that machine. Which of the two a game directory is, and
where the host is reached, lives outside every game directory in one JSON
document keyed by the resolved path of the game directory, next to the mod
sets.

Like the favorites list this store is read tolerantly: a document that cannot
be read means "no Fika" rather than an error, because a damaged setting must
never stand between a player and their game.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

import platformdirs

from sptmod.core.models import Json
from sptmod.core.modmeta import InvalidMetadataError

FIKA_FILE_NAME: Final = "fika.json"

STORE_VERSION: Final = 1

DEFAULT_HOST_PORT: Final = 6969

type FikaRole = Literal["none", "host", "client"]

FIKA_ROLES: Final[tuple[FikaRole, ...]] = ("none", "host", "client")

_APP_NAME: Final = "sptmod"
_TEMP_PREFIX: Final = ".fika-"
_VERSION_KEY: Final = "version"
_GAMES_KEY: Final = "games"
_ROLE_KEY: Final = "role"
_ADDRESS_KEY: Final = "host_address"
_SCHEME_SEPARATOR: Final = "://"
_DEFAULT_SCHEME: Final = "https"
_SCHEMES: Final[tuple[str, ...]] = ("http", "https")
_HOST_PATHS: Final[tuple[str, ...]] = ("", "/")
_QUERY_MARKS: Final[tuple[str, ...]] = ("?", "#")
_MAX_PORT: Final = 65535


@dataclass(frozen=True)
class FikaSettings:
    """The Fika role of one game directory and the host it plays on.

    ``host_address`` is the address as a player typed it; ``base_url`` is that
    address as the game is pointed at it, and stays empty when the address
    cannot be used. It is derived on every read instead of being stored.
    """

    role: FikaRole = "none"
    host_address: str = ""
    base_url: str = ""


def default_fika_path() -> Path:
    """Return the Fika settings file path in the user's config directory."""
    config_dir = platformdirs.user_config_path(_APP_NAME, appauthor=False)
    return config_dir / FIKA_FILE_NAME


def load_settings(store: Path, key: str) -> FikaSettings:
    """Return the Fika settings stored in ``store`` for the game directory ``key``.

    A missing, unreadable or malformed store, and an entry that does not name
    a known role, all yield the settings of a game directory that plays alone.
    An address that no longer canonicalises keeps its text and loses its base
    URL, so a launch can say what is wrong instead of silently playing solo.
    """
    entry = _entry(store, key)
    address = entry.get(_ADDRESS_KEY)
    return FikaSettings(
        role=_role(entry.get(_ROLE_KEY)),
        host_address=address if isinstance(address, str) else "",
        base_url=_usable_base_url(address),
    )


def save_settings(store: Path, key: str, settings: FikaSettings) -> None:
    """Store ``settings`` in ``store`` as the Fika settings of ``key``.

    The entries of every other game directory are kept, and the derived base
    URL is not written: it follows from the address on every read. The file is
    written next to its target and moved into place, so a crash mid-write
    leaves the previous document intact.

    Raises:
        OSError: the directory or the file cannot be written.
    """
    games = _games(store)
    games[key] = {_ROLE_KEY: settings.role, _ADDRESS_KEY: settings.host_address}
    document: Json = {_VERSION_KEY: STORE_VERSION, _GAMES_KEY: games}
    store.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=store.parent, prefix=_TEMP_PREFIX)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(document, temp_file, indent=2)
            temp_file.write("\n")
        os.replace(temp_path, store)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def canonical_base_url(address: str) -> str:
    """Return ``address`` as the base URL a game is pointed at.

    An empty address stays empty, which is what "no host is set" looks like.
    Everything else becomes ``scheme://host:port``: without a scheme the
    address is reached over ``https``, without a port over
    :data:`DEFAULT_HOST_PORT`, and the host is lower-cased, keeping the
    brackets an IPv6 address is written in.

    Raises:
        InvalidMetadataError: the address is not a host with an optional
            scheme and port. A ``?`` or a ``#`` makes it unusable wherever it
            stands, including at the very end, where it would otherwise open
            an empty query or fragment nobody typed on purpose.
    """
    text = address.strip()
    if not text:
        return ""
    if any(character.isspace() for character in text):
        raise InvalidMetadataError(f"{address!r} is no usable address for a Fika host")
    if _SCHEME_SEPARATOR in text:
        if text.split(_SCHEME_SEPARATOR, 1)[0].lower() not in _SCHEMES:
            raise InvalidMetadataError(
                f"a Fika host is reached over http or https, not {address!r}"
            )
    else:
        text = f"{_DEFAULT_SCHEME}{_SCHEME_SEPARATOR}{text}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise InvalidMetadataError(
            f"{address!r} is no usable address for a Fika host: {error}"
        ) from error
    host = parts.hostname or ""
    unusable = (
        not host
        or "@" in parts.netloc
        or any(character in text for character in _QUERY_MARKS)
        or parts.path not in _HOST_PATHS
        or (port is not None and not 1 <= port <= _MAX_PORT)
    )
    if unusable:
        raise InvalidMetadataError(f"{address!r} is no usable address for a Fika host")
    written = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}{_SCHEME_SEPARATOR}{written}:{port or DEFAULT_HOST_PORT}"


def _games(store: Path) -> dict[str, Json]:
    """Return the per-game-directory entries of ``store``, empty on any damage."""
    try:
        document: Json = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    games = document.get(_GAMES_KEY) if isinstance(document, dict) else None
    return dict(games) if isinstance(games, dict) else {}


def _entry(store: Path, key: str) -> dict[str, Json]:
    """Return what ``store`` knows about the game directory ``key``."""
    entry = _games(store).get(key)
    return entry if isinstance(entry, dict) else {}


def _role(value: Json) -> FikaRole:
    """Return the stored role, ``"none"`` when it is not one of the known ones."""
    for role in FIKA_ROLES:
        if value == role:
            return role
    return "none"


def _usable_base_url(address: Json) -> str:
    """Return the base URL of a stored address, empty when it cannot be used."""
    if not isinstance(address, str):
        return ""
    try:
        return canonical_base_url(address)
    except InvalidMetadataError:
        return ""
