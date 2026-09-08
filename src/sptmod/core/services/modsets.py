"""Mod sets: named mod line-ups a player switches between and plays with.

A set names mods and the state each of them should have. Applying one is
authoritative: mods the set turns off and active mods the set does not name at
all are turned off, so what runs afterwards is exactly the line-up the set
describes. Sets are stored per game directory outside of it, can be bound to a
player profile and travel between installations as a file or a share code.
"""

import base64
import binascii
import json
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from sptmod.core.errors import (
    ModSetExistsError,
    ModSetIncompleteError,
    ModSetNotFoundError,
)
from sptmod.core.models import (
    ACTIVE_STATES,
    Json,
    ModInfo,
    ModState,
    Progress,
)
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.modsets import (
    GameSets,
    ModSet,
    ModSetEntry,
    ProfileBinding,
    default_modsets_path,
    load_sets,
    save_sets,
    store_key,
)
from sptmod.core.services.lifecycle import (
    disable_mod,
    enable_mod,
)
from sptmod.core.services.state import (
    list_mods,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


type ModSetMode = Literal["apply", "ignore_missing", "skip"]


MOD_SET_MODES: Final[tuple[ModSetMode, ...]] = ("apply", "ignore_missing", "skip")


_CREATED_AT_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"

_EXPORT_KEY: Final = "mod_set"

_SHARE_PREFIX: Final = "SPTMODSET1-"

_SHARE_COMPRESSION_LEVEL: Final = 9

_SHARE_MAX_BYTES: Final = 4 * 1024 * 1024

_CONTENT_SOURCE: Final = "the given mod set"

_TURNED_OFF_REASON: Final = "the set turns this mod off"

_NOT_INCLUDED_REASON: Final = "the set does not include this mod"

_VERSION_REASON: Final = "the set names another version"


class ModSetStep(StrEnum):
    """The phases applying a mod set reports progress for."""

    PLAN = "plan"
    DISABLE = "disable"
    ENABLE = "enable"
    DONE = "done"


@dataclass(frozen=True)
class ModSetOverview:
    """The mod sets of one game directory and the profiles bound to them."""

    sets: list[ModSet] = field(default_factory=list[ModSet])
    bindings: list[ProfileBinding] = field(default_factory=list[ProfileBinding])


@dataclass(frozen=True)
class ModSetChange:
    """One mod a set would turn on or off, or names in another version.

    ``version`` is the version the set names, ``installed_version`` the one
    the installation has; ``reason`` explains a change in plain language.
    """

    name: str
    version: str
    installed_version: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ModSetPlan:
    """What applying a mod set would change.

    ``different`` is reported only: a set never installs, updates or
    downgrades a mod. ``missing`` holds the mods the set needs and the
    installation does not have.
    """

    name: str
    enable: list[ModSetChange] = field(default_factory=list[ModSetChange])
    disable: list[ModSetChange] = field(default_factory=list[ModSetChange])
    missing: list[ModSetEntry] = field(default_factory=list[ModSetEntry])
    different: list[ModSetChange] = field(default_factory=list[ModSetChange])
    unchanged: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class ModSetApplyResult:
    """Outcome of :func:`apply_mod_set`.

    ``missing`` names the mods that were skipped because the installation
    does not have them; ``notices`` holds what a mod reported instead of
    changing.
    """

    name: str
    enabled: list[str] = field(default_factory=list[str])
    disabled: list[str] = field(default_factory=list[str])
    missing: list[str] = field(default_factory=list[str])
    notices: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class ModSetExport:
    """A mod set rendered for sharing.

    ``content`` is the file a player saves and imports elsewhere, ``code`` the
    same set as one line of text to paste.
    """

    name: str
    content: str
    code: str


def list_mod_sets(path: Path, *, store_path: Path | None = None) -> ModSetOverview:
    """Return the mod sets stored for the game directory ``path``.

    Args:
        path: The game directory the sets belong to.
        store_path: Mod set file; defaults to :func:`default_modsets_path`.

    Returns:
        Every stored set and every profile bound to one of them.

    Raises:
        InvalidMetadataError: the stored mod sets cannot be read.
    """
    stored = _load(path, store_path)
    return ModSetOverview(sets=stored.sets, bindings=stored.bindings)


def save_mod_set(
    path: Path,
    name: str,
    entries: list[ModSetEntry] | None = None,
    *,
    store_path: Path | None = None,
) -> ModSet:
    """Store the mod set ``name`` for the game directory ``path``.

    Without ``entries`` the set is taken from the installation as it is right
    now: every mod that is installed or disabled, with the state it currently
    has. With ``entries`` the set holds exactly those mods, which is how an
    edited set is saved. An existing set of the same name, compared without
    regard to case, is replaced.

    Args:
        path: The game directory the set belongs to.
        name: The name of the set.
        entries: The mods of the set, or ``None`` to take the installed ones.
        store_path: Mod set file; defaults to :func:`default_modsets_path`.

    Returns:
        The stored set.

    Raises:
        InvalidMetadataError: ``name`` is empty or the stored mod sets cannot
            be read.
        GitError: git failed.
        OSError: the mod set file cannot be written.
    """
    cleaned = _clean_name(name)
    store, key, stored = _open(path, store_path)
    members = list(entries) if entries is not None else _installed_entries(path)
    existing = _find(stored.sets, cleaned)
    created_at = existing.created_at if existing is not None else _now()
    mod_set = ModSet(name=cleaned, created_at=created_at, entries=members)
    if existing is None:
        sets = [*stored.sets, mod_set]
    else:
        sets = [mod_set if item is existing else item for item in stored.sets]
    save_sets(store, key, replace(stored, sets=sets))
    return mod_set


def delete_mod_set(
    path: Path, name: str, *, store_path: Path | None = None
) -> ModSetOverview:
    """Delete the mod set ``name`` of the game directory ``path``.

    Profiles bound to the set are unbound with it, so no profile is left
    pointing at a set that is gone.

    Raises:
        ModSetNotFoundError: no set of that name is stored.
        InvalidMetadataError: the stored mod sets cannot be read.
        OSError: the mod set file cannot be written.
    """
    store, key, stored = _open(path, store_path)
    mod_set = _require(stored.sets, name)
    remaining = GameSets(
        sets=[item for item in stored.sets if item is not mod_set],
        bindings=[
            binding
            for binding in stored.bindings
            if binding.set_name.casefold() != mod_set.name.casefold()
        ],
    )
    save_sets(store, key, remaining)
    return ModSetOverview(sets=remaining.sets, bindings=remaining.bindings)


def rename_mod_set(
    path: Path, name: str, new_name: str, *, store_path: Path | None = None
) -> ModSet:
    """Rename the mod set ``name`` of the game directory ``path`` to ``new_name``.

    Profiles bound to the set keep it. Renaming a set to its own name in
    another spelling is allowed and stores the new spelling.

    Raises:
        ModSetNotFoundError: no set of that name is stored.
        ModSetExistsError: another set is already named ``new_name``.
        InvalidMetadataError: ``new_name`` is empty or the stored mod sets
            cannot be read.
        OSError: the mod set file cannot be written.
    """
    cleaned = _clean_name(new_name)
    store, key, stored = _open(path, store_path)
    mod_set = _require(stored.sets, name)
    taken = _find(stored.sets, cleaned)
    if taken is not None and taken is not mod_set:
        raise ModSetExistsError(f"a mod set named {cleaned!r} already exists")
    renamed = replace(mod_set, name=cleaned)
    save_sets(
        store,
        key,
        GameSets(
            sets=[renamed if item is mod_set else item for item in stored.sets],
            bindings=[
                replace(binding, set_name=cleaned)
                if binding.set_name.casefold() == mod_set.name.casefold()
                else binding
                for binding in stored.bindings
            ],
        ),
    )
    return renamed


def export_mod_set(
    path: Path, name: str, *, store_path: Path | None = None
) -> ModSetExport:
    """Render the mod set ``name`` of the game directory ``path`` for sharing.

    Returns the set both as the content of a file another installation can
    import and as a share code, a single line of text carrying the same set.

    Raises:
        ModSetNotFoundError: no set of that name is stored.
        InvalidMetadataError: the stored mod sets cannot be read.
    """
    mod_set = _require(_load(path, store_path).sets, name)
    content = _render(mod_set)
    return ModSetExport(name=mod_set.name, content=content, code=_share_code(content))


def import_mod_set(
    path: Path,
    set_file: Path | None = None,
    content: str | None = None,
    name: str | None = None,
    *,
    store_path: Path | None = None,
) -> ModSet:
    """Store a mod set exported elsewhere for the game directory ``path``.

    The set comes either from ``set_file`` or, for callers that already hold
    it, as ``content``; exactly one of the two must be given. Both an exported
    file and a share code are read, recognised by their content rather than by
    a file name. ``name`` stores the set under another name, which is how a
    caller answers a name that is already taken.

    Raises:
        InvalidMetadataError: neither or both of ``set_file`` and ``content``
            are given, the set is missing or unreadable, or its name is empty.
        ModSetExistsError: a set of that name is already stored.
        OSError: the mod set file cannot be written.
    """
    source, raw = _import_source(set_file, content)
    imported = _parse_set(source, raw)
    cleaned = _clean_name(name if name is not None else imported.name)
    store, key, stored = _open(path, store_path)
    if _find(stored.sets, cleaned) is not None:
        raise ModSetExistsError(f"a mod set named {cleaned!r} already exists")
    mod_set = ModSet(name=cleaned, created_at=_now(), entries=imported.entries)
    save_sets(store, key, replace(stored, sets=[*stored.sets, mod_set]))
    return mod_set


def bind_profile_set(
    path: Path,
    profile_id: str,
    set_name: str | None = None,
    *,
    store_path: Path | None = None,
) -> ModSetOverview:
    """Play the profile ``profile_id`` of ``path`` with the mod set ``set_name``.

    The bound set is applied whenever that profile is launched. Without
    ``set_name`` the profile is unbound and launches change no mods.

    Raises:
        ModSetNotFoundError: no set named ``set_name`` is stored.
        InvalidMetadataError: the stored mod sets cannot be read.
        OSError: the mod set file cannot be written.
    """
    store, key, stored = _open(path, store_path)
    bindings = [
        binding for binding in stored.bindings if binding.profile_id != profile_id
    ]
    if set_name is not None:
        mod_set = _require(stored.sets, set_name)
        bindings.append(ProfileBinding(profile_id=profile_id, set_name=mod_set.name))
    updated = replace(stored, bindings=bindings)
    save_sets(store, key, updated)
    return ModSetOverview(sets=updated.sets, bindings=updated.bindings)


def plan_mod_set(
    path: Path, name: str, *, store_path: Path | None = None
) -> ModSetPlan:
    """Report what applying the mod set ``name`` to ``path`` would change.

    Reads only. Mods are linked to the set by their Forge id where both sides
    carry one and by their name otherwise. A set is authoritative: active mods
    it does not name are turned off as well.

    Raises:
        ModSetNotFoundError: no set of that name is stored.
        InvalidMetadataError: the stored mod sets cannot be read.
        GitError: git failed.
    """
    mod_set = _require(_load(path, store_path).sets, name)
    mods = list_mods(path, include_all=True)
    plan = _match_entries(mod_set, mods)
    return replace(plan, disable=[*plan.disable, *_unnamed_mods(mod_set, mods)])


def apply_mod_set(
    path: Path,
    name: str,
    ignore_missing: bool = False,
    progress: Progress | None = None,
    *,
    store_path: Path | None = None,
) -> ModSetApplyResult:
    """Turn the mods of the game directory ``path`` into the mod set ``name``.

    Turns off every mod the set turns off and every active mod it does not
    name, then turns on the mods it needs; the mods are changed one by one,
    each with the same checks a single mod change makes. A set never installs
    or updates a mod: a set naming a mod the installation does not have is
    refused unless ``ignore_missing`` allows applying the rest of it.

    Args:
        path: The tracked game directory.
        name: The name of the mod set.
        ignore_missing: Apply the set even though mods of it are missing.
        progress: Optional callback for the phases of the change.
        store_path: Mod set file; defaults to :func:`default_modsets_path`.

    Returns:
        What was turned on and off, what was missing, and what a mod reported
        instead of changing.

    Raises:
        ModSetNotFoundError: no set of that name is stored.
        ModSetIncompleteError: the set names mods the installation does not
            have and ``ignore_missing`` is not set; nothing was changed.
        InvalidMetadataError: the stored mod sets cannot be read.
        DirtyTreeError: the game folder holds files sptmod does not manage.
        GameRunningError: the SPT server or client is running.
        OperationInProgressError: another operation holds the game directory.
        ConflictError: a change conflicted with a later mod.
        GitError: git failed.
    """
    plan = plan_mod_set(path, name, store_path=store_path)
    missing = [entry.name for entry in plan.missing]
    if missing and not ignore_missing:
        raise ModSetIncompleteError(plan.name, missing)
    _report(progress, ModSetStep.PLAN, 0, "Working out what to change")

    disabled: list[str] = []
    enabled: list[str] = []
    notices: list[str] = []
    total = len(plan.disable) + len(plan.enable)
    done = 0
    for change in plan.disable:
        _report(
            progress,
            ModSetStep.DISABLE,
            _percent(done, total),
            f"Turning off {change.name}",
        )
        result = disable_mod(path, change.name)
        _record(result.notice, change.name, disabled, notices)
        done += 1
    for change in plan.enable:
        _report(
            progress,
            ModSetStep.ENABLE,
            _percent(done, total),
            f"Turning on {change.name}",
        )
        result = enable_mod(path, change.name)
        _record(result.notice, change.name, enabled, notices)
        done += 1
    _report(progress, ModSetStep.DONE, 100, "The mod set is applied")
    return ModSetApplyResult(
        name=plan.name,
        enabled=enabled,
        disabled=disabled,
        missing=missing,
        notices=notices,
    )


def _open(path: Path, store_path: Path | None) -> tuple[Path, str, GameSets]:
    """Return the store file, the key of ``path`` in it and what it holds."""
    store = store_path or default_modsets_path()
    key = store_key(path)
    return store, key, load_sets(store, key)


def _load(path: Path, store_path: Path | None) -> GameSets:
    """Return the stored mod sets of the game directory ``path``."""
    return _open(path, store_path)[2]


def _now() -> str:
    return datetime.now(UTC).strftime(_CREATED_AT_FORMAT)


def _clean_name(name: str) -> str:
    """Return ``name`` without surrounding whitespace.

    Raises:
        InvalidMetadataError: nothing is left of the name.
    """
    cleaned = name.strip()
    if not cleaned:
        raise InvalidMetadataError("a mod set needs a name")
    return cleaned


def _find(sets: Sequence[ModSet], name: str) -> ModSet | None:
    """Return the set named ``name``, compared without regard to case."""
    wanted = name.strip().casefold()
    for mod_set in sets:
        if mod_set.name.casefold() == wanted:
            return mod_set
    return None


def _require(sets: Sequence[ModSet], name: str) -> ModSet:
    """Return the set named ``name``.

    Raises:
        ModSetNotFoundError: no set of that name is stored.
    """
    mod_set = _find(sets, name)
    if mod_set is None:
        raise ModSetNotFoundError(f"there is no mod set named {name!r}")
    return mod_set


def _installed_entries(path: Path) -> list[ModSetEntry]:
    """Return the installed mods of ``path`` with the state they have now."""
    return sorted(
        (
            ModSetEntry(
                forge_id=mod.forge_id,
                name=mod.name,
                version=mod.version,
                enabled=mod.state in ACTIVE_STATES,
            )
            for mod in list_mods(path, include_all=True)
            if mod.state != ModState.REMOVED
        ),
        key=lambda entry: entry.name.casefold(),
    )


def _match_entries(mod_set: ModSet, mods: Sequence[ModInfo]) -> ModSetPlan:
    """Sort the mods a set names into what applying it would do with them."""
    enable: list[ModSetChange] = []
    disable: list[ModSetChange] = []
    missing: list[ModSetEntry] = []
    different: list[ModSetChange] = []
    unchanged: list[str] = []
    for entry in mod_set.entries:
        mod = _installed_match(entry, mods)
        if mod is None or mod.state == ModState.REMOVED:
            if entry.enabled:
                missing.append(entry)
            else:
                unchanged.append(entry.name)
            continue
        active = mod.state in ACTIVE_STATES
        change = ModSetChange(
            name=mod.name, version=entry.version, installed_version=mod.version
        )
        if entry.enabled and active:
            unchanged.append(mod.name)
            if entry.version and entry.version != mod.version:
                different.append(replace(change, reason=_VERSION_REASON))
        elif entry.enabled:
            enable.append(change)
        elif active:
            disable.append(replace(change, reason=_TURNED_OFF_REASON))
        else:
            unchanged.append(mod.name)
    return ModSetPlan(
        name=mod_set.name,
        enable=enable,
        disable=disable,
        missing=missing,
        different=different,
        unchanged=unchanged,
    )


def _unnamed_mods(mod_set: ModSet, mods: Sequence[ModInfo]) -> list[ModSetChange]:
    """Return the active mods the set does not name, which it turns off."""
    named = {
        matched.name
        for entry in mod_set.entries
        if (matched := _installed_match(entry, mods)) is not None
    }
    return sorted(
        (
            ModSetChange(
                name=mod.name,
                version="",
                installed_version=mod.version,
                reason=_NOT_INCLUDED_REASON,
            )
            for mod in mods
            if mod.state in ACTIVE_STATES and mod.name not in named
        ),
        key=lambda change: change.name.casefold(),
    )


def _installed_match(entry: ModSetEntry, mods: Sequence[ModInfo]) -> ModInfo | None:
    """Return the installed mod an entry names, by Forge id before name."""
    if entry.forge_id:
        for mod in mods:
            if mod.forge_id == entry.forge_id:
                return mod
    wanted = entry.name.casefold()
    for mod in mods:
        if mod.name.casefold() == wanted:
            return mod
    return None


def _percent(done: int, total: int) -> int:
    return int(100 * done / total) if total else 100


def _report(
    progress: Progress | None, step: ModSetStep, percent: int, message: str
) -> None:
    if progress is not None:
        progress(step=step, percent=percent, message=message)


def _record(notice: str, name: str, changed: list[str], notices: list[str]) -> None:
    """Note a mod as changed, or keep what it reported instead."""
    if notice:
        notices.append(notice)
    else:
        changed.append(name)


def _render(mod_set: ModSet) -> str:
    """Render ``mod_set`` as the content of an exported mod set file."""
    payload = {
        _EXPORT_KEY: {
            "name": mod_set.name,
            "entries": [
                {
                    "forge_id": entry.forge_id,
                    "name": entry.name,
                    "version": entry.version,
                    "enabled": entry.enabled,
                }
                for entry in mod_set.entries
            ],
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def _share_code(content: str) -> str:
    """Render an exported mod set as one line of text."""
    packed = zlib.compress(content.encode("utf-8"), _SHARE_COMPRESSION_LEVEL)
    return _SHARE_PREFIX + base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def _import_source(set_file: Path | None, content: str | None) -> tuple[str, str]:
    """Return the name and the text of the mod set to import.

    Raises:
        InvalidMetadataError: neither or both sources are given, or the file
            cannot be read.
    """
    if (set_file is None) == (content is None):
        raise InvalidMetadataError(
            "pass either the mod set file or its content, not both"
        )
    if set_file is None:
        return _CONTENT_SOURCE, content or ""
    try:
        return str(set_file), set_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InvalidMetadataError(f"{set_file} cannot be read: {error}") from error
    except UnicodeDecodeError as error:
        raise InvalidMetadataError(
            f"{set_file} cannot be read: it holds no text"
        ) from error


def _parse_set(source: str, raw: str) -> ModSet:
    """Read an exported mod set, given as file content or as a share code.

    Raises:
        InvalidMetadataError: the text is neither, or holds no mod set.
    """
    text = raw.strip()
    if text.startswith(_SHARE_PREFIX):
        text = _decode_share_code(source, text)
    elif not text.startswith("{"):
        raise InvalidMetadataError(f"{source} is no mod set")
    return _parse_set_json(source, text)


def _decode_share_code(source: str, text: str) -> str:
    """Return the mod set content a share code carries.

    Only the first :data:`_SHARE_MAX_BYTES` of content are unpacked, so a
    short code cannot claim a large amount of memory.

    Raises:
        InvalidMetadataError: the code is damaged or carries more content than
            a mod set ever needs.
    """
    body = text[len(_SHARE_PREFIX) :]
    try:
        packed = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        unpacker = zlib.decompressobj()
        content = unpacker.decompress(packed, _SHARE_MAX_BYTES)
        oversized = bool(unpacker.unconsumed_tail)
        decoded = content.decode("utf-8")
    except (binascii.Error, ValueError, zlib.error) as error:
        raise InvalidMetadataError(
            f"{source} is no usable share code: {error}"
        ) from error
    if oversized:
        raise InvalidMetadataError(f"{source} is a damaged or too large share code")
    return decoded


def _parse_set_json(source: str, text: str) -> ModSet:
    """Read the JSON of an exported mod set.

    Raises:
        InvalidMetadataError: the text is no valid JSON or holds no mod set.
    """
    try:
        document: Json = json.loads(text)
    except ValueError as error:
        raise InvalidMetadataError(f"{source} is no valid JSON: {error}") from error
    payload = document.get(_EXPORT_KEY) if isinstance(document, dict) else None
    if not isinstance(payload, dict):
        raise InvalidMetadataError(f"{source} has no '{_EXPORT_KEY}' object")
    name = payload.get("name")
    entries = payload.get("entries", [])
    if not isinstance(name, str) or not isinstance(entries, list):
        raise InvalidMetadataError(f"{source} has no mod set name or mods")
    return ModSet(
        name=name,
        created_at="",
        entries=[_parse_entry(source, item) for item in entries],
    )


def _parse_entry(source: str, item: Json) -> ModSetEntry:
    """Read one mod of an exported mod set.

    Raises:
        InvalidMetadataError: the mod has no name or unusable values.
    """
    if not isinstance(item, dict):
        raise InvalidMetadataError(f"{source}: a mod of the set is no object")
    forge_id = item.get("forge_id", "")
    name = item.get("name")
    version = item.get("version", "")
    enabled = item.get("enabled", True)
    if (
        not isinstance(forge_id, str)
        or not isinstance(name, str)
        or not isinstance(version, str)
        or not isinstance(enabled, bool)
    ):
        raise InvalidMetadataError(f"{source}: a mod of the set is unreadable")
    return ModSetEntry(forge_id=forge_id, name=name, version=version, enabled=enabled)
