"""Mod metadata: package.json, client-mod fallbacks, trailer mapping, semver."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from sptmod.core.errors import SptmodError
from sptmod.core.models import (
    MOD_STATES,
    MOD_TRAILER_KEYS,
    MOD_TYPES,
    TRAILER_MOD,
    TRAILER_MOD_AUTHOR,
    TRAILER_MOD_FORGE_ID,
    TRAILER_MOD_FORGE_VERSION_ID,
    TRAILER_MOD_GUID,
    TRAILER_MOD_PREVIOUS_VERSION,
    TRAILER_MOD_SOURCE,
    TRAILER_MOD_SPT,
    TRAILER_MOD_STATE,
    TRAILER_MOD_TYPE,
    TRAILER_MOD_VERSION,
    CommitInfo,
    Json,
    ModInfo,
    ModState,
    ModType,
)

PACKAGE_JSON_NAME: Final = "package.json"

PACKAGE_NAME_FIELD: Final = "name"
PACKAGE_VERSION_FIELD: Final = "version"
PACKAGE_SPT_VERSION_FIELD: Final = "sptVersion"
PACKAGE_AUTHOR_FIELD: Final = "author"
PACKAGE_DEPENDENCIES_FIELD: Final = "dependencies"
PACKAGE_GUID_FIELD: Final = "guid"

DLL_SUFFIX: Final = ".dll"

_WILDCARDS: Final[frozenset[str]] = frozenset({"*", "x", "X"})
_COMPARATORS: Final[tuple[str, ...]] = (">=", "<=", ">", "<", "=")
_UNSUPPORTED_RANGE_TOKENS: Final[tuple[str, ...]] = ("||", "-", "+")


class InvalidMetadataError(SptmodError):
    """Mod metadata is unreadable or lacks a mandatory field."""


class UnsupportedRangeError(SptmodError):
    """A version range uses syntax the matcher does not understand."""


@dataclass(frozen=True)
class ModMeta:
    """Metadata of a mod as found on disk, before it becomes a commit."""

    name: str
    version: str
    spt_range: str = ""
    author: str = ""
    guid: str = ""
    dependencies: dict[str, str] = field(default_factory=dict[str, str])


def _mandatory_string(value: Json, field_name: str, mod_dir: Path) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidMetadataError(
            f"{mod_dir / PACKAGE_JSON_NAME}: field {field_name!r} is missing or empty"
        )
    return value


def _optional_string(value: Json) -> str:
    return value if isinstance(value, str) else ""


def _dependencies(value: Json) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(name): version
        for name, version in value.items()
        if isinstance(version, str)
    }


def read_package_json(mod_dir: Path) -> ModMeta:
    """Read ``package.json`` of the server mod in ``mod_dir``.

    ``name`` and ``version`` are mandatory. ``sptVersion``, ``author``,
    ``guid`` and ``dependencies`` are optional and default to empty values;
    ``dependencies`` maps mod name to semver range.

    Mods in the wild ship messy metadata, so optional fields are read
    tolerantly: a non-string ``sptVersion``, ``author`` or ``guid`` is ignored
    and becomes an empty string, a ``dependencies`` value that is no object is
    ignored, and single dependency entries whose range is no string are
    skipped.

    Raises:
        InvalidMetadataError: the file is missing, is no valid JSON object, or
            ``name`` or ``version`` is missing, empty or not a string.
    """
    package_file = mod_dir / PACKAGE_JSON_NAME
    try:
        raw = package_file.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise InvalidMetadataError(f"{package_file} cannot be read: {error}") from error
    try:
        data: Json = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvalidMetadataError(
            f"{package_file} is no valid JSON: {error}"
        ) from error
    if not isinstance(data, dict):
        raise InvalidMetadataError(f"{package_file} is no JSON object")

    return ModMeta(
        name=_mandatory_string(
            data.get(PACKAGE_NAME_FIELD), PACKAGE_NAME_FIELD, mod_dir
        ),
        version=_mandatory_string(
            data.get(PACKAGE_VERSION_FIELD), PACKAGE_VERSION_FIELD, mod_dir
        ),
        spt_range=_optional_string(data.get(PACKAGE_SPT_VERSION_FIELD)),
        author=_optional_string(data.get(PACKAGE_AUTHOR_FIELD)),
        guid=_optional_string(data.get(PACKAGE_GUID_FIELD)),
        dependencies=_dependencies(data.get(PACKAGE_DEPENDENCIES_FIELD)),
    )


def meta_from_dll(
    dll_path: Path, name: str | None = None, version: str | None = None
) -> ModMeta:
    """Build metadata for a client-only mod from its DLL.

    Assembly attributes are not parsed: the name falls back to the DLL file
    name without its suffix, and ``version`` is the caller-supplied override
    (empty when unknown). Explicit ``name`` wins over the file name.
    """
    return ModMeta(name=name or dll_path.stem, version=version or "")


def build_trailers(
    mod_info: ModInfo, previous_version: str | None = None
) -> dict[str, str]:
    """Build the commit trailers of ``mod_info``.

    Contains every key of ``MOD_TRAILER_KEYS`` in that order; unknown values
    stay as empty trailers. ``previous_version`` adds
    ``Mod-Previous-Version`` for update commits.
    """
    values: dict[str, str] = {
        TRAILER_MOD: mod_info.name,
        TRAILER_MOD_VERSION: mod_info.version,
        TRAILER_MOD_TYPE: mod_info.mod_type,
        TRAILER_MOD_AUTHOR: mod_info.author,
        TRAILER_MOD_SPT: mod_info.spt_range,
        TRAILER_MOD_SOURCE: mod_info.source,
        TRAILER_MOD_FORGE_ID: mod_info.forge_id,
        TRAILER_MOD_FORGE_VERSION_ID: mod_info.forge_version_id,
        TRAILER_MOD_GUID: mod_info.guid,
        TRAILER_MOD_STATE: mod_info.state,
    }
    trailers = {key: values[key] for key in MOD_TRAILER_KEYS}
    if previous_version is not None:
        trailers[TRAILER_MOD_PREVIOUS_VERSION] = previous_version
    return trailers


def mod_info_from_commit(commit: CommitInfo) -> ModInfo | None:
    """Map the trailers of ``commit`` back to a :class:`ModInfo`.

    Returns ``None`` for commits without a ``Mod`` trailer, such as snapshots
    and the initial commit. ``commit`` supplies hash and date; missing
    optional trailers become empty strings.

    Raises:
        InvalidMetadataError: ``Mod-Type`` or ``Mod-State`` holds a value
            outside ``MOD_TYPES`` / ``MOD_STATES``.
    """
    trailers = commit.trailers
    name = trailers.get(TRAILER_MOD, "")
    if not name:
        return None

    mod_type = trailers.get(TRAILER_MOD_TYPE, "")
    if mod_type not in MOD_TYPES:
        raise InvalidMetadataError(
            f"commit {commit.hash}: unknown {TRAILER_MOD_TYPE} {mod_type!r}"
        )
    state = trailers.get(TRAILER_MOD_STATE, "")
    if state not in MOD_STATES:
        raise InvalidMetadataError(
            f"commit {commit.hash}: unknown {TRAILER_MOD_STATE} {state!r}"
        )

    return ModInfo(
        name=name,
        version=trailers.get(TRAILER_MOD_VERSION, ""),
        mod_type=ModType(mod_type),
        author=trailers.get(TRAILER_MOD_AUTHOR, ""),
        spt_range=trailers.get(TRAILER_MOD_SPT, ""),
        state=ModState(state),
        commit=commit.hash,
        date=commit.date,
        forge_id=trailers.get(TRAILER_MOD_FORGE_ID, ""),
        forge_version_id=trailers.get(TRAILER_MOD_FORGE_VERSION_ID, ""),
        guid=trailers.get(TRAILER_MOD_GUID, ""),
        source=trailers.get(TRAILER_MOD_SOURCE, ""),
    )


def _numeric_parts(text: str) -> list[int]:
    """Extract up to 3 semantic version numbers from text, stripping leading 'v'."""
    body = text[1:] if text[:1] in {"v", "V"} else text
    if not body:
        raise UnsupportedRangeError(f"unsupported version syntax: {text!r}")
    parts: list[int] = []
    for chunk in body.split("."):
        if chunk in _WILDCARDS:
            break
        if not chunk.isdigit():
            raise UnsupportedRangeError(f"unsupported version syntax: {text!r}")
        parts.append(int(chunk))
    return parts[:3]


def _padded(parts: list[int]) -> tuple[int, int, int]:
    """Pad version parts to a 3-tuple with zeros."""
    filled = [*parts, 0, 0, 0]
    return filled[0], filled[1], filled[2]


def _token_matches(version: tuple[int, int, int], token: str) -> bool:
    """Return whether the padded version satisfies one range token."""
    for operator in _COMPARATORS:
        if token.startswith(operator):
            bound = _padded(_numeric_parts(token[len(operator) :]))
            if operator == ">=":
                return version >= bound
            if operator == "<=":
                return version <= bound
            if operator == ">":
                return version > bound
            if operator == "<":
                return version < bound
            return version == bound

    if token.startswith("^"):
        parts = _numeric_parts(token[1:])
        if not parts:
            return True
        lower = _padded(parts)
        # Find the index of the first explicitly given non-zero part; increment
        # the upper bound at that position. Implements npm semantics where
        # ^0.0 := >=0.0.0 <0.1.0, ^0 := >=0.0.0 <1.0.0, ^0.0.3 := >=0.0.3 <0.0.4.
        bump = next(
            (index for index, part in enumerate(parts) if part > 0), len(parts) - 1
        )
        upper = _padded([*lower[:bump], lower[bump] + 1])
        return lower <= version < upper

    if token.startswith("~"):
        parts = _numeric_parts(token[1:])
        lower = _padded(parts)
        upper = (lower[0], lower[1] + 1, 0) if len(parts) >= 2 else (lower[0] + 1, 0, 0)
        return lower <= version < upper

    parts = _numeric_parts(token)
    return version[: len(parts)] == tuple(parts)


def version_matches(version: str, range_spec: str) -> bool:
    """Return whether ``version`` satisfies the npm-style range ``range_spec``.

    Supported: exact versions, ``^``, ``~``, ``>=``, ``>``, ``<=``, ``<``,
    x-ranges (``*``, ``1.x``, ``1.2.x``) and several comparators separated by
    whitespace, which are combined with AND. A leading ``v`` and omitted minor
    or patch parts are tolerated. An empty range matches everything.

    Caret operator (``^``) follows npm semantics: the upper bound increments
    at the position of the first explicitly given non-zero part. Examples:
    ``^1.2.3`` allows ``>=1.2.3 <2.0.0``, ``^0.1.2`` allows ``>=0.1.2 <0.2.0``,
    ``^0.0.3`` allows ``>=0.0.3 <0.0.4``, and ``^0`` allows ``>=0.0.0 <1.0.0``.

    Raises:
        UnsupportedRangeError: ``range_spec`` uses syntax outside that list
            (``||``, hyphen ranges, prerelease or build metadata) or
            ``version`` is no dotted numeric version. The matcher never
            guesses a result for input it does not understand.
    """
    parsed_version = _numeric_parts(version)
    if not parsed_version:
        raise UnsupportedRangeError(f"unsupported version syntax: {version!r}")
    if any(token in range_spec for token in _UNSUPPORTED_RANGE_TOKENS):
        raise UnsupportedRangeError(f"unsupported range syntax: {range_spec!r}")

    tokens = range_spec.split()
    return all(_token_matches(_padded(parsed_version), token) for token in tokens)
