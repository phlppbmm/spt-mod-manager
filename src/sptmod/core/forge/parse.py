"""Tolerant JSON-to-dataclass coercion for the Forge API v0 responses.

Every helper degrades to a default instead of raising, so a field the API
renames or omits never breaks a whole page of results.
"""

from collections.abc import Callable, Mapping

from sptmod.core.forge.models import (
    BlockedUpdate,
    BlockingMod,
    CompatibleVersion,
    CurrentVersion,
    DependencyNode,
    FikaCompatibility,
    ForgeCategory,
    ForgeLicense,
    ForgeMod,
    ForgeUser,
    ForgeVersion,
    ForgeVersionDependency,
    ForgeVersionSummary,
    IncompatibleMod,
    LatestVersion,
    ModUpdate,
    Page,
    PageMeta,
    RecommendedVersion,
    SourceCodeLink,
    UpdatesResult,
    UpToDateMod,
)
from sptmod.core.models import Json


def mapping(value: Json) -> Mapping[str, Json]:
    """Return ``value`` as a JSON object, or an empty one when it is not a mapping."""
    return value if isinstance(value, Mapping) else {}


def _optional_mapping(value: Json) -> Mapping[str, Json] | None:
    return value if isinstance(value, Mapping) else None


def _elements(value: Json) -> list[Json]:
    return list(value) if isinstance(value, list) else []


def mappings(value: Json) -> list[Mapping[str, Json]]:
    """Return ``value`` as a list of JSON objects, skipping nothing and coercing non-objects to ``{}``."""
    return [mapping(item) for item in _elements(value)]


def text(value: Json) -> str:
    """Return ``value`` as a string, or an empty string when it is not one."""
    return value if isinstance(value, str) else ""


def _optional_text(value: Json) -> str | None:
    return value if isinstance(value, str) else None


def _texts(value: Json) -> list[str]:
    return [item for item in _elements(value) if isinstance(item, str)]


def _number(value: Json) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_number(value: Json) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def flag(value: Json) -> bool:
    """Return True only for JSON ``true``; anything else counts as False."""
    return value is True


def _optional_flag(value: Json) -> bool | None:
    return value if isinstance(value, bool) else None


def _fika(value: Json) -> FikaCompatibility:
    if value == "compatible":
        return "compatible"
    if value == "incompatible":
        return "incompatible"
    return "unknown"


def _parse_user(data: Mapping[str, Json]) -> ForgeUser:
    return ForgeUser(
        id=_number(data.get("id")),
        name=text(data.get("name")),
        profile_photo_url=_optional_text(data.get("profile_photo_url")),
        cover_photo_url=_optional_text(data.get("cover_photo_url")),
    )


def _parse_source_code_link(data: Mapping[str, Json]) -> SourceCodeLink:
    return SourceCodeLink(url=text(data.get("url")), label=text(data.get("label")))


def _parse_version_summary(data: Mapping[str, Json]) -> ForgeVersionSummary:
    return ForgeVersionSummary(
        id=_number(data.get("id")),
        version=text(data.get("version")),
        spt_version_constraint=text(data.get("spt_version_constraint")),
        downloads=_number(data.get("downloads")),
        published_at=_optional_text(data.get("published_at")),
    )


def _parse_license(data: Mapping[str, Json] | None) -> ForgeLicense | None:
    if data is None:
        return None
    return ForgeLicense(
        id=_number(data.get("id")),
        name=text(data.get("name")),
        link=text(data.get("link")),
    )


def _parse_category(data: Mapping[str, Json] | None) -> ForgeCategory | None:
    if data is None:
        return None
    return ForgeCategory(
        id=_number(data.get("id")),
        # The live API names the field "title"; older payloads used "name".
        title=text(data.get("title") or data.get("name")),
        slug=text(data.get("slug")),
    )


def parse_mod(data: Mapping[str, Json]) -> ForgeMod:
    """Parse a mod from /mods or /mod/{modId}."""
    owner = _optional_mapping(data.get("owner"))
    return ForgeMod(
        id=_number(data.get("id")),
        hub_id=_optional_number(data.get("hub_id")),
        guid=_optional_text(data.get("guid")),
        name=text(data.get("name")),
        slug=text(data.get("slug")),
        teaser=_optional_text(data.get("teaser")),
        thumbnail=_optional_text(data.get("thumbnail")),
        downloads=_number(data.get("downloads")),
        favourites_count=_number(data.get("favourites_count")),
        endorsements_count=_number(data.get("endorsements_count")),
        owner=None if owner is None else _parse_user(owner),
        additional_authors=[
            _parse_user(item) for item in mappings(data.get("additional_authors"))
        ],
        source_code_links=[
            _parse_source_code_link(item)
            for item in mappings(data.get("source_code_links"))
        ],
        detail_url=text(data.get("detail_url")),
        fika_compatibility=_optional_flag(data.get("fika_compatibility")),
        featured=flag(data.get("featured")),
        contains_ads=flag(data.get("contains_ads")),
        contains_ai_content=flag(data.get("contains_ai_content")),
        shows_profile_binding_notice=flag(data.get("shows_profile_binding_notice")),
        published_at=_optional_text(data.get("published_at")),
        created_at=_optional_text(data.get("created_at")),
        updated_at=_optional_text(data.get("updated_at")),
        description=_optional_text(data.get("description")),
        custom_ai_disclosure=_optional_text(data.get("custom_ai_disclosure")),
        license=_parse_license(_optional_mapping(data.get("license"))),
        category=_parse_category(_optional_mapping(data.get("category"))),
        versions=[
            _parse_version_summary(item) for item in mappings(data.get("versions"))
        ],
    )


def _parse_version_dependency(data: Mapping[str, Json]) -> ForgeVersionDependency:
    return ForgeVersionDependency(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        mod_guid=_optional_text(data.get("mod_guid")),
        mod_name=text(data.get("mod_name")),
        version_constraint=text(data.get("version_constraint")),
        is_optional=flag(data.get("is_optional")),
    )


def parse_version(data: Mapping[str, Json]) -> ForgeVersion:
    """Parse a mod version from /mod/{modId}/versions."""
    return ForgeVersion(
        id=_number(data.get("id")),
        hub_id=_optional_number(data.get("hub_id")),
        version=text(data.get("version")),
        description=_optional_text(data.get("description")),
        link=_optional_text(data.get("link")),
        content_length=_optional_number(data.get("content_length")),
        spt_version_constraint=text(data.get("spt_version_constraint")),
        downloads=_number(data.get("downloads")),
        fika_compatibility=_fika(data.get("fika_compatibility")),
        published_at=_optional_text(data.get("published_at")),
        created_at=_optional_text(data.get("created_at")),
        updated_at=_optional_text(data.get("updated_at")),
        dependencies=[
            _parse_version_dependency(item)
            for item in mappings(data.get("dependencies"))
        ],
        virus_total_links=_texts(data.get("virus_total_links")),
    )


def parse_page[T](
    payload: Mapping[str, Json], parse: Callable[[Mapping[str, Json]], T]
) -> Page[T]:
    """Parse one page of a paginated response, applying ``parse`` to every item."""
    meta = mapping(payload.get("meta"))
    links = mapping(payload.get("links"))
    return Page(
        items=[parse(item) for item in mappings(payload.get("data"))],
        meta=PageMeta(
            current_page=_number(meta.get("current_page")),
            last_page=_number(meta.get("last_page")),
            per_page=_number(meta.get("per_page")),
            total=_number(meta.get("total")),
        ),
        next_url=_optional_text(links.get("next")),
    )


def _parse_current_version(data: Mapping[str, Json]) -> CurrentVersion:
    return CurrentVersion(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        guid=_optional_text(data.get("guid")),
        name=text(data.get("name")),
        version=text(data.get("version")),
        slug=_optional_text(data.get("slug")),
    )


def _parse_recommended_version(data: Mapping[str, Json]) -> RecommendedVersion:
    return RecommendedVersion(
        id=_number(data.get("id")),
        version=text(data.get("version")),
        link=_optional_text(data.get("link")),
        content_length=_optional_number(data.get("content_length")),
        fika_compatibility=_fika(data.get("fika_compatibility")),
        spt_versions=_texts(data.get("spt_versions")),
    )


def _parse_update(data: Mapping[str, Json]) -> ModUpdate:
    return ModUpdate(
        current_version=_parse_current_version(mapping(data.get("current_version"))),
        recommended_version=_parse_recommended_version(
            mapping(data.get("recommended_version"))
        ),
        update_reason=text(data.get("update_reason")),
    )


def _parse_latest_version(data: Mapping[str, Json]) -> LatestVersion:
    return LatestVersion(
        id=_number(data.get("id")),
        version=text(data.get("version")),
        spt_versions=_texts(data.get("spt_versions")),
    )


def _parse_blocking_mod(data: Mapping[str, Json]) -> BlockingMod:
    return BlockingMod(
        mod_id=_number(data.get("mod_id")),
        mod_guid=_optional_text(data.get("mod_guid")),
        mod_name=text(data.get("mod_name")),
        current_version=text(data.get("current_version")),
        constraint=text(data.get("constraint")),
        incompatible_with=text(data.get("incompatible_with")),
    )


def _parse_blocked_update(data: Mapping[str, Json]) -> BlockedUpdate:
    return BlockedUpdate(
        current_version=_parse_current_version(mapping(data.get("current_version"))),
        latest_version=_parse_latest_version(mapping(data.get("latest_version"))),
        block_reason=text(data.get("block_reason")),
        blocking_mods=[
            _parse_blocking_mod(item) for item in mappings(data.get("blocking_mods"))
        ],
    )


def _parse_up_to_date(data: Mapping[str, Json]) -> UpToDateMod:
    return UpToDateMod(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        guid=_optional_text(data.get("guid")),
        name=text(data.get("name")),
        version=text(data.get("version")),
        spt_versions=_texts(data.get("spt_versions")),
    )


def _parse_incompatible(data: Mapping[str, Json]) -> IncompatibleMod:
    return IncompatibleMod(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        guid=_optional_text(data.get("guid")),
        name=text(data.get("name")),
        version=text(data.get("version")),
        reason=text(data.get("reason")),
        latest_compatible_version=_optional_text(data.get("latest_compatible_version")),
    )


def parse_updates(data: Mapping[str, Json]) -> UpdatesResult:
    """Parse the four result buckets of a /mods/updates response."""
    return UpdatesResult(
        spt_version=text(data.get("spt_version")),
        updates=[_parse_update(item) for item in mappings(data.get("updates"))],
        blocked_updates=[
            _parse_blocked_update(item)
            for item in mappings(data.get("blocked_updates"))
        ],
        up_to_date=[
            _parse_up_to_date(item) for item in mappings(data.get("up_to_date"))
        ],
        incompatible_with_spt=[
            _parse_incompatible(item)
            for item in mappings(data.get("incompatible_with_spt"))
        ],
    )


def _parse_compatible_version(
    data: Mapping[str, Json] | None,
) -> CompatibleVersion | None:
    if data is None:
        return None
    return CompatibleVersion(
        id=_number(data.get("id")),
        version=text(data.get("version")),
        link=_optional_text(data.get("link")),
        content_length=_optional_number(data.get("content_length")),
        fika_compatibility=_fika(data.get("fika_compatibility")),
    )


def parse_dependency_node(data: Mapping[str, Json]) -> DependencyNode:
    """Parse a dependency tree node from the API response.

    The conflict field treats anything not JSON false as True (fail-safe:
    missing or null conflict becomes conflict=True, which is safer than
    silently assuming no conflict).
    """
    return DependencyNode(
        id=_number(data.get("id")),
        guid=_optional_text(data.get("guid")),
        name=text(data.get("name")),
        slug=text(data.get("slug")),
        latest_compatible_version=_parse_compatible_version(
            _optional_mapping(data.get("latest_compatible_version"))
        ),
        # Anything not JSON false counts as a conflict (fail-safe).
        conflict=data.get("conflict") is not False,
        dependencies=[
            parse_dependency_node(item) for item in mappings(data.get("dependencies"))
        ],
    )
