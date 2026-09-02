"""Response parsing into the client's dataclasses.

Covers: version object with a null content_length, the full /mods/updates
shape (including the blocked_updates quirks: current_version has no slug,
blocking_mods omitted -> []), and the recursive /mods/dependencies tree
(conflict flag, nullable latest_compatible_version, omitted pairs).
"""

import httpx
import pytest
import respx

from sptmod.core.forge.client import (
    BlockedUpdate,
    BlockingMod,
    CompatibleVersion,
    CurrentVersion,
    DependencyNode,
    ForgeVersion,
    ForgeVersionDependency,
    IncompatibleMod,
    LatestVersion,
    ModUpdate,
    RecommendedVersion,
    UpdatesResult,
    UpToDateMod,
)
from test_forge_client_fixtures import (
    client_factory,
    fake_clock,
    page_envelope,
    recording_sleep,
    respx_router,
    success_envelope,
    version_json,
)

__all__ = ["client_factory", "fake_clock", "recording_sleep", "respx_router"]


def test_mod_versions_parses_null_content_length_and_embedded_dependency(
    client_factory, respx_router: respx.MockRouter
) -> None:
    respx_router.get("/mod/42/versions").mock(
        return_value=httpx.Response(
            200, json=page_envelope([version_json(content_length=None)])
        )
    )
    client = client_factory()

    page = client.mod_versions(42, include=["dependencies", "virus_total_links"])

    assert page.items == [
        ForgeVersion(
            id=900,
            hub_id=800,
            version="1.0.0",
            description="Initial release.",
            link="https://github.com/ex/mod/releases/download/v1.0.0/mod.zip",
            content_length=None,
            spt_version_constraint="^3.8.0",
            downloads=10,
            fika_compatibility="compatible",
            published_at="2025-01-02T00:00:00Z",
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-02T00:00:00Z",
            dependencies=[
                ForgeVersionDependency(
                    id=1,
                    mod_id=2,
                    mod_guid="com.example.dep",
                    mod_name="Dep Mod",
                    version_constraint="^2.0.0",
                    is_optional=False,
                )
            ],
            virus_total_links=["https://virustotal.com/gui/file/abc123"],
        )
    ]


def test_updates_parses_all_four_lists_with_blocked_update_quirks(
    client_factory, respx_router: respx.MockRouter
) -> None:
    data = {
        "spt_version": "3.11.5",
        "updates": [
            {
                "current_version": {
                    "id": 21,
                    "mod_id": 5,
                    "guid": "com.example.mod",
                    "name": "Example Mod",
                    "slug": "example-mod",
                    "version": "1.0.0",
                },
                "recommended_version": {
                    "id": 22,
                    "version": "1.1.0",
                    "link": "https://dl.example/1.1.0.zip",
                    "content_length": 2048,
                    "fika_compatibility": "compatible",
                    "spt_versions": ["3.11.5"],
                },
                "update_reason": "Newer compatible version available.",
            }
        ],
        "blocked_updates": [
            {
                # Verified fact: blocked_updates[].current_version has no slug.
                "current_version": {
                    "id": 11,
                    "mod_id": 2,
                    "guid": "com.example.dep",
                    "name": "Dep Mod",
                    "version": "1.9.0",
                },
                "latest_version": {
                    "id": 12,
                    "version": "2.0.0",
                    "spt_versions": ["3.11.5"],
                },
                "block_reason": "Conflicting mod dependency.",
                # Verified fact: blocking_mods key omitted when empty.
            }
        ],
        "up_to_date": [
            {
                "id": 31,
                "mod_id": 6,
                "guid": "com.example.uptodate",
                "name": "Up To Date Mod",
                "version": "2.0.0",
                "spt_versions": ["3.11.5"],
            }
        ],
        "incompatible_with_spt": [
            {
                "id": 41,
                "mod_id": 7,
                "guid": "com.example.incompat",
                "name": "Incompatible Mod",
                "version": "0.9.0",
                "reason": "No version for this SPT release.",
                "latest_compatible_version": "1.0.0",
            }
        ],
    }
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(200, json=success_envelope(data))
    )
    client = client_factory()

    result = client.updates(mods=[("5", "1.0.0")], spt_version="3.11.5")

    assert result == UpdatesResult(
        spt_version="3.11.5",
        updates=[
            ModUpdate(
                current_version=CurrentVersion(
                    id=21, mod_id=5, guid="com.example.mod", name="Example Mod",
                    version="1.0.0", slug="example-mod",
                ),
                recommended_version=RecommendedVersion(
                    id=22, version="1.1.0", link="https://dl.example/1.1.0.zip",
                    content_length=2048, fika_compatibility="compatible",
                    spt_versions=["3.11.5"],
                ),
                update_reason="Newer compatible version available.",
            )
        ],
        blocked_updates=[
            BlockedUpdate(
                current_version=CurrentVersion(
                    id=11, mod_id=2, guid="com.example.dep", name="Dep Mod",
                    version="1.9.0", slug=None,
                ),
                latest_version=LatestVersion(
                    id=12, version="2.0.0", spt_versions=["3.11.5"]
                ),
                block_reason="Conflicting mod dependency.",
                blocking_mods=[],
            )
        ],
        up_to_date=[
            UpToDateMod(
                id=31, mod_id=6, guid="com.example.uptodate", name="Up To Date Mod",
                version="2.0.0", spt_versions=["3.11.5"],
            )
        ],
        incompatible_with_spt=[
            IncompatibleMod(
                id=41, mod_id=7, guid="com.example.incompat", name="Incompatible Mod",
                version="0.9.0", reason="No version for this SPT release.",
                latest_compatible_version="1.0.0",
            )
        ],
    )


def test_updates_parses_populated_blocking_mods(
    client_factory, respx_router: respx.MockRouter
) -> None:
    data = {
        "spt_version": "3.11.5",
        "updates": [],
        "blocked_updates": [
            {
                "current_version": {
                    "id": 11, "mod_id": 2, "guid": "com.example.dep", "name": "Dep Mod",
                    "version": "1.9.0",
                },
                "latest_version": {
                    "id": 12,
                    "version": "2.0.0",
                    "spt_versions": ["3.11.5"],
                },
                "block_reason": "Conflicting mod dependency.",
                "blocking_mods": [
                    {
                        "mod_id": 9,
                        "mod_guid": "com.example.blocker",
                        "mod_name": "Blocker Mod",
                        "current_version": "1.0.0",
                        "constraint": "^1.0.0",
                        "incompatible_with": "2.0.0",
                    }
                ],
            }
        ],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(200, json=success_envelope(data))
    )
    client = client_factory()

    result = client.updates(mods=[("2", "1.9.0")], spt_version="3.11.5")

    assert result.blocked_updates[0].blocking_mods == [
        BlockingMod(
            mod_id=9,
            mod_guid="com.example.blocker",
            mod_name="Blocker Mod",
            current_version="1.0.0",
            constraint="^1.0.0",
            incompatible_with="2.0.0",
        )
    ]


def test_dependencies_parses_recursive_tree_with_conflict_and_nullable_version(
    client_factory, respx_router: respx.MockRouter
) -> None:
    nested_dep = {
        "id": 100,
        "guid": "com.example.nested",
        "name": "Nested Dep",
        "slug": "nested-dep",
        "latest_compatible_version": {
            "id": 500,
            "version": "1.0.0",
            "link": "https://dl.example/nested-1.0.0.zip",
            "content_length": 512,
            "fika_compatibility": "unknown",
        },
        "conflict": False,
        "dependencies": [],
    }
    dep_with_conflict = {
        "id": 200,
        "guid": "com.example.conflicted",
        "name": "Conflicted Dep",
        "slug": "conflicted-dep",
        "latest_compatible_version": None,
        "conflict": True,
        "dependencies": [nested_dep],
    }
    data = {"5:1.2.0": [dep_with_conflict]}
    respx_router.get("/mods/dependencies").mock(
        return_value=httpx.Response(200, json=success_envelope(data))
    )
    client = client_factory()

    result = client.dependencies(mods=[("5", "1.2.0")], spt_version="3.11.5")

    expected_nested = DependencyNode(
        id=100,
        guid="com.example.nested",
        name="Nested Dep",
        slug="nested-dep",
        latest_compatible_version=CompatibleVersion(
            id=500, version="1.0.0", link="https://dl.example/nested-1.0.0.zip",
            content_length=512, fika_compatibility="unknown",
        ),
        conflict=False,
        dependencies=[],
    )
    expected_root = DependencyNode(
        id=200,
        guid="com.example.conflicted",
        name="Conflicted Dep",
        slug="conflicted-dep",
        latest_compatible_version=None,
        conflict=True,
        dependencies=[expected_nested],
    )
    assert result == {"5:1.2.0": [expected_root]}


def test_dependencies_omits_unresolvable_pairs_and_keeps_empty_list_for_no_deps(
    client_factory, respx_router: respx.MockRouter
) -> None:
    # Two pairs queried; only one resolves ("9:9.9.9" is unresolvable and
    # omitted by the server). The resolved pair has no dependencies -> [].
    data = {"5:1.2.0": []}
    respx_router.get("/mods/dependencies").mock(
        return_value=httpx.Response(200, json=success_envelope(data))
    )
    client = client_factory()

    result = client.dependencies(
        mods=[("5", "1.2.0"), ("9", "9.9.9")], spt_version="3.11.5"
    )

    assert result == {"5:1.2.0": []}
    assert "9:9.9.9" not in result


@pytest.mark.parametrize(
    "raw_conflict", ["true", 1, None], ids=["string", "int", "null"]
)
def test_dependencies_conflict_fails_safe_for_any_non_false_value(
    client_factory, respx_router: respx.MockRouter, raw_conflict: object
) -> None:
    """Anything that is not JSON ``false`` must be treated as a conflict."""
    node = {
        "id": 1,
        "guid": "com.example.dep",
        "name": "Dep",
        "slug": "dep",
        "latest_compatible_version": None,
        "conflict": raw_conflict,
        "dependencies": [],
    }
    respx_router.get("/mods/dependencies").mock(
        return_value=httpx.Response(200, json=success_envelope({"5:1.0.0": [node]}))
    )
    client = client_factory()

    result = client.dependencies(mods=[("5", "1.0.0")], spt_version="3.11.5")

    assert result["5:1.0.0"][0].conflict is True


def test_dependencies_conflict_false_stays_false(
    client_factory, respx_router: respx.MockRouter
) -> None:
    node = {
        "id": 1,
        "guid": "com.example.dep",
        "name": "Dep",
        "slug": "dep",
        "latest_compatible_version": None,
        "conflict": False,
        "dependencies": [],
    }
    respx_router.get("/mods/dependencies").mock(
        return_value=httpx.Response(200, json=success_envelope({"5:1.0.0": [node]}))
    )
    client = client_factory()

    result = client.dependencies(mods=[("5", "1.0.0")], spt_version="3.11.5")

    assert result["5:1.0.0"][0].conflict is False
