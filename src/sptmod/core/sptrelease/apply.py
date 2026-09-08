"""Path logic shared by applying and undoing an SPT release.

Which files of an unpacked release belong in the game directory, which files
of the installed program set the release no longer provides, which client
build it asks for, and how two SPT versions compare.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sptmod.core.game_dir import (
    BEPINEX_CORE_DIR,
    RUNTIME_DIR_NAMES,
    SPT_DATA_DIR_NAME,
    SPT_OWN_CLIENT_PATHS,
    GameLayout,
    is_game_program_path,
)
from sptmod.core.models import Json
from sptmod.core.sptrelease.client import SptRelease

CLIENT_BUILD_FIELD: Final = "compatibleTarkovVersion"

# Directory names the whitelist keeps out of the repository wherever they
# appear: the caches and logs the server writes while it runs.
RUNTIME_DIR_EXCLUDES: Final[frozenset[str]] = frozenset({"cache", "logs"})

_VERSION_COMPONENTS: Final = 3


@dataclass(frozen=True)
class ReleaseFile:
    """One file of an unpacked release with its place in the game directory.

    Attributes:
        source: The file inside the unpacked release.
        target: Where it belongs, relative to the game directory.
    """

    source: Path
    target: Path


def version_key(version: str) -> tuple[int, int, int]:
    """Return the three numeric components of ``version``, padded with zeros.

    A missing or non-numeric component counts as zero, so an unknown version
    sorts before every published one.
    """
    chunks = version.strip().removeprefix("v").split(".")[:_VERSION_COMPONENTS]
    parts = [int(chunk) if chunk.isdigit() else 0 for chunk in chunks]
    filled = [*parts, 0, 0, 0]
    return filled[0], filled[1], filled[2]


def compare_versions(left: str, right: str) -> int:
    """Return -1, 0 or 1 as ``left`` sorts before, with or after ``right``.

    The first three dot-separated components are compared as numbers, as
    :func:`version_key` reads them.
    """
    first, second = version_key(left), version_key(right)
    if first == second:
        return 0
    return -1 if first < second else 1


def same_version_line(left: str, right: str) -> bool:
    """Return whether both versions share their major and minor component."""
    return version_key(left)[:2] == version_key(right)[:2]


def release_files(unpacked: Path, layout: GameLayout) -> list[ReleaseFile]:
    """Return the files of the release below ``unpacked`` that ``layout`` takes.

    Only files of the SPT program set are taken; player content, mod settings
    and anything else a release may ship are left where they are. Releases
    name the runtime directory ``SPT_Runtime``, which is rewritten to the
    runtime directory the installation actually uses.
    """
    files: list[ReleaseFile] = []
    for source in sorted(_tree(unpacked)):
        target = _to_layout(source.relative_to(unpacked), layout)
        if is_game_program_path(target, layout) and not _is_excluded(target):
            files.append(ReleaseFile(source=source, target=target))
    return files


def installed_program_files(layout: GameLayout) -> list[Path]:
    """Return the SPT program files present in ``layout``, relative to its root.

    That is the set a release replaces: what is in it but not in the release
    is what the release dropped.
    """
    root = layout.root
    found = {
        entry.relative_to(root)
        for entry in (
            *_children(root),
            *_children(root / layout.runtime_dir),
            *_tree(root / layout.spt_data_dir),
            *_tree(root / BEPINEX_CORE_DIR),
            *[entry for own in SPT_OWN_CLIENT_PATHS for entry in _tree(root / own)],
        )
    }
    return sorted(
        entry
        for entry in found
        if is_game_program_path(entry, layout) and not _is_excluded(entry)
    )


def required_client_build(release: SptRelease, unpacked: Path) -> str:
    """Return the Escape from Tarkov build ``release`` needs, empty when unknown.

    The release names its build itself; when it does not, the trailing
    component of ``compatibleTarkovVersion`` in the core configuration of the
    unpacked release answers the same question.
    """
    if release.client_build:
        return release.client_build
    return _core_config_build(unpacked)


def _to_layout(member: Path, layout: GameLayout) -> Path:
    """Rewrite the runtime directory of an archive member to the one in use."""
    if member.parts[0] in RUNTIME_DIR_NAMES:
        return layout.runtime_dir.joinpath(*member.parts[1:])
    return member


def _is_excluded(entry: Path) -> bool:
    """Return whether ``entry`` lies in a cache or log directory."""
    return bool(RUNTIME_DIR_EXCLUDES.intersection(entry.parts[:-1]))


def _children(directory: Path) -> Iterator[Path]:
    """Yield the files directly inside ``directory``."""
    if not directory.is_dir():
        return
    yield from (entry for entry in directory.iterdir() if entry.is_file())


def _tree(directory: Path) -> Iterator[Path]:
    """Yield every file below ``directory``, or ``directory`` itself if it is one."""
    if directory.is_file():
        yield directory
        return
    yield from (entry for entry in directory.rglob("*") if entry.is_file())


def _core_config_build(unpacked: Path) -> str:
    """Return the client build the core configuration of the release names."""
    for name in RUNTIME_DIR_NAMES:
        config = unpacked / name / SPT_DATA_DIR_NAME / "configs" / "core.json"
        compatible = _compatible_version(config)
        build = compatible.rsplit(".", maxsplit=1)[-1]
        if build.isdigit():
            return build
    return ""


def _compatible_version(config: Path) -> str:
    """Return ``compatibleTarkovVersion`` of ``config``, empty when unreadable."""
    try:
        raw = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    try:
        content: Json = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(content, dict):
        return ""
    value = content.get(CLIENT_BUILD_FIELD)
    return value if isinstance(value, str) else ""
