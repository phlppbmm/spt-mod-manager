"""In-process tests for the Forge-backed side of ``sptmod rpc <name>``:
``get_mod_details``, ``plan_mod_install``, ``install_mod``, ``check_updates``,
``update_mod``, ``import_list`` with apply, and the ``apply_spt_release``
version change. Split out of :mod:`test_rpc`, which keeps the envelope and
error-mapping cases; the Forge and the SPT release source are always mocked,
so no test reaches a real host.
"""

import json
from pathlib import Path

import httpx
import respx

from conftest import clean_repo_at, invoke_rpc
from fixture_types import (
    MakeArchive,
    MakeSptRelease,
    TrailerInstallCommit,
)
from fixtures_rpc import assert_progress_then_result, install_request
from fixtures_spt import SptReleaseSource
from forge_json import mod_json_full, owner_json, page_envelope, version_json
from sptmod.core.forge.client import DEFAULT_BASE_URL

SPT_CONSTRAINT = "^4.0.0"

# --------------------------------------------------------------------------
# Forge install path: get_mod_details, plan_mod_install, install_mod,
# check_updates, update_mod, import_list with apply.
# --------------------------------------------------------------------------


def _seed_forge_mod(
    db_path: Path,
    router: respx.MockRouter,
    *,
    mod_id: int,
    name: str,
    version: str,
    version_id: int,
    link: str,
    content_length: int,
) -> None:
    """Sync ``name`` into the catalog at ``db_path`` and mock its version
    list and (empty) dependency tree, so ``plan_mod_install``/``install_mod``
    can resolve it end to end."""
    slug = name.lower().replace(" ", "-")
    mod = mod_json_full(
        id=mod_id,
        name=name,
        slug=slug,
        owner=owner_json(id=mod_id * 10, name="Alice"),
        versions=[
            {
                "id": version_id,
                "version": version,
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1)
    router.get("/mods").respond(200, json=page)
    sync_exit, _sync_lines = invoke_rpc("sync_catalog", {"path": "/unused", "full": True, "db_path": str(db_path)})
    assert sync_exit == 0

    versions_page = page_envelope(
        [
            version_json(
                id=version_id,
                version=version,
                link=link,
                content_length=content_length,
                spt_version_constraint=SPT_CONSTRAINT,
            )
        ],
        current_page=1,
        last_page=1,
        total=1,
    )
    router.get(f"/mod/{mod_id}/versions").respond(200, json=versions_page)

    def _empty_dependencies(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    router.get("/mods/dependencies").mock(side_effect=_empty_dependencies)


def test_get_mod_details_combines_cached_data_with_the_live_description(
    tmp_path: Path,
) -> None:
    """get_mod_details combines cached mod data with live API description and
    removes HTML tags."""
    db_path = tmp_path / "catalog.db"
    mod = mod_json_full(
        id=42,
        name="Detail Mod",
        slug="detail-mod",
        description="<p>Adds <b>cool</b> stuff.</p>",
        versions=[
            {
                "id": 420,
                "version": "1.0.0",
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 5,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1)
    versions_page = page_envelope(
        [
            version_json(
                id=420,
                version="1.0.0",
                link="https://dl.example/detail-mod-1.0.0.zip",
                content_length=123,
                spt_version_constraint=SPT_CONSTRAINT,
            )
        ],
        current_page=1,
        last_page=1,
        total=1,
    )
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods").respond(200, json=page)
        invoke_rpc("sync_catalog", {"path": "/unused", "db_path": str(db_path)})
        router.get("/mod/42/versions").respond(200, json=versions_page)
        router.get("/mod/42").respond(200, json={"success": True, "data": mod})

        exit_code, lines = invoke_rpc("get_mod_details", {"mod_id": 42, "db_path": str(db_path)})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["mod"]["name"] == "Detail Mod"
    assert data["versions"] == [
        {
            "id": 420,
            "mod_id": 42,
            "version": "1.0.0",
            "link": "https://dl.example/detail-mod-1.0.0.zip",
            "content_length": 123,
            "spt_version_constraint": SPT_CONSTRAINT,
            "downloads": 10,
            "published_at": "2025-01-02T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
            "fika_compatibility": "compatible",
        }
    ]
    assert data["description"] == "Adds cool stuff."


def test_plan_mod_install_resolves_the_target_and_its_download_link(fake_game_dir: Path, tmp_path: Path) -> None:
    """plan_mod_install(\"Target Mod\", \"2.0.0\") returns entries with link and version_id.

    The response includes the download link from the version record and the
    version_id (9010) for later use by install_mod.
    """
    clean_repo_at(fake_game_dir)
    db_path = tmp_path / "catalog.db"
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        _seed_forge_mod(
            db_path,
            router,
            mod_id=901,
            name="Target Mod",
            version="2.0.0",
            version_id=9010,
            link="https://dl.example/target-mod-2.0.0.zip",
            content_length=1000,
        )

        exit_code, lines = invoke_rpc(
            "plan_mod_install",
            {
                "path": str(fake_game_dir),
                "mod_id_or_name": "Target Mod",
                "version": "2.0.0",
                "db_path": str(db_path),
            },
        )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["spt_version"] == "4.1.0"
    assert data["entries"] == [
        {
            "mod_id": 901,
            "name": "Target Mod",
            "guid": "com.example.mod",
            "version": "2.0.0",
            "version_id": 9010,
            "link": "https://dl.example/target-mod-2.0.0.zip",
            "content_length": 1000,
            "is_dependency": False,
        }
    ]


def test_install_mod_downloads_and_installs_the_target(
    fake_game_dir: Path, tmp_path: Path, make_archive: MakeArchive
) -> None:
    """install_mod downloads a mod archive from the resolved link and
    installs it with Forge metadata."""
    clean_repo_at(fake_game_dir)
    db_path = tmp_path / "catalog.db"
    archive = make_archive(
        {
            "SPT/user/mods/Target Mod/package.json": json.dumps(
                {"name": "Target Mod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = archive.read_bytes()
    link = "https://dl.example/target-mod-2.0.0.zip"
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        _seed_forge_mod(
            db_path,
            router,
            mod_id=902,
            name="Target Mod",
            version="2.0.0",
            version_id=9020,
            link=link,
            content_length=len(body),
        )
        router.get(link).respond(200, content=body, headers={"Content-Length": str(len(body))})

        exit_code, lines = invoke_rpc(
            "install_mod",
            {
                "path": str(fake_game_dir),
                "mod_id_or_name": "Target Mod",
                "version": "2.0.0",
                "db_path": str(db_path),
                "operation_id": "caller-chosen-id",
            },
        )

    assert exit_code == 0
    assert_progress_then_result(lines)
    result = lines[-1]["data"]
    assert result["operation_id"] == "caller-chosen-id"
    assert result["plan"]["entries"][0]["name"] == "Target Mod"
    assert len(result["installed"]) == 1
    installed_mod = result["installed"][0]["mod"]
    assert installed_mod["name"] == "Target Mod"
    assert installed_mod["version"] == "2.0.0"
    assert installed_mod["forge_id"] == "902"
    assert installed_mod["forge_version_id"] == "9020"
    assert installed_mod["source"] == link


def test_check_updates_reports_an_available_update(
    fake_game_dir: Path, tmp_path: Path, install_commit: TrailerInstallCommit
) -> None:
    """check_updates queries /mods/updates and returns available with new version info.

    CoolMod 1.0.0 (Forge ID 10) has an update to 2.0.0; available includes the
    link and new version details with version_id=200.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "10"})
    db_path = tmp_path / "catalog.db"
    data = {
        "spt_version": "4.1.0",
        "updates": [
            {
                "current_version": {
                    "id": 100,
                    "mod_id": 10,
                    "guid": None,
                    "name": "CoolMod",
                    "version": "1.0.0",
                    "slug": "coolmod",
                },
                "recommended_version": {
                    "id": 200,
                    "version": "2.0.0",
                    "link": "https://dl.example/coolmod-2.0.0.zip",
                    "content_length": 4242,
                    "fika_compatibility": "unknown",
                    "spt_versions": ["4.1.0"],
                },
                "update_reason": "newer version available",
            }
        ],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods/updates").respond(200, json={"success": True, "data": data})

        exit_code, lines = invoke_rpc(
            "check_updates",
            {"path": str(fake_game_dir), "db_path": str(db_path)},
        )

    assert exit_code == 0
    body = lines[0]["data"]
    assert body["available"] == [
        {
            "name": "CoolMod",
            "installed_version": "1.0.0",
            "new_version": "2.0.0",
            "version_id": 200,
            "link": "https://dl.example/coolmod-2.0.0.zip",
            "content_length": 4242,
            "reason": "newer version available",
        }
    ]


def test_update_mod_applies_the_recommended_version(
    fake_game_dir: Path,
    tmp_path: Path,
    install_commit: TrailerInstallCommit,
    make_archive: MakeArchive,
) -> None:
    """update_mod(\"CoolMod\") downloads from link and result shows version change.

    Installed CoolMod 1.0.0 is replaced with 2.0.0; the result includes
    operation_id in data, previous_version=\"1.0.0\", and new commit hash.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "10"})
    db_path = tmp_path / "catalog.db"
    new_archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "2.0.0", "author": "Acme"})},
        "zip",
    )
    body = new_archive.read_bytes()
    link = "https://dl.example/coolmod-2.0.0.zip"
    data = {
        "spt_version": "4.1.0",
        "updates": [
            {
                "current_version": {
                    "id": 100,
                    "mod_id": 10,
                    "guid": None,
                    "name": "CoolMod",
                    "version": "1.0.0",
                    "slug": "coolmod",
                },
                "recommended_version": {
                    "id": 200,
                    "version": "2.0.0",
                    "link": link,
                    "content_length": len(body),
                    "fika_compatibility": "unknown",
                    "spt_versions": ["4.1.0"],
                },
                "update_reason": "newer version available",
            }
        ],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mods/updates").respond(200, json={"success": True, "data": data})
        router.get(link).respond(200, content=body, headers={"Content-Length": str(len(body))})

        exit_code, lines = invoke_rpc(
            "update_mod",
            {
                "path": str(fake_game_dir),
                "name": "CoolMod",
                "db_path": str(db_path),
                "operation_id": "update-op-id",
            },
        )

    assert exit_code == 0
    assert_progress_then_result(lines)
    result = lines[-1]["data"]
    assert result["operation_id"] == "update-op-id"
    assert result["name"] == "CoolMod"
    assert result["previous_version"] == "1.0.0"
    assert result["version"] == "2.0.0"
    assert result["commit"] != ""
    assert result["notice"] == ""


def test_import_list_reports_a_mod_only_the_list_has_as_missing(fake_game_dir: Path, tmp_path: Path) -> None:
    """import_list reads \"GhostMod 2.0.0\" from file and returns missing=[GhostMod, 2.0.0].

    With no prior installation, missing contains the mod name and version;
    installed, different, and extra are all empty.
    """
    clean_repo_at(fake_game_dir)
    list_file = tmp_path / "mods.txt"
    list_file.write_text("GhostMod 2.0.0\n", encoding="utf-8")

    exit_code, lines = invoke_rpc("import_list", {"path": str(fake_game_dir), "list_file": str(list_file)})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["missing"] == [
        {
            "name": "GhostMod",
            "wanted_version": "2.0.0",
            "installed_version": "",
            "link": "",
        }
    ]
    assert data["different"] == []
    assert data["extra"] == []
    assert data["installed"] == []


def test_import_list_with_apply_installs_the_missing_mod_and_reports_progress(
    fake_game_dir: Path, tmp_path: Path, make_archive: MakeArchive
) -> None:
    """import_list(apply=True) emits progress events then installs and reports results.

    All lines are events with the result event containing installed mod details
    including the downloaded version; progress events precede result.
    """
    clean_repo_at(fake_game_dir)
    db_path = tmp_path / "catalog.db"
    archive = make_archive(
        {
            "SPT/user/mods/GhostMod/package.json": json.dumps(
                {"name": "GhostMod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = archive.read_bytes()
    link = "https://dl.example/ghostmod-2.0.0.zip"
    list_file = tmp_path / "mods.txt"
    list_file.write_text("GhostMod 2.0.0\n", encoding="utf-8")

    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        _seed_forge_mod(
            db_path,
            router,
            mod_id=930,
            name="GhostMod",
            version="2.0.0",
            version_id=9300,
            link=link,
            content_length=len(body),
        )
        router.get(link).respond(200, content=body, headers={"Content-Length": str(len(body))})

        exit_code, lines = invoke_rpc(
            "import_list",
            {
                "path": str(fake_game_dir),
                "list_file": str(list_file),
                "apply": True,
                "db_path": str(db_path),
            },
        )

    assert exit_code == 0
    assert_progress_then_result(lines)
    data = lines[-1]["data"]
    assert data["missing"] == [
        {
            "name": "GhostMod",
            "wanted_version": "2.0.0",
            "installed_version": "",
            "link": "",
        }
    ]
    assert len(data["installed"]) == 1
    installed_mod = data["installed"][0]["installed"][0]["mod"]
    assert installed_mod["name"] == "GhostMod"
    assert installed_mod["version"] == "2.0.0"


def test_install_from_archive_reports_a_refused_write_with_its_own_code(
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """install_from_archive answers MOD_FILE_WRITE_FAILED when a file cannot be written.

    The mods folder of the game directory is read-only, so copying the mod stops there.
    The caller must get the mapped error envelope with the plain message, not an
    INTERNAL_ERROR from a raw operating system error.
    """
    request = install_request(fake_game_dir, make_archive)
    blocked = fake_game_dir / "SPT/user/mods"
    blocked.chmod(0o555)

    try:
        exit_code, lines = invoke_rpc("install_from_archive", request)
    finally:
        blocked.chmod(0o755)

    assert exit_code == 1
    data = lines[-1]["data"]
    assert data["code"] == "MOD_FILE_WRITE_FAILED"
    assert data["error"] == (
        "The mod files could not be written: Permission denied. The change "
        "stopped at SPT/user/mods/CoolMod/package.json; your previous files "
        "are one snapshot back."
    )


# --------------------------------------------------------------------------
# SPT version change: apply_spt_release.
# --------------------------------------------------------------------------


def test_apply_spt_release_reports_a_refused_write_with_its_own_code(
    spt_game_dir: Path,
    spt_release_source: SptReleaseSource,
    make_spt_release: MakeSptRelease,
) -> None:
    """apply_spt_release answers SPT_RELEASE_WRITE_FAILED when a file cannot be written.

    The core configuration of the installation is read-only, so writing the
    release stops there. The caller must get the mapped error envelope with
    the plain message, not an INTERNAL_ERROR from a raw operating system
    error.
    """
    spt_release_source.offer(
        make_spt_release(version="4.1.1", client_build="40743"),
        version="4.1.1",
        client_build="40743",
    )
    blocked = spt_game_dir / "SPT_Runtime/SPT_Data/configs/core.json"
    blocked.chmod(0o444)

    try:
        exit_code, lines = invoke_rpc(
            "apply_spt_release", {"path": str(spt_game_dir), "version": "4.1.1"}
        )
    finally:
        blocked.chmod(0o644)

    assert exit_code == 1
    data = lines[-1]["data"]
    assert data["code"] == "SPT_RELEASE_WRITE_FAILED"
    assert data["error"] == (
        "The game files could not be written: Permission denied. The update "
        "stopped at SPT_Runtime/SPT_Data/configs/core.json; your previous "
        "files are one snapshot back."
    )
