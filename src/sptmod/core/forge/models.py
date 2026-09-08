"""Dataclasses mirroring the read-only Forge API v0 payloads.

Pure data: this module must stay free of httpx so the shapes can be reused
without pulling the HTTP stack in.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

type FikaCompatibility = Literal["compatible", "incompatible", "unknown"]


@dataclass(frozen=True)
class ForgeUser:
    """A mod owner or additional author."""

    id: int
    name: str
    profile_photo_url: str | None
    cover_photo_url: str | None


@dataclass(frozen=True)
class SourceCodeLink:
    """A source code link attached to a mod."""

    url: str
    label: str


@dataclass(frozen=True)
class ForgeVersionSummary:
    """The reduced version shape delivered by ``include=versions`` on /mods."""

    id: int
    version: str
    spt_version_constraint: str
    downloads: int
    published_at: str | None


@dataclass(frozen=True)
class ForgeLicense:
    """The license shape delivered by ``include=license``."""

    id: int
    name: str
    link: str


@dataclass(frozen=True)
class ForgeCategory:
    """The category shape delivered by ``include=category``."""

    id: int
    title: str
    slug: str


@dataclass(frozen=True)
class ForgeMod:
    """A mod as returned by /mods and /mod/{modId}.

    The detail endpoint adds description and custom_ai_disclosure fields,
    which are None when fetched from the list endpoint.
    """

    id: int
    hub_id: int | None
    guid: str | None
    name: str
    slug: str
    teaser: str | None
    thumbnail: str | None
    downloads: int
    favourites_count: int
    owner: ForgeUser | None
    additional_authors: list[ForgeUser]
    source_code_links: list[SourceCodeLink]
    detail_url: str
    fika_compatibility: bool | None
    featured: bool
    contains_ads: bool
    contains_ai_content: bool
    shows_profile_binding_notice: bool
    published_at: str | None
    created_at: str | None
    updated_at: str | None
    description: str | None = None
    custom_ai_disclosure: str | None = None
    endorsements_count: int = 0
    license: ForgeLicense | None = None
    category: ForgeCategory | None = None
    versions: list[ForgeVersionSummary] = field(default_factory=list[ForgeVersionSummary])


@dataclass(frozen=True)
class ForgeVersionDependency:
    """An immediate dependency embedded in a version via ``include=dependencies``."""

    id: int
    mod_id: int
    mod_guid: str | None
    mod_name: str
    version_constraint: str
    is_optional: bool


@dataclass(frozen=True)
class ForgeVersion:
    """A mod version from /mod/{modId}/versions.

    Note: content_length may be None for historical versions. link is the
    download URL.
    """

    id: int
    hub_id: int | None
    version: str
    description: str | None
    link: str | None
    content_length: int | None
    spt_version_constraint: str
    downloads: int
    fika_compatibility: FikaCompatibility
    published_at: str | None
    created_at: str | None
    updated_at: str | None
    dependencies: list[ForgeVersionDependency] = field(default_factory=list[ForgeVersionDependency])
    virus_total_links: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class PageMeta:
    """The ``meta`` block of a paginated response."""

    current_page: int
    last_page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class Page[T]:
    """One page of a paginated list response.

    Attributes:
        items: The items on this page.
        meta: Pagination metadata (current_page, last_page, per_page, total).
        next_url: The URL for the next page, or None if this is the last page.
    """

    items: list[T]
    meta: PageMeta
    next_url: str | None


@dataclass(frozen=True)
class CurrentVersion:
    """The installed version an update entry refers to.

    ``slug`` is only present in ``updates`` entries, not in ``blocked_updates``.
    """

    id: int
    mod_id: int
    guid: str | None
    name: str
    version: str
    slug: str | None = None


@dataclass(frozen=True)
class RecommendedVersion:
    """The version /mods/updates recommends installing."""

    id: int
    version: str
    link: str | None
    content_length: int | None
    fika_compatibility: FikaCompatibility
    spt_versions: list[str]


@dataclass(frozen=True)
class ModUpdate:
    """An available update for an installed mod version."""

    current_version: CurrentVersion
    recommended_version: RecommendedVersion
    update_reason: str


@dataclass(frozen=True)
class LatestVersion:
    """The newest version of a mod whose update is blocked."""

    id: int
    version: str
    spt_versions: list[str]


@dataclass(frozen=True)
class BlockingMod:
    """An installed mod whose dependency constraint blocks an update."""

    mod_id: int
    mod_guid: str | None
    mod_name: str
    current_version: str
    constraint: str
    incompatible_with: str


@dataclass(frozen=True)
class BlockedUpdate:
    """An update that exists but cannot be applied."""

    current_version: CurrentVersion
    latest_version: LatestVersion
    block_reason: str
    blocking_mods: list[BlockingMod] = field(default_factory=list[BlockingMod])


@dataclass(frozen=True)
class UpToDateMod:
    """An installed mod version that is already current."""

    id: int
    mod_id: int
    guid: str | None
    name: str
    version: str
    spt_versions: list[str]


@dataclass(frozen=True)
class IncompatibleMod:
    """An installed mod version without a version for the queried SPT release."""

    id: int
    mod_id: int
    guid: str | None
    name: str
    version: str
    reason: str
    latest_compatible_version: str | None


@dataclass(frozen=True)
class UpdatesResult:
    """The parsed response from /mods/updates for installed mod versions.

    Attributes:
        spt_version: The SPT version that was queried.
        updates: Mods with available updates for the queried SPT version.
        blocked_updates: Mods with newer versions available but blocked by
            dependency constraints.
        up_to_date: Mods already at the recommended version.
        incompatible_with_spt: Mods without any version for the queried SPT release.
    """

    spt_version: str
    updates: list[ModUpdate]
    blocked_updates: list[BlockedUpdate]
    up_to_date: list[UpToDateMod]
    incompatible_with_spt: list[IncompatibleMod]


@dataclass(frozen=True)
class CompatibleVersion:
    """The version chosen for a dependency node."""

    id: int
    version: str
    link: str | None
    content_length: int | None
    fika_compatibility: FikaCompatibility


@dataclass(frozen=True)
class DependencyNode:
    """A node in the recursive dependency tree from /mods/dependencies.

    Attributes:
        id: The mod ID.
        guid: The mod GUID, or None if unknown.
        name: The mod name.
        slug: The mod slug.
        latest_compatible_version: The version chosen for this node (the
            latest one satisfying all constraints), or None if unavailable.
        conflict: True if no single version of this mod satisfies all constraints
            across the queried mods (version selection is global). Defaults to True
            if missing (fail-safe).
        dependencies: Direct dependencies of this node (recursive).
    """

    id: int
    guid: str | None
    name: str
    slug: str
    latest_compatible_version: CompatibleVersion | None
    conflict: bool
    dependencies: list["DependencyNode"]


@dataclass(frozen=True)
class DownloadStream:
    """An open streaming response for an arbitrary download URL."""

    status_code: int
    headers: Mapping[str, str]
    chunks: Iterator[bytes]
