"""Favorite mods, stored by Forge mod id in the user's config directory.

The list lives outside every game directory, so it survives switching game
folders and deleting the catalog cache. A file that cannot be read or does not
hold a list of ids counts as "no favorites yet" — a marked mod is a
convenience, never a truth about the installation.
"""

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import platformdirs

FAVORITES_FILE_NAME: Final = "favorites.json"

_APP_NAME: Final = "sptmod"
_TEMP_PREFIX: Final = ".favorites-"


def default_favorites_path() -> Path:
    """Return the favorites file path in the user's config directory."""
    config_dir = platformdirs.user_config_path(_APP_NAME, appauthor=False)
    return config_dir / FAVORITES_FILE_NAME


def load_favorites(path: Path) -> list[int]:
    """Return the favored Forge mod ids stored in ``path``.

    A missing, unreadable or malformed file yields an empty list instead of an
    error.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    ids: list[int] = []
    for item in payload:
        if not isinstance(item, int) or isinstance(item, bool):
            return []
        ids.append(item)
    return sorted(set(ids))


def save_favorites(path: Path, mod_ids: Sequence[int]) -> None:
    """Write ``mod_ids`` to ``path``, sorted and without duplicates.

    The file is written next to its target and moved into place, so a crash
    mid-write leaves the previous list intact.

    Raises:
        OSError: the directory or the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=_TEMP_PREFIX)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(sorted(set(mod_ids)), temp_file)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
