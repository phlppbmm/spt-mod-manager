"""Fika co-op: the role of a game directory and the profiles of its host.

A game directory either plays alone, hosts a co-op session for other players,
or joins one on another machine. The role and the host's address are settings
of the directory; the profiles of a joined host are read from that host,
because a joining machine has no player profiles of its own.
"""

from dataclasses import dataclass
from pathlib import Path

from sptmod.core import sptserver
from sptmod.core.errors import FikaHostUnreachableError, LaunchError
from sptmod.core.fika import (
    FikaRole,
    FikaSettings,
    canonical_base_url,
    default_fika_path,
    load_settings,
    save_settings,
)
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.modsets import store_key

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


@dataclass(frozen=True)
class HostProfile:
    """One player profile a Fika host offers to the players joining it."""

    profile_id: str
    username: str
    nickname: str
    level: int
    side: str
    has_password: bool


def get_fika_settings(path: Path, *, fika_path: Path | None = None) -> FikaSettings:
    """Return the Fika role of the game directory ``path``.

    Reads only, and never fails: a game directory nothing is stored about, and
    a store that cannot be read, both report a directory that plays alone.

    Args:
        path: The game directory.
        fika_path: Fika settings file; defaults to :func:`default_fika_path`.

    Returns:
        The role, the host address as it was typed and, when that address can
        be used, the base URL the game is pointed at.
    """
    return load_settings(fika_path or default_fika_path(), store_key(path))


def set_fika_settings(
    path: Path,
    role: FikaRole = "none",
    host_address: str = "",
    *,
    fika_path: Path | None = None,
) -> FikaSettings:
    """Store the Fika role of the game directory ``path``.

    The address is kept as it was typed and checked right here, so an address
    that cannot be reached is refused while a player is looking at it rather
    than when they want to play. A directory that joins a host needs one. A
    directory that plays alone is stored without an address and without
    checking what was passed, so an address left over from a Fika role can
    never stand between a player and playing alone.

    Args:
        path: The game directory.
        role: ``none`` to play alone, ``host`` to run the session, ``client``
            to join one on another machine.
        host_address: The address of the host, for example ``192.168.1.20``
            or ``pc.local:7000``.
        fika_path: Fika settings file; defaults to :func:`default_fika_path`.

    Returns:
        The stored settings, including the base URL the address leads to.

    Raises:
        InvalidMetadataError: the address cannot be used, or the game
            directory joins a host without naming one.
        OSError: the settings cannot be written.
    """
    typed = "" if role == "none" else host_address.strip()
    base_url = canonical_base_url(typed)
    if role == "client" and not base_url:
        raise InvalidMetadataError("a Fika client needs the address of the host")
    settings = FikaSettings(role=role, host_address=typed, base_url=base_url)
    save_settings(fika_path or default_fika_path(), store_key(path), settings)
    return settings


def list_host_profiles(
    path: Path, *, fika_path: Path | None = None
) -> list[HostProfile]:
    """Return the player profiles the Fika host of ``path`` offers.

    The profiles come from the host in the order it lists them; a profile that
    is protected by a password is reported as such, because it cannot be
    played from another machine.

    Args:
        path: The game directory.
        fika_path: Fika settings file; defaults to :func:`default_fika_path`.

    Returns:
        The profiles of the host.

    Raises:
        FikaHostUnreachableError: no usable host address is set for the game
            directory, or the host does not answer.
    """
    settings = get_fika_settings(path, fika_path=fika_path)
    if not settings.base_url:
        raise FikaHostUnreachableError(
            "No usable address is set for the Fika host. Set it in the settings."
        )
    try:
        profiles = sptserver.host_profiles(settings.base_url)
    except LaunchError as error:
        raise FikaHostUnreachableError(
            f"The host at {settings.base_url} did not answer. Check the "
            f"address and that the host's server is running."
        ) from error
    return [
        HostProfile(
            profile_id=profile.profile_id,
            username=profile.username,
            nickname=profile.nickname,
            level=profile.level,
            side=profile.side,
            has_password=profile.has_password,
        )
        for profile in profiles
    ]
