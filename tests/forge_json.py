"""JSON builders for Forge API responses and catalog rows.

Not a test module: the ``test_forge_*`` and ``test_services_*`` suites import
these builders so the shape of a mocked Forge payload lives in one place.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sptmod.core.forge.client import DEFAULT_BASE_URL
from sptmod.core.models import ModInfo


def success_envelope(data: Any) -> dict[str, Any]:
    """Build a Forge API success envelope with the given data payload.

    Returns {"success": True, "data": data}, the wrapper required by all
    successful Forge API responses (e.g., /mod/{id}, /mods/updates).
    """
    return {"success": True, "data": data}


def error_envelope(code: str, message: str) -> dict[str, Any]:
    """Build a Forge API error envelope with code and message.

    Returns {"success": False, "code": code, "message": message}, the wrapper
    for API errors (e.g., VALIDATION_FAILED, NOT_FOUND); _fetch() unwraps this
    to raise ForgeApiError.
    """
    return {"success": False, "code": code, "message": message}


def page_envelope(
    items: list[Any],
    *,
    current_page: int = 1,
    last_page: int = 1,
    per_page: int = 50,
    total: int | None = None,
    next_url: str | None = None,
) -> dict[str, Any]:
    """Build a paginated response envelope with success=true, items, links, and metadata."""
    resolved_total = len(items) if total is None else total
    return {
        "success": True,
        "data": items,
        "links": {
            "first": f"{DEFAULT_BASE_URL}/mods?page=1",
            "last": f"{DEFAULT_BASE_URL}/mods?page={last_page}",
            "prev": None,
            "next": next_url,
        },
        "meta": {
            "current_page": current_page,
            "from": 1 if items else None,
            "last_page": last_page,
            "path": f"{DEFAULT_BASE_URL}/mods",
            "per_page": per_page,
            "to": len(items),
            "total": resolved_total,
        },
    }


def owner_json(**overrides: Any) -> dict[str, Any]:
    """Build a ForgeUser (owner) object with default values, overridable by kwargs."""
    data = {
        "id": 10,
        "name": "Alice",
        "profile_photo_url": "https://cdn.example/alice.png",
        "cover_photo_url": "https://cdn.example/alice-cover.png",
    }
    data.update(overrides)
    return data


def mod_json_minimal(**overrides: Any) -> dict[str, Any]:
    """Build a minimal ForgeMod object (as from /mods list) with default values, overridable by kwargs."""
    data: dict[str, Any] = {
        "id": 1,
        "hub_id": 100,
        "guid": "com.example.mod",
        "name": "Example Mod",
        "slug": "example-mod",
        "teaser": "A short teaser.",
        "thumbnail": "https://cdn.example/thumb.png",
        "downloads": 1234,
        "favourites_count": 56,
        "owner": owner_json(),
        "additional_authors": [],
        "source_code_links": [{"url": "https://github.com/ex/mod", "label": "GitHub"}],
        "detail_url": "https://forge.sp-tarkov.com/mods/example-mod",
        "fika_compatibility": True,
        "featured": False,
        "contains_ads": False,
        "contains_ai_content": False,
        "shows_profile_binding_notice": False,
        "published_at": "2025-01-01T00:00:00Z",
        "created_at": "2024-12-01T00:00:00Z",
        "updated_at": "2025-06-01T00:00:00Z",
    }
    data.update(overrides)
    return data


def mod_json_full(**overrides: Any) -> dict[str, Any]:
    """Build a full ForgeMod object (as from /mod/{id} detail) with include fields, overridable by kwargs."""
    data = mod_json_minimal(
        description="Full description.",
        custom_ai_disclosure="Uses AI for translation.",
        license={"id": 1, "name": "MIT License", "link": "https://mit.example"},
        category={"id": 5, "title": "Weapons", "slug": "weapons"},
        versions=[
            {
                "id": 900,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 10,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    data.update(overrides)
    return data


def version_json(**overrides: Any) -> dict[str, Any]:
    """Build a ForgeVersion object with embedded dependencies and metadata, overridable by kwargs."""
    data: dict[str, Any] = {
        "id": 900,
        "hub_id": 800,
        "version": "1.0.0",
        "description": "Initial release.",
        "link": "https://github.com/ex/mod/releases/download/v1.0.0/mod.zip",
        "content_length": None,
        "spt_version_constraint": "^3.8.0",
        "downloads": 10,
        "fika_compatibility": "compatible",
        "published_at": "2025-01-02T00:00:00Z",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-02T00:00:00Z",
        "dependencies": [
            {
                "id": 1,
                "mod_id": 2,
                "mod_guid": "com.example.dep",
                "mod_name": "Dep Mod",
                "version_constraint": "^2.0.0",
                "is_optional": False,
            }
        ],
        "virus_total_links": ["https://virustotal.com/gui/file/abc123"],
    }
    data.update(overrides)
    return data


def make_mod_info(
    *,
    forge_id: str = "",
    name: str = "Some Mod",
    version: str = "1.0.0",
    **overrides: Any,
) -> ModInfo:
    """Build a ``ModInfo`` fixture with sensible defaults for classify() tests.

    ``forge_id`` and ``name`` are the two fields classify() matches on;
    everything else defaults to plausible but otherwise irrelevant values.
    """
    fields: dict[str, Any] = {
        "name": name,
        "version": version,
        "mod_type": "server",
        "author": "Someone",
        "spt_range": "",
        "state": "installed",
        "commit": "abc123",
        "date": datetime(2025, 1, 1),
        "forge_id": forge_id,
        "forge_version_id": "",
        "guid": "",
        "source": "forge",
    }
    fields.update(overrides)
    return ModInfo(**fields)


def current_version_json(
    *, version_id: int, mod_id: int, name: str, version: str, slug: str | None = None
) -> dict[str, Any]:
    """The ``current_version`` object of a ``/mods/updates`` entry."""
    return {
        "id": version_id,
        "mod_id": mod_id,
        "guid": None,
        "name": name,
        "version": version,
        "slug": slug,
    }


def recommended_version_json(
    *,
    version_id: int,
    version: str,
    link: str,
    content_length: int,
    spt_version: str,
    fika_compatibility: str = "unknown",
) -> dict[str, Any]:
    """The ``recommended_version`` object of a ``/mods/updates`` entry."""
    return {
        "id": version_id,
        "version": version,
        "link": link,
        "content_length": content_length,
        "fika_compatibility": fika_compatibility,
        "spt_versions": [spt_version],
    }


def updates_payload(
    spt_version: str,
    *,
    updates: list[dict[str, Any]] | None = None,
    blocked_updates: list[dict[str, Any]] | None = None,
    up_to_date: list[dict[str, Any]] | None = None,
    incompatible_with_spt: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a ``/mods/updates`` payload with its four report lists."""
    return {
        "spt_version": spt_version,
        "updates": list(updates or []),
        "blocked_updates": list(blocked_updates or []),
        "up_to_date": list(up_to_date or []),
        "incompatible_with_spt": list(incompatible_with_spt or []),
    }


def mod_json_with_versions(
    *, mod_id: int, name: str, constraints: Sequence[str]
) -> dict[str, Any]:
    """Build a Forge mod envelope holding one version per SPT constraint.

    The mod carries ``mod_id`` and ``name``, and one published version per
    entry of ``constraints``, each declaring that entry as its
    ``spt_version_constraint``. An empty ``constraints`` sequence builds a mod
    the catalog knows by name and ID but holds no version of.
    """
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=name.casefold().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name=f"{name} author"),
        detail_url=f"{DEFAULT_BASE_URL}/mods/{name.casefold().replace(' ', '-')}",
        versions=[
            {
                "id": mod_id * 100 + index,
                "version": f"1.{index}.0",
                "spt_version_constraint": constraint,
                "downloads": 7,
                "published_at": "2025-01-02T00:00:00Z",
            }
            for index, constraint in enumerate(constraints)
        ],
    )
