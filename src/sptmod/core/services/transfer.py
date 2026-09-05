"""Exporting and importing mod lists.

An export names the installed mods with their versions and download links; an
import reads such a list back, tells the caller how it differs from what is
installed and can install the missing entries.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, Literal

from sptmod.core.errors import (
    CatalogUnavailableError,
)
from sptmod.core.forge.client import ForgeClient
from sptmod.core.models import (
    MOD_TYPES,
    Json,
    ModInfo,
    ModType,
    Progress,
)
from sptmod.core.modmeta import (
    InvalidMetadataError,
)
from sptmod.core.services.common import (
    open_catalog,
)
from sptmod.core.services.install_forge import (
    InstallModResult,
    install_mod,
)
from sptmod.core.services.state import (
    list_mods,
)

# The public surface of this module: the service functions behind the CLI and
# the RPC adapter, their result types, and the constants consumers read.


type ExportFormat = Literal["text", "json"]


EXPORT_FORMATS: Final[tuple[ExportFormat, ...]] = ("text", "json")


_CLIENT_TYPES: Final[tuple[ModType, ...]] = (ModType.CLIENT, ModType.HYBRID)


_LINK_MARKER: Final = "://"


_CONTENT_SOURCE: Final = "the given mod list"


@dataclass(frozen=True)
class ExportEntry:
    """One mod of an exported mod list.

    ``link`` is the Forge detail URL of the mod, empty when the catalog knows
    no match. ``mod_type`` keeps its default for entries read back from a text
    list, which carries no type.
    """

    name: str
    version: str
    mod_type: ModType = ModType.SERVER
    link: str = ""


@dataclass(frozen=True)
class ImportEntry:
    """One mod an imported list and the installed mods disagree about.

    ``wanted_version`` is empty for mods only the game directory has,
    ``installed_version`` for mods only the list has.
    """

    name: str
    wanted_version: str = ""
    installed_version: str = ""
    link: str = ""


@dataclass(frozen=True)
class ImportDiff:
    """The difference between an imported mod list and the installed mods.

    ``installed`` stays empty unless the import was applied; it then holds one
    entry per mod of ``missing`` that was installed from Forge.
    """

    missing: list[ImportEntry] = field(default_factory=list[ImportEntry])
    different: list[ImportEntry] = field(default_factory=list[ImportEntry])
    extra: list[ImportEntry] = field(default_factory=list[ImportEntry])
    installed: list[InstallModResult] = field(default_factory=list[InstallModResult])


def export_mods(
    path: Path,
    export_format: ExportFormat = "text",
    client_only: bool = False,
    *,
    db_path: Path | None = None,
) -> str:
    """Render the active mods of the game directory at ``path`` as a list.

    ``text`` writes one ``<name> <version> <link>`` line per mod, ordered by
    name, with the link omitted when the catalog knows none. ``json`` writes an
    object ``{"mods": [{"name", "version", "type", "link"}]}``. Both are read
    back by :func:`import_list`. ``client_only`` keeps only the mods with a
    client part, meaning the ``client`` and ``hybrid`` ones, which is the list
    a Fika client needs.

    Args:
        path: The tracked game directory.
        export_format: The output format.
        client_only: Keep only mods with a client part.
        db_path: Catalog database path; defaults to :func:`default_db_path`.
            An unusable catalog cache is not fatal here: the list is then
            rendered without Forge links.

    Returns:
        The rendered list, ending in a newline.

    Raises:
        GitError: git failed.
    """
    mods = list_mods(path)
    if client_only:
        mods = [mod for mod in mods if mod.mod_type in _CLIENT_TYPES]
    try:
        links = _catalog_links(mods, db_path)
    except CatalogUnavailableError:
        links = {}
    entries = sorted(
        (
            ExportEntry(
                name=mod.name,
                version=mod.version,
                mod_type=mod.mod_type,
                link=links.get(mod.name.casefold(), ""),
            )
            for mod in mods
        ),
        key=lambda entry: entry.name.casefold(),
    )
    if export_format == "json":
        payload = {
            "mods": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "type": entry.mod_type,
                    "link": entry.link,
                }
                for entry in entries
            ]
        }
        return json.dumps(payload, indent=2) + "\n"
    return "".join(
        " ".join(part for part in (entry.name, entry.version, entry.link) if part)
        + "\n"
        for entry in entries
    )


def import_list(
    path: Path,
    list_file: Path | None = None,
    apply: bool = False,
    progress: Progress | None = None,
    *,
    content: str | None = None,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> ImportDiff:
    """Compare an exported mod list with the game directory ``path``.

    The list comes either from ``list_file`` or, for callers that already hold
    it, as ``content``; exactly one of the two must be given. Both formats of
    :func:`export_mods` are read, recognised by content rather than by file
    name, and the entries are matched against the active mods
    case-insensitively by name. Mods only the list has are ``missing``, mods
    both know with a different version are ``different``, and mods only the
    game directory has are ``extra``, sorted by name. ``link`` is taken from
    the list where it carries one and stays empty otherwise.

    ``apply`` additionally installs every ``missing`` mod through
    :func:`install_mod`, each with its own plan and conflict handling, and
    returns the per-mod results in ``installed``; version differences and
    extras are only reported. ``progress``, ``db_path`` and ``client`` are
    passed to those installs and unused without ``apply``.

    Raises:
        InvalidMetadataError: neither or both of ``list_file`` and ``content``
            are given, or the list is missing or unreadable as a mod list.
        NoCompatibleVersionError: the catalog knows no version of a missing
            mod.
        UnresolvableDependencyError: a dependency of a missing mod cannot be
            resolved.
        ConflictError: a file or DLL conflict blocks an install.
        DependencyError: a dependency check blocks an install.
        ForgeUnavailableError: the Forge API or a download URL is unreachable.
        ForgeApiError: the Forge API answered with an error envelope.
        GitError: git failed.
    """
    wanted = _mod_list_entries(list_file, content)
    installed = {mod.name.casefold(): mod for mod in list_mods(path)}

    missing: list[ImportEntry] = []
    different: list[ImportEntry] = []
    for entry in wanted:
        mod = installed.get(entry.name.casefold())
        if mod is None:
            missing.append(
                ImportEntry(
                    name=entry.name, wanted_version=entry.version, link=entry.link
                )
            )
        elif mod.version != entry.version:
            different.append(
                ImportEntry(
                    name=mod.name,
                    wanted_version=entry.version,
                    installed_version=mod.version,
                    link=entry.link,
                )
            )

    listed = {entry.name.casefold() for entry in wanted}
    extra = [
        ImportEntry(name=mod.name, installed_version=mod.version)
        for key, mod in sorted(installed.items())
        if key not in listed
    ]

    diff = ImportDiff(missing=missing, different=different, extra=extra)
    if not apply:
        return diff
    return replace(
        diff,
        installed=_apply_import(path, diff, progress, db_path=db_path, client=client),
    )


def _apply_import(
    path: Path,
    diff: ImportDiff,
    progress: Progress | None = None,
    *,
    db_path: Path | None = None,
    client: ForgeClient | None = None,
) -> list[InstallModResult]:
    """Install the ``missing`` mods of an import diff through the Forge path.

    Installs every missing mod by name with :func:`install_mod`, in the order
    the list named them, and returns one result per mod.
    """
    return [
        install_mod(
            path,
            entry.name,
            entry.wanted_version or None,
            progress=progress,
            db_path=db_path,
            client=client,
        )
        for entry in diff.missing
    ]


def _catalog_links(mods: Sequence[ModInfo], db_path: Path | None) -> dict[str, str]:
    """Return the Forge detail URL per installed mod name, case-folded.

    Raises:
        CatalogUnavailableError: the catalog cache cannot be opened, for
            example because its directory cannot be created.
    """
    by_forge_id = {mod.forge_id: mod for mod in mods if mod.forge_id}
    links: dict[str, str] = {}
    with open_catalog(db_path) as catalog:
        for entry in catalog.classify(mods):
            if entry.installed_version is None:
                continue
            matched = by_forge_id.get(str(entry.mod.id))
            name = matched.name if matched is not None else entry.mod.name
            links[name.casefold()] = entry.mod.detail_url
    return links


def _mod_list_entries(list_file: Path | None, content: str | None) -> list[ExportEntry]:
    """Return the entries of a mod list given as a file or as its content.

    Raises:
        InvalidMetadataError: neither or both sources are given, the file is
            unreadable, or the list holds no mod entries.
    """
    if (list_file is None) == (content is None):
        raise InvalidMetadataError(
            "pass either the mod list file or its content, not both"
        )
    if list_file is None:
        return _parse_mod_list(_CONTENT_SOURCE, content or "")
    try:
        raw = list_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InvalidMetadataError(f"{list_file} cannot be read: {error}") from error
    return _parse_mod_list(str(list_file), raw)


def _parse_mod_list(source: str, raw: str) -> list[ExportEntry]:
    """Parse an exported mod list in either format.

    JSON is recognised by a leading ``{``, everything else is read as text.
    ``source`` names the list in the error messages.

    Raises:
        InvalidMetadataError: the list holds no mod entries.
    """
    if raw.lstrip().startswith("{"):
        return _parse_mod_list_json(source, raw)
    return _parse_mod_list_text(source, raw)


def _parse_mod_list_json(source: str, raw: str) -> list[ExportEntry]:
    try:
        data: Json = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvalidMetadataError(f"{source} is no valid JSON: {error}") from error
    mods = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(mods, list):
        raise InvalidMetadataError(f"{source} has no 'mods' list")
    return [_mod_list_entry(source, item) for item in mods]


def _mod_list_entry(source: str, item: Json) -> ExportEntry:
    if not isinstance(item, dict):
        raise InvalidMetadataError(f"{source}: a mod entry is no object")
    name = item.get("name")
    version = item.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise InvalidMetadataError(f"{source}: a mod entry has no name or version")
    mod_type = item.get("type")
    link = item.get("link")
    return ExportEntry(
        name=name,
        version=version,
        mod_type=ModType(mod_type) if mod_type in MOD_TYPES else ModType.SERVER,
        link=link if isinstance(link, str) else "",
    )


def _parse_mod_list_text(source: str, raw: str) -> list[ExportEntry]:
    """Parse the text format, whose last token is a link when it is a URL.

    Names may therefore contain spaces: everything before the version is the
    name, and a trailing token counts as a link only when it holds ``://``.
    """
    entries: list[ExportEntry] = []
    for line in raw.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        link = tokens[-1] if _LINK_MARKER in tokens[-1] else ""
        head = tokens[:-1] if link else tokens
        if len(head) < 2:
            raise InvalidMetadataError(
                f"{source}: cannot read {line!r}, expected '<name> <version> [link]'"
            )
        entries.append(
            ExportEntry(name=" ".join(head[:-1]), version=head[-1], link=link)
        )
    return entries
