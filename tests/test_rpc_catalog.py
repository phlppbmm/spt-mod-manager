"""In-process tests for the catalog side of ``sptmod rpc <name>``:
``sync_catalog`` against a respx-mocked Forge plus the cache routes
``search_catalog``, ``browse_catalog``, ``clear_catalog_cache`` and
``get_thumbnails``. Split out of :mod:`test_rpc`, which keeps the envelope
and error-mapping cases; the
Forge is always mocked, so no test reaches the real API.
"""

import base64
import json
import re
from pathlib import Path

import pytest
import respx

from conftest import invoke_rpc
from fixture_types import JsonDict
from forge_json import mod_json_minimal, page_envelope
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import DEFAULT_BASE_URL
from sptmod.core.repo import Repo
from sptmod.core.workspace import download_cache_dir

# --------------------------------------------------------------------------
# Forge-backed function, mocked with respx.
# --------------------------------------------------------------------------


def test_sync_catalog_reaches_forge_through_respx_and_reports_the_page_count(
    tmp_path: Path,
) -> None:
    """sync_catalog hits /mods endpoint and result includes pages and operation_id.

    The empty page envelope returns pages=1; the result event has operation_id
    at data level due to operation_id=True on the RpcSpec.
    """
    db_path = tmp_path / "catalog.db"
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        empty_page = page_envelope([], current_page=1, last_page=1, total=0)
        router.get("/mods").respond(200, json=empty_page)

        exit_code, lines = invoke_rpc(
            "sync_catalog",
            {"path": "/unused", "full": True, "db_path": str(db_path)},
        )

    assert exit_code == 0
    assert lines[0]["event"] == "progress"
    assert lines[0]["step"] == "page"
    assert lines[-1] == {
        "event": "result",
        "data": {
            "new_mods": 0,
            "updated_mods": 0,
            "new_versions": 0,
            "updated_versions": 0,
            "pages": 1,
            "full": True,
            "operation_id": lines[-1]["data"]["operation_id"],
        },
    }


# --------------------------------------------------------------------------
# Catalog cache: search_catalog, browse_catalog, clear_catalog_cache.
# --------------------------------------------------------------------------


def test_search_catalog_finds_a_synced_mod_by_name(clean_repo: Repo, tmp_path: Path) -> None:
    """search_catalog finds synced mods by name and reports state and
    installed version."""
    db_path = tmp_path / "catalog.db"
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").respond(200, json=envelope)
        invoke_rpc(
            "sync_catalog",
            {"path": str(clean_repo.path), "db_path": str(db_path)},
        )

        exit_code, lines = invoke_rpc(
            "search_catalog",
            {
                "path": str(clean_repo.path),
                "query": "Example",
                "db_path": str(db_path),
            },
        )

    assert exit_code == 0
    data = lines[0]["data"]
    assert [entry["mod"]["name"] for entry in data] == ["Example Mod"]
    assert data[0]["state"] == "available"
    assert data[0]["installed_version"] is None


def test_browse_catalog_available_filter_keeps_the_uninstalled_synced_mod(clean_repo: Repo, tmp_path: Path) -> None:
    """browse_catalog with 'available' filter shows only uninstalled mods and
    'installed' filter shows only installed mods."""
    db_path = tmp_path / "catalog.db"
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").respond(200, json=envelope)
        invoke_rpc(
            "sync_catalog",
            {"path": str(clean_repo.path), "db_path": str(db_path)},
        )

        available_exit, available_lines = invoke_rpc(
            "browse_catalog",
            {
                "path": str(clean_repo.path),
                "filter": "available",
                "db_path": str(db_path),
            },
        )
        installed_exit, installed_lines = invoke_rpc(
            "browse_catalog",
            {
                "path": str(clean_repo.path),
                "filter": "installed",
                "db_path": str(db_path),
            },
        )

    assert available_exit == 0
    assert installed_exit == 0
    assert [entry["mod"]["name"] for entry in available_lines[0]["data"]] == ["Example Mod"]
    assert installed_lines[0]["data"] == []


def test_browse_catalog_exposes_the_first_seen_at_stamp_of_a_synced_mod(clean_repo: Repo, tmp_path: Path) -> None:
    """browse_catalog returns mod.first_seen_at as ISO8601 timestamp string.

    After sync_catalog, browsing returns the timestamp in format
    \\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z (UTC).
    """
    db_path = tmp_path / "catalog.db"
    envelope = page_envelope([mod_json_minimal(id=1, name="Example Mod")])
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").respond(200, json=envelope)
        invoke_rpc(
            "sync_catalog",
            {"path": str(clean_repo.path), "db_path": str(db_path)},
        )

        exit_code, lines = invoke_rpc(
            "browse_catalog",
            {
                "path": str(clean_repo.path),
                "filter": "all",
                "db_path": str(db_path),
            },
        )

    assert exit_code == 0
    stamp = lines[0]["data"][0]["mod"]["first_seen_at"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)


def test_clear_catalog_cache_deletes_the_db_file_and_reports_removed_false_on_retry(
    tmp_path: Path,
) -> None:
    """clear_catalog_cache deletes db and returns removed=True; retry returns False.

    The first call finds the db file and removes it; the second call returns
    removed=False because the file is already gone.
    """
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    assert db.exists()

    exit_code, lines = invoke_rpc("clear_catalog_cache", {"db_path": str(db)})

    assert exit_code == 0
    assert lines[0]["data"] == {
        "db_path": str(db),
        "removed": True,
        "downloads_path": None,
        "downloads_freed_bytes": 0,
    }
    assert not db.exists()

    second_exit, second_lines = invoke_rpc("clear_catalog_cache", {"db_path": str(db)})

    assert second_exit == 0
    assert second_lines[0]["data"]["removed"] is False


def test_clear_catalog_cache_with_downloads_also_clears_the_archive_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``downloads`` flag must reach ``clear_catalog_cache`` and its
    return value must carry the archive directory and the bytes it freed --
    not just clear the catalog db as the ``downloads=False`` default does.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = tmp_path / "forge-cache.db"
    with Catalog(db):
        pass
    cache_dir = download_cache_dir()
    entry = cache_dir / "555"
    entry.mkdir(parents=True)
    payload = b"cached archive bytes"
    (entry / "mod.zip").write_bytes(payload)
    meta = json.dumps({"name": "mod.zip", "size": len(payload), "url": "unused"})
    (entry / "meta.json").write_text(meta, encoding="utf-8")
    expected_freed = len(payload) + len(meta.encode("utf-8"))

    exit_code, lines = invoke_rpc("clear_catalog_cache", {"db_path": str(db), "downloads": True})

    assert exit_code == 0
    assert lines[0]["data"] == {
        "db_path": str(db),
        "removed": True,
        "downloads_path": str(cache_dir),
        "downloads_freed_bytes": expected_freed,
    }
    assert not entry.exists()


THUMBNAIL_URL = "https://files.example.test/mods/47/thumb.png"
THUMBNAIL_BYTES = b"\x89PNG\r\n\x1a\nthe pixels of mod 47"


def test_get_thumbnails_answers_a_data_url_per_forge_id_over_the_wire(tmp_path: Path) -> None:
    """The route carries the images to the frontend, so the answer must reach
    it complete: the requested ids as keys of the ``thumbnails`` object, the
    picture of a mod as a ``data:`` URL and None for a mod without one. The
    image host serves the picture only against the Forge origin as
    ``Referer``, hence a data URL here also proves the header was sent.
    """
    db = tmp_path / "catalog.db"
    cache_dir = tmp_path / "thumbnails"
    cache_dir.mkdir()
    mods = [
        mod_json_minimal(id=47, thumbnail=THUMBNAIL_URL),
        mod_json_minimal(id=48, name="Second Mod", slug="second-mod", thumbnail=None),
    ]
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{DEFAULT_BASE_URL}/mods").respond(200, json=page_envelope(mods))
        image = router.get(THUMBNAIL_URL, headers={"Referer": "https://sp-mod.com/"})
        image.respond(200, content=THUMBNAIL_BYTES, headers={"Content-Type": "image/png"})
        router.get(THUMBNAIL_URL).respond(403)
        invoke_rpc("sync_catalog", {"path": "/unused", "db_path": str(db)})

        exit_code, lines = invoke_rpc(
            "get_thumbnails",
            {"mod_ids": [47, 48], "db_path": str(db), "cache_dir": str(cache_dir)},
        )

    encoded = base64.b64encode(THUMBNAIL_BYTES).decode("ascii")
    assert exit_code == 0
    assert lines[-1] == {
        "event": "result",
        "data": {"thumbnails": {"47": f"data:image/png;base64,{encoded}", "48": None}},
    }


def _mod_with_version(mod_id: int, name: str, version: str, spt_constraint: str) -> JsonDict:
    """Build the Forge JSON of a mod offering exactly one version."""
    return mod_json_minimal(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        versions=[
            {
                "id": mod_id * 100,
                "version": version,
                "spt_version_constraint": spt_constraint,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )


def _offered(entries: list[JsonDict]) -> list[tuple[str, str | None]]:
    """Return the version each browsed entry offers, by mod name."""
    return [
        (
            entry["mod"]["name"],
            None if entry["latest_version"] is None else entry["latest_version"]["version"],
        )
        for entry in entries
    ]


def test_browse_catalog_judges_the_entries_against_the_requested_spt_version(
    clean_repo: Repo, tmp_path: Path
) -> None:
    """The Compatibility filter of the Mods view sends the version the user
    picked, so it has to travel over the route and decide what is offered: the
    game directory runs SPT 4.1.0, which fits Alpha Mod's ``^4.0.0``, while a
    browse for 3.8.0 must offer Beta Mod's version instead and none for Alpha
    Mod. Sending no version keeps judging against the installed one.
    """
    db_path = tmp_path / "catalog.db"
    envelope = page_envelope(
        [
            _mod_with_version(1, "Alpha Mod", "1.0.0", "^4.0.0"),
            _mod_with_version(2, "Beta Mod", "2.0.0", "^3.8.0"),
        ]
    )
    request: JsonDict = {"path": str(clean_repo.path), "db_path": str(db_path)}
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").respond(200, json=envelope)
        invoke_rpc("sync_catalog", request)

        installed_exit, installed_lines = invoke_rpc("browse_catalog", request)
        chosen_exit, chosen_lines = invoke_rpc(
            "browse_catalog", {**request, "spt_version": "3.8.0"}
        )

    assert installed_exit == 0
    assert chosen_exit == 0
    assert _offered(installed_lines[0]["data"]) == [("Alpha Mod", "1.0.0"), ("Beta Mod", None)]
    assert _offered(chosen_lines[0]["data"]) == [("Alpha Mod", None), ("Beta Mod", "2.0.0")]
