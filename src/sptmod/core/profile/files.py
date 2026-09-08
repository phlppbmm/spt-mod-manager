"""Reading, describing and writing the game's own profile files."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sptmod.core.game_dir import GameLayout
from sptmod.core.models import Json, ProfileSummary
from sptmod.core.profile.nodes import dict_at

UNREADABLE_PROFILE: Final = "This file could not be read as a player profile."


def profile_paths(layout: GameLayout) -> list[Path]:
    """Return the profile files of the installation, sorted by name.

    These are the ``*.json`` files directly in the profiles directory of
    ``layout``; the ``backups/`` directory below it is skipped. Returns an
    empty list if the profiles directory does not exist.
    """
    profiles_dir = layout.root / layout.profiles_dir
    backups_dir = layout.root / layout.profile_backups_dir
    if not profiles_dir.is_dir():
        return []
    return sorted(
        path
        for path in profiles_dir.glob("*.json")
        if path.is_file() and path.parent != backups_dir
    )


def profile_summaries(layout: GameLayout) -> list[ProfileSummary]:
    """Describe every profile file of the installation, sorted by file name.

    Reads the PMC character of each file returned by :func:`profile_paths`.
    A file that cannot be read or is no valid JSON is reported as unreadable
    with a message that can be shown as is; the other files are still
    described. Missing character data is no error: the profile is readable and
    simply carries no name, level or faction yet.
    """
    return [_summary(path) for path in profile_paths(layout)]


def _summary(path: Path) -> ProfileSummary:
    """Describe one profile file by its PMC character and its file data."""
    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    parsed = parse_profile(path)
    if parsed is None:
        return ProfileSummary(
            file=path.name,
            profile_id=path.stem,
            modified=modified,
            readable=False,
            error=UNREADABLE_PROFILE,
        )
    info = dict_at(parsed.root, "characters", "pmc", "Info")
    nickname = info.get("Nickname")
    level = info.get("Level")
    side = info.get("Side")
    return ProfileSummary(
        file=path.name,
        profile_id=path.stem,
        nickname=nickname if isinstance(nickname, str) else "",
        level=level if isinstance(level, int) else 0,
        side=side if isinstance(side, str) else "",
        modified=modified,
    )


@dataclass(frozen=True)
class ParsedProfile:
    """A profile file read and parsed once, ready to match several mods against."""

    name: str
    text: str
    root: dict[str, Json]


def parse_profile(profile_path: Path) -> ParsedProfile | None:
    """Parse a profile JSON file into a ParsedProfile if valid.

    Returns None if the file does not exist, cannot be read, is not UTF-8
    encoded, or contains invalid JSON.
    """
    try:
        text = profile_path.read_text(encoding="utf-8-sig")
        profile: Json = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    root = profile if isinstance(profile, dict) else {}
    return ParsedProfile(name=profile_path.stem, text=text, root=root)


def write_profile(path: Path, data: dict[str, Json]) -> None:
    """Write ``data`` to ``path`` the way the game writes profiles itself.

    Serialises without indentation, without a trailing newline and with
    non-ASCII characters kept literally, so a document that was not changed is
    written back byte for byte. The bytes go to a temporary file next to
    ``path`` that is then moved into place, so an interrupted write cannot
    leave behind a half-written profile.

    Raises:
        OSError: the profile could not be written.
    """
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(_dumps(data).encode())
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _dumps(data: dict[str, Json]) -> str:
    """Return the JSON text of a profile document in the game's own format."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
