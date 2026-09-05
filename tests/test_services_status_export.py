"""Tests for ``sptmod.core.services`` status/export/import and CLI wiring.

Covers ``status()`` grouping (modified profile, modified config, modified
mod file, untracked mod folder/loose plugin as manual mods), ``export_mods``
(text/json rendering, ``--client-only`` filtering, catalog link lookup) and
``import_list`` (diff against a deviating install state, format detection by
content; the ``--apply`` Forge install path itself is covered in
``test_services_forge_install.py``). CLI cases exercise the same paths
through Typer's ``CliRunner``.
"""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from fixture_types import InstallCommit, MakeArchive
from sptmod.cli import app
from sptmod.core import game_dir, services
from sptmod.core.forge.catalog import DB_FILE_NAME, Catalog
from sptmod.core.forge.client import ForgeClient
from sptmod.core.game_dir import detect_layout
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo
from test_forge_client_fixtures import (
    client_factory,
    fake_clock,
    mod_json_full,
    owner_json,
    page_envelope,
    recording_sleep,
    respx_router,
    version_json,
)

__all__ = ["client_factory", "fake_clock", "recording_sleep", "respx_router"]

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub game-running checks to report no process, allowing operations.

    All tests status/export/import without mocking this, so the stubs work in
    every test without repeating the setup.
    """
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


def _clean_repo(path: Path) -> Repo:
    """Initialize a repository with an empty baseline commit.

    Used by CLI tests that do not import a clean_repo fixture from conftest,
    so they must create their own.
    """
    repo = Repo.init(detect_layout(path))
    repo.commit(
        "chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None
    )
    return repo


@pytest.fixture
def isolated_default_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``default_db_path()`` at an isolated location for CLI tests.

    Mirrors the fixture of the same name in ``test_services_catalog.py``: the
    CLI never passes ``db_path`` explicitly, so it resolves through
    ``forge.catalog.default_db_path()``, which must not touch the developer's
    real config directory.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        "sptmod.core.forge.catalog.platformdirs.user_config_path",
        lambda *args, **kwargs: config_dir,
    )
    return config_dir / DB_FILE_NAME


# --- status ------------------------------------------------------------------


def test_status_groups_modified_profile(clean_repo: Repo) -> None:
    """Status reports modified profile files separately."""
    (clean_repo.path / "SPT/user/profiles/abc.json").write_text(
        '{"changed": true}', encoding="utf-8"
    )

    report = services.status(clean_repo.path)

    assert report.modified_profiles == [Path("SPT/user/profiles/abc.json")]
    assert report.modified_configs == []
    assert report.manual_mods == []


def test_status_groups_modified_config(clean_repo: Repo) -> None:
    """Status reports modified BepInEx config files separately."""
    config_file = clean_repo.path / "BepInEx/config/mod.cfg"
    config_file.write_text("original", encoding="utf-8")
    clean_repo.commit("chore: seed config", paths=[Path("BepInEx/config/mod.cfg")])
    config_file.write_text("changed", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.modified_configs == [Path("BepInEx/config/mod.cfg")]
    assert report.modified_profiles == []


def test_status_puts_a_modified_tracked_mod_file_into_other_modified(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Status reports modified mod files under other_modified, not profiles/configs."""
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "v1"})
    (clean_repo.path / "BepInEx/plugins/ModA.dll").write_text("v2", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.other_modified == [Path("BepInEx/plugins/ModA.dll")]
    assert report.modified_profiles == []
    assert report.modified_configs == []


def test_status_groups_untracked_mod_folder_as_a_manual_mod(clean_repo: Repo) -> None:
    """Status groups untracked files below mod folders as manual mods."""
    mod_dir = clean_repo.path / "SPT/user/mods/NewMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        '{"name": "NewMod", "version": "1.0.0"}', encoding="utf-8"
    )
    (mod_dir / "src.js").write_text("code", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert len(report.manual_mods) == 1
    manual = report.manual_mods[0]
    assert manual.path == Path("SPT/user/mods/NewMod")
    assert set(manual.files) == {
        Path("SPT/user/mods/NewMod/package.json"),
        Path("SPT/user/mods/NewMod/src.js"),
    }


def test_status_groups_a_loose_untracked_dll_as_a_manual_mod_by_itself(
    clean_repo: Repo,
) -> None:
    """Status reports a loose untracked DLL as its own manual mod entry."""
    (clean_repo.path / "BepInEx/plugins/Loose.dll").write_bytes(b"dll-bytes")

    report = services.status(clean_repo.path)

    assert len(report.manual_mods) == 1
    manual = report.manual_mods[0]
    assert manual.path == Path("BepInEx/plugins/Loose.dll")
    assert manual.files == [Path("BepInEx/plugins/Loose.dll")]


# --- export ------------------------------------------------------------------


def test_export_text_lists_name_version_ordered_by_name(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Export text format lists installed mods by name and version, alphabetically."""
    install_commit(clean_repo, "Zeta", version="1.0.0", mod_type="client", minute=1)
    install_commit(clean_repo, "Alpha", version="2.0.0", mod_type="client", minute=2)

    output = services.export_mods(
        clean_repo.path, format="text", db_path=tmp_path / "catalog.db"
    )

    lines = output.strip("\n").split("\n")
    assert lines == ["Alpha 2.0.0", "Zeta 1.0.0"]


def test_export_json_structure(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Export JSON format wraps mods in object with mods array."""
    install_commit(clean_repo, "Alpha", version="2.0.0", mod_type="server")

    output = services.export_mods(
        clean_repo.path, format="json", db_path=tmp_path / "catalog.db"
    )

    data = json.loads(output)
    assert data == {
        "mods": [{"name": "Alpha", "version": "2.0.0", "type": "server", "link": ""}]
    }


def test_export_client_only_keeps_client_and_hybrid_mods(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Export with client_only=True filters out server-only mods."""
    install_commit(clean_repo, "ServerOnly", mod_type="server", minute=1)
    install_commit(clean_repo, "ClientOnly", mod_type="client", minute=2)
    install_commit(clean_repo, "HybridMod", mod_type="hybrid", minute=3)

    output = services.export_mods(
        clean_repo.path,
        format="text",
        client_only=True,
        db_path=tmp_path / "catalog.db",
    )

    names = {line.split()[0] for line in output.strip("\n").split("\n") if line}
    assert names == {"ClientOnly", "HybridMod"}


def _seed_catalog_mod(
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
    *,
    name: str,
    mod_id: int,
    detail_url: str,
) -> None:
    """Seed the catalog cache with a single mocked Forge mod entry.

    Used by export link tests to ensure a catalog lookup finds matching mods.
    """
    mod = mod_json_full(
        id=mod_id,
        name=name,
        owner=owner_json(name="Someone"),
        detail_url=detail_url,
        versions=[],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    with Catalog(db_path) as catalog:
        catalog.sync(client, full=True)


def test_export_includes_the_catalog_link_when_the_db_has_a_match(
    clean_repo: Repo,
    install_commit: InstallCommit,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    tmp_path: Path,
) -> None:
    """Export looks up catalog links by mod name and includes them in text output."""
    install_commit(clean_repo, "Alpha", version="2.0.0", mod_type="client")
    db_path = tmp_path / "catalog.db"
    _seed_catalog_mod(
        client_factory,
        respx_router,
        db_path,
        name="Alpha",
        mod_id=42,
        detail_url="https://forge.sp-tarkov.com/mods/alpha",
    )

    output = services.export_mods(clean_repo.path, format="text", db_path=db_path)

    assert output.strip("\n") == "Alpha 2.0.0 https://forge.sp-tarkov.com/mods/alpha"


def test_export_skips_a_cached_catalog_mod_that_is_not_installed(
    clean_repo: Repo,
    install_commit: InstallCommit,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    tmp_path: Path,
) -> None:
    """catalog.classify returns one entry per *cached* mod, not per installed
    one; a cached mod the game directory never installed has
    installed_version=None and must be skipped when building the link map.
    If the `if entry.installed_version is None: continue` guard in
    _catalog_links were removed, this fails: KeyError matching the wrong
    installed mod by name, or a spurious link for the uninstalled mod."""
    install_commit(clean_repo, "Alpha", version="2.0.0", mod_type="client")
    db_path = tmp_path / "catalog.db"
    # Both cached mods must be seeded by a single sync page: a second, later
    # full sync would prune whichever mod its page does not return.
    page = page_envelope(
        [
            mod_json_full(
                id=42,
                name="Alpha",
                owner=owner_json(name="Someone"),
                detail_url="https://forge.sp-tarkov.com/mods/alpha",
                versions=[],
            ),
            mod_json_full(
                id=99,
                name="NeverInstalled",
                owner=owner_json(name="Someone"),
                detail_url="https://forge.sp-tarkov.com/mods/never-installed",
                versions=[],
            ),
        ],
        current_page=1,
        last_page=1,
        total=2,
        next_url=None,
    )
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    client = client_factory()
    with Catalog(db_path) as catalog:
        catalog.sync(client, full=True)

    output = services.export_mods(clean_repo.path, format="text", db_path=db_path)

    assert output.strip("\n") == "Alpha 2.0.0 https://forge.sp-tarkov.com/mods/alpha"


def test_export_degrades_to_a_linkless_list_when_the_catalog_is_unavailable(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """The catalog-link lookup opens the cache DB whose parent directory it
    creates on the fly (``Catalog.__init__``); when that directory cannot be
    created (e.g. a file sits where the directory should be, mirroring a
    permission failure in the real config dir), ``export_mods`` must degrade
    to the same mod list without links rather than raising -- unlike
    ``sync_catalog``/``search_catalog``/``browse_catalog``, which still raise
    ``CatalogUnavailableError`` (pinned in ``test_api_errors.py``), export has
    a link-free fallback and must keep working.
    """
    install_commit(clean_repo, "Alpha", version="1.0.0")
    blocked = tmp_path / "blocked-config-dir"
    blocked.write_text("not a directory", encoding="utf-8")
    db_path = blocked / "catalog.db"

    output = services.export_mods(clean_repo.path, format="text", db_path=db_path)

    assert output.strip("\n") == "Alpha 1.0.0"


# --- import ------------------------------------------------------------------


def test_import_diff_reports_missing_different_and_extra(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Import compares a list against installed mods, categorizing them as
    missing (in list only), different (mismatched versions), or extra (installed only).
    """
    install_commit(clean_repo, "ModA", version="1.0.0", minute=1)
    install_commit(clean_repo, "ModB", version="2.0.0", minute=2)
    install_commit(clean_repo, "ModD", version="4.0.0", minute=3)
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("ModA 1.0.0\nModC 3.0.0\nModB 1.5.0\n", encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file)

    assert [
        (entry.name, entry.wanted_version, entry.installed_version)
        for entry in diff.missing
    ] == [("ModC", "3.0.0", "")]
    assert [
        (entry.name, entry.wanted_version, entry.installed_version)
        for entry in diff.different
    ] == [("ModB", "1.5.0", "2.0.0")]
    assert [
        (entry.name, entry.wanted_version, entry.installed_version)
        for entry in diff.extra
    ] == [("ModD", "", "4.0.0")]


def test_import_matches_names_case_insensitively(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Import matches mod names case-insensitively."""
    install_commit(clean_repo, "ModA", version="1.0.0")
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("moda 1.0.0\n", encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


def test_import_json_format_is_recognised_by_content_not_extension(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Import detects JSON format by content, not file extension."""
    install_commit(clean_repo, "ModA", version="1.0.0")
    list_file = tmp_path / "wanted.txt"  # ".txt" extension, JSON content
    payload = {
        "mods": [{"name": "ModA", "version": "1.0.0", "type": "client", "link": ""}]
    }
    list_file.write_text(json.dumps(payload), encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


def test_import_raises_invalid_metadata_for_a_missing_list_file(
    clean_repo: Repo, tmp_path: Path
) -> None:
    """Import raises InvalidMetadataError when list file does not exist."""
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, tmp_path / "does-not-exist.txt")


def test_import_raises_invalid_metadata_when_neither_source_is_given(
    clean_repo: Repo,
) -> None:
    """Import raises InvalidMetadataError when neither file nor content is given."""
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path)


def test_import_raises_invalid_metadata_when_both_sources_are_given(
    clean_repo: Repo, tmp_path: Path
) -> None:
    """Import raises InvalidMetadataError when both file and content are given."""
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("ModA 1.0.0\n", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, list_file, content="ModA 1.0.0\n")


def test_import_raises_invalid_metadata_for_malformed_json_content(
    clean_repo: Repo,
) -> None:
    """Import raises InvalidMetadataError for malformed JSON content."""
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, content="{not valid json")


def test_import_raises_invalid_metadata_when_json_has_no_mods_list(
    clean_repo: Repo,
) -> None:
    """Import raises InvalidMetadataError when JSON mods field is not an array."""
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, content=json.dumps({"mods": "nope"}))


def test_import_raises_invalid_metadata_for_a_non_object_mod_entry(
    clean_repo: Repo,
) -> None:
    """Import raises InvalidMetadataError when a mod entry is not an object."""
    with pytest.raises(InvalidMetadataError):
        services.import_list(
            clean_repo.path, content=json.dumps({"mods": ["just-a-string"]})
        )


def test_import_raises_invalid_metadata_for_a_mod_entry_missing_name_or_version(
    clean_repo: Repo,
) -> None:
    """Import raises InvalidMetadataError when mod entry lacks name or version."""
    with pytest.raises(InvalidMetadataError):
        services.import_list(
            clean_repo.path, content=json.dumps({"mods": [{"name": "ModA"}]})
        )


def test_import_text_format_skips_blank_lines(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """Import text format ignores blank lines in the content."""
    install_commit(clean_repo, "ModA", version="1.0.0")

    diff = services.import_list(
        clean_repo.path, content="\n\nModA 1.0.0\n\n"
    )

    assert diff == services.ImportDiff()


def test_import_raises_invalid_metadata_for_a_text_line_with_no_version(
    clean_repo: Repo,
) -> None:
    """Import text format raises InvalidMetadataError when line has no version."""
    with pytest.raises(InvalidMetadataError) as exc_info:
        services.import_list(clean_repo.path, content="JustAName\n")

    assert "JustAName" in str(exc_info.value)


def test_export_import_text_roundtrip(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Exporting to text and re-importing produces no diff, verifying format integrity."""
    install_commit(clean_repo, "ModA", version="1.0.0", mod_type="client")
    list_file = tmp_path / "wanted.txt"
    list_file.write_text(
        services.export_mods(
            clean_repo.path, format="text", db_path=tmp_path / "catalog.db"
        ),
        encoding="utf-8",
    )

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


def test_export_import_json_roundtrip(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """Exporting to JSON and re-importing produces no diff, verifying format integrity."""
    install_commit(clean_repo, "ModA", version="1.0.0", mod_type="client")
    list_file = tmp_path / "wanted.json"
    list_file.write_text(
        services.export_mods(
            clean_repo.path, format="json", db_path=tmp_path / "catalog.db"
        ),
        encoding="utf-8",
    )

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


# --- CLI -----------------------------------------------------------------


def test_cli_status_shows_adopt_hint_for_a_manual_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI status shows untracked mods with an adopt hint."""
    _clean_repo(fake_game_dir)
    mod_dir = fake_game_dir / "SPT/user/mods/NewMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        '{"name": "NewMod", "version": "1.0.0"}', encoding="utf-8"
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "adopt" in result.stdout
    assert "SPT/user/mods/NewMod" in result.stdout


def test_cli_export_json_happy_path(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """CLI export with --format json outputs installed mods as JSON."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["export", "--format", "json"])

    assert result.exit_code == 0
    assert "CoolMod" in result.stdout


def test_cli_import_apply_installs_the_missing_mod_via_the_mocked_forge_path(
    fake_game_dir: Path,
    isolated_default_db_path: Path,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    make_archive: MakeArchive,
    tmp_path: Path,
) -> None:
    """CLI import --apply installs missing mods from list via the Forge."""
    _clean_repo(fake_game_dir)
    archive = make_archive(
        {
            "SPT/user/mods/Import Target/package.json": json.dumps(
                {"name": "Import Target", "version": "3.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = archive.read_bytes()
    link = "https://dl.example/import-target-3.0.0.zip"
    mod = mod_json_full(
        id=701,
        name="Import Target",
        slug="import-target",
        owner=owner_json(id=7010, name="Alice"),
        versions=[
            {
                "id": 7010,
                "version": "3.0.0",
                "spt_version_constraint": "^4.0.0",
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], current_page=1, last_page=1, total=1, next_url=None)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))
    seed_client = client_factory()
    with Catalog(isolated_default_db_path) as cat:
        cat.sync(seed_client, full=True)
        versions_page = page_envelope(
            [
                version_json(
                    id=7010,
                    version="3.0.0",
                    link=link,
                    content_length=len(body),
                    spt_version_constraint="^4.0.0",
                )
            ],
            current_page=1,
            last_page=1,
            total=1,
            next_url=None,
        )
        respx_router.get("/mod/701/versions").mock(
            return_value=httpx.Response(200, json=versions_page)
        )
        cat.fetch_versions(seed_client, 701)

    def _echo_dependencies(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    respx_router.get("/mods/dependencies").mock(side_effect=_echo_dependencies)
    respx_router.get(link).mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Length": str(len(body))}
        )
    )
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("Import Target 3.0.0\n", encoding="utf-8")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["import", str(list_file), "--apply"])

    assert result.exit_code == 0
    assert "installed Import Target 3.0.0" in result.stdout
