"""Game directory detection, SPT version lookup and the running-game check."""

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

import psutil

from sptmod.core.errors import GameRunningError, NotAGameDirError
from sptmod.core.repo import Repo

RUNTIME_DIR_NAMES: Final[tuple[str, ...]] = ("SPT_Runtime", "SPT")
BEPINEX_DIR: Final = Path("BepInEx")
PLUGINS_DIR: Final = Path("BepInEx/plugins")
BEPINEX_CONFIG_DIR: Final = Path("BepInEx/config")
SERVER_EXE_NAME: Final = "SPT.Server.exe"

# Directories a flat SPT 3.x installation keeps in the game root; 4.x moved
# them below the runtime directory.
LEGACY_FLAT_DIR_NAMES: Final[tuple[str, ...]] = ("SPT_Data", "user")

# Client files SPT itself ships below BepInEx; they are part of the
# installation, never mods, and must survive every mod operation.
SPT_OWN_CLIENT_PATHS: Final[tuple[Path, ...]] = (
    Path("BepInEx/plugins/spt"),
    Path("BepInEx/patchers/spt-prepatch.dll"),
)

SPT_VERSION_FIELD: Final = "sptVersion"
SPT_VERSION_CONFIG_KEY: Final = "sptmod.sptversion"

GAME_PROCESS_NAMES: Final[tuple[str, ...]] = ("SPT.Server", "EscapeFromTarkov")

_DOS_HEADER_SIZE: Final = 0x40
_COFF_HEADER_SIZE: Final = 24
_SECTION_HEADER_SIZE: Final = 40
# Offset of the data directory array inside the optional header, per PE magic:
# 0x10B is PE32, 0x20B is PE32+ with its wider fields.
_DATA_DIRECTORY_OFFSETS: Final[dict[int, int]] = {0x10B: 96, 0x20B: 112}
_RESOURCE_DIRECTORY_INDEX: Final = 2
_RESOURCE_DIRECTORY_SIZE: Final = 16
_SUBDIRECTORY_FLAG: Final = 0x80000000
_RT_VERSION: Final = 16
_TEXT_VALUE: Final = 1
_FIXED_INFO_SIGNATURE: Final = b"\xbd\x04\xef\xfe"
_FIXED_INFO_SIZE: Final = 52
# Upper bound for the resource bytes a crafted header may make us read; real
# resource sections stay far below it.
_MAX_RESOURCE_BYTES: Final = 8 * 1024 * 1024


@dataclass(frozen=True)
class GameLayout:
    """The directory layout of one SPT installation.

    ``runtime_dir_name`` is the name of the runtime directory below ``root``:
    ``SPT_Runtime`` for SPT 4.1 and newer, ``SPT`` for 4.0.x. ``warning``
    carries a human-readable note about the detection, empty when there is
    nothing to report.

    Every path accessor is relative to ``root``, so it compares directly with
    the repository-relative paths of :class:`~sptmod.core.repo.Repo`.
    """

    root: Path
    runtime_dir_name: str
    warning: str = ""

    @property
    def runtime_dir(self) -> Path:
        """The SPT runtime directory."""
        return Path(self.runtime_dir_name)

    @property
    def mods_dir(self) -> Path:
        """The directory holding the server mods."""
        return self.runtime_dir / "user" / "mods"

    @property
    def profiles_dir(self) -> Path:
        """The directory holding the player profiles."""
        return self.runtime_dir / "user" / "profiles"

    @property
    def profile_backups_dir(self) -> Path:
        """The directory holding the profile backups written by the server."""
        return self.profiles_dir / "backups"

    @property
    def core_config_file(self) -> Path:
        """The core server configuration file."""
        return self.runtime_dir / "SPT_Data" / "configs" / "core.json"

    @property
    def server_exe(self) -> Path:
        """The server executable carrying the SPT version resource."""
        return self.runtime_dir / SERVER_EXE_NAME


def detect_layout(path: Path) -> GameLayout:
    """Detect the layout of the SPT installation at ``path``.

    Accepts either runtime directory name and prefers ``SPT_Runtime`` over
    ``SPT`` when both exist, reporting that ambiguity in
    :attr:`GameLayout.warning`.

    Raises:
        NotAGameDirError: ``path`` is missing, is no directory, or holds
            neither runtime directory nor ``BepInEx/``. The message names what
            is missing; a flat SPT 3.x installation is rejected as unsupported
            and names the supported versions.
    """
    if not path.exists():
        raise NotAGameDirError(f"{path} does not exist")
    if not path.is_dir():
        raise NotAGameDirError(f"{path} is not a directory")

    present = [name for name in RUNTIME_DIR_NAMES if (path / name).is_dir()]
    if not present and _is_flat_legacy_dir(path):
        raise NotAGameDirError(
            f"{path} is a flat SPT 3.x installation, which sptmod does not "
            f"support; supported are SPT 4.0.x (SPT/) and SPT 4.1 and newer "
            f"(SPT_Runtime/)"
        )

    missing: list[str] = []
    if not present:
        missing.append(" or ".join(RUNTIME_DIR_NAMES))
    if not (path / BEPINEX_DIR).is_dir():
        missing.append(BEPINEX_DIR.name)
    if missing:
        raise NotAGameDirError(
            f"{path} is not an SPT game directory, missing: {', '.join(missing)}"
        )

    warning = ""
    if len(present) > 1:
        warning = (
            f"{path} holds both {' and '.join(present)}; "
            f"using {present[0]} as the runtime directory"
        )
    return GameLayout(root=path, runtime_dir_name=present[0], warning=warning)


def _is_flat_legacy_dir(path: Path) -> bool:
    """Return whether ``path`` looks like a flat SPT 3.x installation."""
    return all((path / name).is_dir() for name in LEGACY_FLAT_DIR_NAMES)


def is_spt_own_path(entry: Path) -> bool:
    """Return whether ``entry`` is one of SPT's own client files.

    ``entry`` is relative to the game directory. Matching is case-insensitive,
    because the installer writes these names on Windows.
    """
    lowered = entry.as_posix().casefold()
    return any(
        lowered == own or lowered.startswith(f"{own}/")
        for own in (path.as_posix().casefold() for path in SPT_OWN_CLIENT_PATHS)
    )


def is_game_dir(path: Path) -> bool:
    """Return whether ``path`` looks like an SPT installation.

    True exactly when ``BepInEx/`` and one of ``RUNTIME_DIR_NAMES`` exist below
    ``path``.
    """
    return (path / BEPINEX_DIR).is_dir() and any(
        (path / name).is_dir() for name in RUNTIME_DIR_NAMES
    )


def validate_game_dir(path: Path) -> GameLayout:
    """Check that ``path`` is an SPT installation and return its layout.

    Raises:
        NotAGameDirError: see :func:`detect_layout`.
    """
    return detect_layout(path)


def read_core_json_version(layout: GameLayout) -> str | None:
    """Return the ``sptVersion`` field of the core config, or ``None``.

    SPT 4.x ships no such field, so this is the first, usually empty step of
    :func:`resolve_spt_version`. Returns ``None`` when the file is missing,
    unreadable, no valid JSON or carries no non-empty ``sptVersion`` string.
    """
    try:
        raw = (layout.root / layout.core_config_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(config, dict):
        return None
    version = config.get(SPT_VERSION_FIELD)
    if not isinstance(version, str) or not version.strip():
        return None
    return version.strip()


def read_stored_version(repo: Repo) -> str | None:
    """Return the SPT version stored in the game repository, or ``None``.

    Reads the local git config key ``sptmod.sptversion``, which holds the
    version a user answered when none could be detected.

    Raises:
        GitError: git failed.
    """
    stored = repo.config_get(SPT_VERSION_CONFIG_KEY)
    return stored.strip() if stored and stored.strip() else None


def store_spt_version(repo: Repo, version: str) -> None:
    """Store ``version`` under ``sptmod.sptversion`` in the game repository.

    Raises:
        GitError: git failed.
    """
    repo.config_set(SPT_VERSION_CONFIG_KEY, version)


def resolve_spt_version(layout: GameLayout, repo: Repo) -> str | None:
    """Return the SPT version of ``layout``, or ``None`` if it stays unknown.

    Tries, in order, :func:`read_core_json_version`,
    :func:`read_pe_file_version` on :attr:`GameLayout.server_exe` and
    :func:`read_stored_version`. ``None`` means the caller has to ask the user
    and store the answer through
    :func:`~sptmod.core.services.set_spt_version`.

    Raises:
        GitError: git failed.
    """
    return (
        read_core_json_version(layout)
        or read_pe_file_version(layout.root / layout.server_exe)
        or read_stored_version(repo)
    )


def read_pe_file_version(exe_path: Path) -> str | None:
    """Return the ``FileVersion`` of the PE executable ``exe_path``, or ``None``.

    Reads the ``VS_VERSION_INFO`` resource of the executable and falls back to
    ``ProductVersion`` when it carries no ``FileVersion``. Returns ``None``
    when the file is missing, unreadable, no PE image or has no version
    resource; version detection stays best effort.
    """
    try:
        with exe_path.open("rb") as stream:
            block = _read_version_resource(stream)
    except OSError:
        return None
    if block is None:
        return None
    strings = _version_strings(block)
    for key in ("FileVersion", "ProductVersion"):
        value = strings.get(key, "").strip()
        if value:
            return value
    return None


def _read_version_resource(stream: IO[bytes]) -> bytes | None:
    """Return the raw ``VS_VERSION_INFO`` bytes of a PE image, or ``None``."""
    dos_header = stream.read(_DOS_HEADER_SIZE)
    if len(dos_header) < _DOS_HEADER_SIZE or dos_header[:2] != b"MZ":
        return None
    stream.seek(struct.unpack_from("<I", dos_header, 0x3C)[0])

    coff = stream.read(_COFF_HEADER_SIZE)
    if len(coff) < _COFF_HEADER_SIZE or coff[:4] != b"PE\0\0":
        return None
    section_count = struct.unpack_from("<H", coff, 6)[0]
    optional_size = struct.unpack_from("<H", coff, 20)[0]

    optional = stream.read(optional_size)
    if len(optional) < optional_size:
        return None
    directories_offset = _DATA_DIRECTORY_OFFSETS.get(
        struct.unpack_from("<H", optional, 0)[0]
    )
    if directories_offset is None:
        return None
    entry_offset = directories_offset + _RESOURCE_DIRECTORY_INDEX * 8
    if entry_offset + 8 > optional_size:
        return None
    resource_rva = struct.unpack_from("<I", optional, entry_offset)[0]
    if not resource_rva:
        return None

    sections = _read_sections(stream, section_count)
    section = next(
        (
            candidate
            for candidate in sections
            if candidate.virtual_address
            <= resource_rva
            < candidate.virtual_address + max(candidate.raw_size, 1)
        ),
        None,
    )
    if section is None:
        return None
    stream.seek(section.raw_pointer)
    data = stream.read(min(section.raw_size, _MAX_RESOURCE_BYTES))
    return _version_resource_bytes(
        data, resource_rva - section.virtual_address, section
    )


@dataclass(frozen=True)
class _Section:
    """The address mapping of one PE section header."""

    virtual_address: int
    raw_size: int
    raw_pointer: int


def _read_sections(stream: IO[bytes], count: int) -> list[_Section]:
    """Read ``count`` section headers from the current stream position."""
    sections: list[_Section] = []
    for _ in range(count):
        header = stream.read(_SECTION_HEADER_SIZE)
        if len(header) < _SECTION_HEADER_SIZE:
            break
        virtual_address, raw_size, raw_pointer = struct.unpack_from("<III", header, 12)
        sections.append(
            _Section(
                virtual_address=virtual_address,
                raw_size=raw_size,
                raw_pointer=raw_pointer,
            )
        )
    return sections


def _version_resource_bytes(data: bytes, base: int, section: _Section) -> bytes | None:
    """Walk the resource tree in ``data`` down to the version resource bytes."""
    node = base
    leaf: int | None = None
    for wanted in (_RT_VERSION, None, None):
        target = _resource_entry(data, node, wanted)
        if target is None:
            return None
        if wanted is None and not target & _SUBDIRECTORY_FLAG:
            leaf = base + target
            break
        if not target & _SUBDIRECTORY_FLAG:
            return None
        node = base + (target & ~_SUBDIRECTORY_FLAG)
    if leaf is None or leaf + 8 > len(data):
        return None
    data_rva, size = struct.unpack_from("<II", data, leaf)
    start = data_rva - section.virtual_address
    if start < 0 or size > _MAX_RESOURCE_BYTES or start + size > len(data):
        return None
    return data[start : start + size]


def _resource_entry(data: bytes, directory: int, wanted_id: int | None) -> int | None:
    """Return the target offset of one entry of a resource directory.

    ``wanted_id`` selects the entry with that resource ID; ``None`` takes the
    first entry, which is how the name and language levels are walked.
    """
    if directory + _RESOURCE_DIRECTORY_SIZE > len(data):
        return None
    named_count, id_count = struct.unpack_from("<HH", data, directory + 12)
    for index in range(named_count + id_count):
        entry = directory + _RESOURCE_DIRECTORY_SIZE + index * 8
        if entry + 8 > len(data):
            return None
        name, target = struct.unpack_from("<II", data, entry)
        if wanted_id is None or (not name & _SUBDIRECTORY_FLAG and name == wanted_id):
            return int(target)
    return None


def _version_strings(block: bytes) -> dict[str, str]:
    """Return the ``StringFileInfo`` entries of a ``VS_VERSION_INFO`` block."""
    root = _version_node(block, 0)
    if root is None:
        return {}
    strings: dict[str, str] = {}
    for info in _version_children(block, root):
        if info.key != "StringFileInfo":
            continue
        for table in _version_children(block, info):
            for entry in _version_children(block, table):
                strings.setdefault(entry.key, entry.text)
    return strings


@dataclass(frozen=True)
class _VersionNode:
    """One node of the ``VS_VERSION_INFO`` tree."""

    key: str
    text: str
    children_offset: int
    end: int


def _version_node(data: bytes, offset: int) -> _VersionNode | None:
    """Parse the node starting at ``offset``, or return ``None`` if malformed."""
    if offset + 6 > len(data):
        return None
    length, value_length, value_type = struct.unpack_from("<HHH", data, offset)
    if length < 6 or offset + length > len(data):
        return None
    key, after_key = _version_key(data, offset + 6)
    value_offset = _align4(after_key)
    value_bytes = value_length * 2 if value_type == _TEXT_VALUE else value_length
    if data[value_offset : value_offset + 4] == _FIXED_INFO_SIGNATURE:
        # Some writers declare the fixed info in words; its size is constant,
        # so the signature is the more reliable source than the field.
        value_bytes = _FIXED_INFO_SIZE
    text = ""
    if value_type == _TEXT_VALUE and value_offset + value_bytes <= len(data):
        text = (
            data[value_offset : value_offset + value_bytes]
            .decode("utf-16-le", errors="replace")
            .rstrip("\x00")
        )
    return _VersionNode(
        key=key,
        text=text,
        children_offset=_align4(value_offset + value_bytes),
        end=offset + length,
    )


def _version_children(data: bytes, node: _VersionNode) -> list[_VersionNode]:
    """Return the child nodes of ``node``."""
    children: list[_VersionNode] = []
    offset = node.children_offset
    while offset < node.end:
        child = _version_node(data, offset)
        if child is None or child.end <= offset:
            break
        children.append(child)
        offset = _align4(child.end)
    return children


def _version_key(data: bytes, offset: int) -> tuple[str, int]:
    """Return the UTF-16 key at ``offset`` and the offset behind its terminator."""
    end = offset
    while end + 2 <= len(data) and data[end : end + 2] != b"\x00\x00":
        end += 2
    return data[offset:end].decode("utf-16-le", errors="replace"), end + 2


def _align4(offset: int) -> int:
    """Round ``offset`` up to the next 32-bit boundary."""
    return (offset + 3) & ~3


def is_game_process_name(name: str) -> bool:
    """Return whether a process name belongs to the SPT server or client.

    Matches case-insensitively on the substrings in ``GAME_PROCESS_NAMES``, so
    Wine processes whose name ends in ``.exe`` match as well.
    """
    lowered = name.lower()
    return any(candidate.lower() in lowered for candidate in GAME_PROCESS_NAMES)


def running_game_processes() -> list[str]:
    """Return the names of the running SPT server and client processes.

    The only place that scans the process table (via ``psutil``); tests replace
    this function instead of touching real processes.
    """
    names: list[str] = []
    for process in psutil.process_iter():
        try:
            name = process.name()
        except psutil.Error:
            continue
        if is_game_process_name(name):
            names.append(name)
    return names


def ensure_game_not_running() -> None:
    """Check that neither the SPT server nor the client is running.

    Raises:
        GameRunningError: at least one process reported by
            :func:`running_game_processes` is running. The message names them.
    """
    names = running_game_processes()
    if names:
        raise GameRunningError(
            f"the game is running, close it first: {', '.join(names)}"
        )
