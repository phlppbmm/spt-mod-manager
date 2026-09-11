"""Build output builders for the local mod source tests.

A local source is a project folder whose ``build`` folder holds the archives a
player's own build produced. These helpers write such archives, with a
caller-chosen modification time where a test needs the resolution order to be
deterministic instead of depending on how fast the file system ticks.

A plain import module, not a pytest plugin: the local source suites import it
the way they import ``forge_json``.
"""

import json
import os
import zipfile
from collections.abc import Mapping
from pathlib import Path

BUILD_DIR_NAME = "build"


def write_build(
    folder: Path,
    archive_name: str,
    *,
    mod_name: str = "CoolMod",
    version: str = "1.0.0",
    spt_range: str = "",
    files: Mapping[str, str] | None = None,
    mtime: float | None = None,
) -> Path:
    """Write one build archive into the ``build`` folder of ``folder``.

    The archive carries ``SPT/user/mods/<mod_name>/package.json`` plus every
    entry of ``files`` (paths relative to the game directory), so installing it
    is an ordinary server mod install. ``spt_range`` declares the SPT versions
    the mod says it is for, which is what makes an install report a warning.
    ``mtime`` sets the modification time the resolution rule sorts by.
    """
    build = folder / BUILD_DIR_NAME
    build.mkdir(parents=True, exist_ok=True)
    archive = build / archive_name
    package = {"name": mod_name, "version": version, "author": "Fork Author"}
    if spt_range:
        package["sptVersion"] = spt_range
    members = {
        f"SPT/user/mods/{mod_name}/package.json": json.dumps(package),
        **(files or {}),
    }
    with zipfile.ZipFile(archive, "w") as zip_file:
        for member, content in members.items():
            zip_file.writestr(member, content)
    if mtime is not None:
        os.utime(archive, (mtime, mtime))
    return archive


def write_archive(folder: Path, archive_name: str, members: Mapping[str, str]) -> Path:
    """Write an archive of ``members`` into the ``build`` folder of ``folder``.

    Used for archives that are no mod at all, which a build folder regularly
    holds next to the mods it produced.
    """
    build = folder / BUILD_DIR_NAME
    build.mkdir(parents=True, exist_ok=True)
    archive = build / archive_name
    with zipfile.ZipFile(archive, "w") as zip_file:
        for member, content in members.items():
            zip_file.writestr(member, content)
    return archive


def write_plain_file(folder: Path, name: str, *, mtime: float | None = None) -> Path:
    """Write a file that is no mod archive into the ``build`` folder of ``folder``.

    Used to pin that the resolution rule looks at the supported archive
    suffixes only, no matter how recently anything else was written.
    """
    build = folder / BUILD_DIR_NAME
    build.mkdir(parents=True, exist_ok=True)
    path = build / name
    path.write_text("not an archive\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path
