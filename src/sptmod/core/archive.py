"""Archive handling: extraction and normalization to the game-dir layout."""

import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import py7zr
import py7zr.exceptions
import rarfile

from sptmod.core.errors import ArchiveError
from sptmod.core.models import ModType

SERVER_MODS_TARGET: Final = Path("SPT/user/mods")
CLIENT_PLUGINS_TARGET: Final = Path("BepInEx/plugins")
SUPPORTED_SUFFIXES: Final[tuple[str, ...]] = (".7z", ".zip", ".rar")

RAR_INSTALL_HINT: Final = (
    "no rar backend found: install 'unrar' or 'bsdtar' (libarchive) and make "
    "sure it is on PATH"
)

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_MAX_LISTED_ENTRIES: Final = 30

type _AnchorKind = Literal["server", "client"]


@dataclass(frozen=True)
class AnchorMapping:
    """One extracted source path and its target relative to the game directory.

    Attributes:
        source: A directory path within the extracted archive that contains mod content.
        target: The destination path in the game directory using canonical casing
            (``SPT/user/mods/...`` or ``BepInEx/plugins/...``).
    """

    source: Path
    target: Path


@dataclass(frozen=True)
class ArchiveLayout:
    """The normalized content of an extracted mod archive.

    Attributes:
        mod_type: The mod classification: ``'server'`` for only SPT user mods,
            ``'client'`` for only BepInEx plugins, or ``'hybrid'`` when both
            are present.
        mappings: The source-to-target anchor mappings discovered in the
            archive; each entry pairs an archive path with its canonical
            destination in the game directory.
        files: All files under the anchors, listed with their canonical
            target paths.
    """

    mod_type: ModType
    mappings: list[AnchorMapping] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def rar_backend_available() -> bool:
    """Report whether ``rarfile`` finds a usable backend (``unrar`` or ``bsdtar``)."""
    try:
        rarfile.tool_setup()
    except rarfile.RarCannotExec:
        return False
    return True


def validate_member_paths(members: Sequence[str]) -> None:
    """Reject archive member names that would escape the extraction directory.

    Absolute paths, drive-relative Windows paths and any ``..`` component are
    refused before a single byte is written.

    Raises:
        ArchiveError: at least one member is unsafe; the message names it.
    """
    for member in members:
        normalized = member.replace("\\", "/")
        unsafe = (
            normalized.startswith("/")
            or _WINDOWS_DRIVE.match(normalized) is not None
            or any(part == ".." for part in normalized.split("/"))
        )
        if unsafe:
            raise ArchiveError(f"unsafe path in archive (path traversal): {member}")


def extract(archive_path: Path, dest_temp: Path) -> None:
    """Extract ``archive_path`` into the existing directory ``dest_temp``.

    Dispatches on the suffix: ``.7z`` via py7zr, ``.zip`` via zipfile, ``.rar``
    via rarfile. Member paths are validated for absolute paths, drive-relative
    Windows paths, and ``.`` components before any byte is written.

    Raises:
        ArchiveError: unsupported archive suffix, password-protected archive
            (named as such in the message), corrupt or unreadable archive,
            unsafe member path (path traversal), or ``.rar`` format without
            an installed backend (the message includes an installation hint).
    """
    suffix = archive_path.suffix.lower()
    if suffix == ".zip":
        _extract_zip(archive_path, dest_temp)
    elif suffix == ".7z":
        _extract_7z(archive_path, dest_temp)
    elif suffix == ".rar":
        _extract_rar(archive_path, dest_temp)
    else:
        raise ArchiveError(
            f"unsupported archive format '{suffix}' ({archive_path.name}); "
            f"supported: {', '.join(SUPPORTED_SUFFIXES)}"
        )


def _extract_zip(archive_path: Path, dest_temp: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as zip_file:
            names = zip_file.namelist()
            validate_member_paths(names)
            zip_file.extractall(dest_temp)
    except (RuntimeError, NotImplementedError) as exc:
        raise ArchiveError(
            f"cannot extract zip archive {archive_path.name} (password "
            f"protected or unsupported compression): {exc}"
        ) from exc
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(
            f"cannot read zip archive {archive_path.name}: {exc}"
        ) from exc


def _extract_7z(archive_path: Path, dest_temp: Path) -> None:
    try:
        with py7zr.SevenZipFile(archive_path, "r") as seven_zip:
            names: list[str] = seven_zip.getnames()
            validate_member_paths(names)
            seven_zip.extractall(path=dest_temp)
    except py7zr.exceptions.PasswordRequired as exc:
        raise ArchiveError(
            f"cannot extract 7z archive {archive_path.name}: it is password protected"
        ) from exc
    except (py7zr.exceptions.ArchiveError, OSError) as exc:
        raise ArchiveError(
            f"cannot read 7z archive {archive_path.name}: {exc}"
        ) from exc


def _extract_rar(archive_path: Path, dest_temp: Path) -> None:
    if not rar_backend_available():
        raise ArchiveError(
            f"cannot read rar archive {archive_path.name}: {RAR_INSTALL_HINT}"
        )
    try:
        with rarfile.RarFile(archive_path) as rar_file:
            names: list[str] = rar_file.namelist()
            validate_member_paths(names)
            rar_file.extractall(path=dest_temp)
    except (rarfile.Error, OSError) as exc:
        raise ArchiveError(
            f"cannot read rar archive {archive_path.name}: {exc}"
        ) from exc


def find_anchor(extracted_root: Path) -> ArchiveLayout:
    """Locate the mod trees below ``extracted_root`` and map them to the game dir.

    Searches for anchors at any depth: prefixed anchors ``SPT/user/mods`` and
    ``BepInEx/plugins`` are always recognized; bare names ``user/mods`` and
    ``plugins`` require validation (a direct subdirectory with ``package.json``
    for server mods, or a ``.dll`` file anywhere in the tree for client plugins).
    Case-insensitive; canonical targets use ``SPT/user/mods/...`` and
    ``BepInEx/plugins/...``. Descent stops inside accepted anchors. When both
    anchor kinds are present, the result type is ``hybrid``; when only one, the
    result type is ``server`` or ``client`` accordingly. Prefixed anchors take
    precedence over bare anchors of the same kind.

    Raises:
        ArchiveError: no anchor was found. The error message lists the first 30
            archive entries (or fewer if the archive is smaller); if there are
            more, it reports the count.
    """
    server_dirs, client_dirs = _find_anchor_dirs(extracted_root)
    if not server_dirs and not client_dirs:
        raise ArchiveError(_no_anchor_message(extracted_root))

    mod_type: ModType
    if server_dirs and client_dirs:
        mod_type = "hybrid"
    elif server_dirs:
        mod_type = "server"
    else:
        mod_type = "client"

    mappings: list[AnchorMapping] = []
    files: list[Path] = []
    for anchor_dir, target in (
        *((d, SERVER_MODS_TARGET) for d in server_dirs),
        *((d, CLIENT_PLUGINS_TARGET) for d in client_dirs),
    ):
        mappings.append(AnchorMapping(source=anchor_dir, target=target))
        for path in sorted(anchor_dir.rglob("*")):
            if path.is_file():
                files.append(target / path.relative_to(anchor_dir))

    return ArchiveLayout(mod_type=mod_type, mappings=mappings, files=files)


def _find_anchor_dirs(extracted_root: Path) -> tuple[list[Path], list[Path]]:
    prefixed: dict[_AnchorKind, list[Path]] = {"server": [], "client": []}
    bare: dict[_AnchorKind, list[Path]] = {"server": [], "client": []}
    pending = [extracted_root]
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            match = _anchor_match(child.relative_to(extracted_root).parts)
            if match is None:
                pending.append(child)
                continue
            kind, is_prefixed = match
            if is_prefixed:
                prefixed[kind].append(child)
            elif _looks_like_anchor_content(kind, child):
                bare[kind].append(child)
            else:
                pending.append(child)
    server_dirs = prefixed["server"] or bare["server"]
    client_dirs = prefixed["client"] or bare["client"]
    return sorted(server_dirs), sorted(client_dirs)


def _anchor_match(parts: tuple[str, ...]) -> tuple[_AnchorKind, bool] | None:
    lowered = tuple(part.lower() for part in parts)
    if lowered[-3:] == ("spt", "user", "mods"):
        return "server", True
    if lowered[-2:] == ("user", "mods"):
        return "server", False
    if lowered[-2:] == ("bepinex", "plugins"):
        return "client", True
    if lowered[-1:] == ("plugins",):
        return "client", False
    return None


def _looks_like_anchor_content(kind: _AnchorKind, anchor_dir: Path) -> bool:
    """Check whether a prefix-less candidate really carries mod content."""
    if kind == "server":
        return any(
            child.is_dir() and (child / "package.json").is_file()
            for child in anchor_dir.iterdir()
        )
    return any(path.suffix.lower() == ".dll" for path in anchor_dir.rglob("*"))


def _no_anchor_message(extracted_root: Path) -> str:
    entries = sorted(
        path.relative_to(extracted_root).as_posix()
        for path in extracted_root.rglob("*")
    )
    shown = entries[:_MAX_LISTED_ENTRIES]
    listing = ", ".join(shown) if shown else "<empty>"
    if len(entries) > len(shown):
        listing += f", ... and {len(entries) - len(shown)} more"
    return (
        "no 'SPT/user/mods' or 'BepInEx/plugins' anchor found in the archive; "
        f"content: {listing}"
    )
