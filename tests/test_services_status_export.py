"""Tests for ``sptmod.core.services`` status/export/import and CLI wiring.

Covers ``status()`` grouping (modified profile, modified config, modified
mod file, changed or deleted SPT game file, untracked mod folder/loose
plugin as manual mods), ``export_mods``
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

from conftest import clean_repo_at
from fixture_types import InstallCommit, MakeArchive
from forge_json import mod_json_full, owner_json, page_envelope, version_json
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient
from sptmod.core.modmeta import InvalidMetadataError
from sptmod.core.repo import Repo

pytestmark = pytest.mark.usefixtures("not_running")

runner = CliRunner()


# --- status ------------------------------------------------------------------


def test_status_groups_modified_profile(clean_repo: Repo) -> None:
    """status reports untracked profile changes under modified_profiles, not manual_mods.

    Writing to SPT/user/profiles/abc.json and calling status() yields report.modified_profiles
    containing the path, while report.modified_configs and report.manual_mods remain empty.
    This guards the category partitioning that surfaces profile/config drift separately
    from untracked mod folders for different UX treatment.
    """
    (clean_repo.path / "SPT/user/profiles/abc.json").write_text('{"changed": true}', encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.modified_profiles == [Path("SPT/user/profiles/abc.json")]
    assert not report.modified_configs
    assert not report.manual_mods


def test_status_groups_modified_config(clean_repo: Repo) -> None:
    """status reports tracked BepInEx config drift under modified_configs, not other_modified.

    After seeding and committing BepInEx/config/mod.cfg, modifying it and calling status()
    yields report.modified_configs containing the path, with modified_profiles and manual_mods
    empty. This guards the category partitioning that separates config drift for special CLI
    handling (e.g., profile cleanup preservation).
    """
    config_file = clean_repo.path / "BepInEx/config/mod.cfg"
    config_file.write_text("original", encoding="utf-8")
    clean_repo.commit("chore: seed config", paths=[Path("BepInEx/config/mod.cfg")])
    config_file.write_text("changed", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.modified_configs == [Path("BepInEx/config/mod.cfg")]
    assert not report.modified_profiles


def test_status_reports_the_resolved_spt_version(clean_repo: Repo) -> None:
    """status() fills spt_version from the game directory's core.json.

    clean_repo builds on fake_game_dir, whose core.json carries sptVersion
    "4.1.0"; status() must surface that value, not leave it at the empty
    dataclass default.
    """
    report = services.status(clean_repo.path)

    assert report.spt_version == "4.1.0"


def test_status_spt_version_is_empty_when_no_version_source_resolves(
    fake_game_dir_runtime: Path,
) -> None:
    """status() reports an empty spt_version when the fallback chain is empty.

    fake_game_dir_runtime ships a 4.x-style core.json without a sptVersion
    field and no SPT.Server.exe, and clean_repo_at never stores a version, so
    resolve_spt_version() returns None; status() must surface that as "",
    never as the string "None".
    """
    repo = clean_repo_at(fake_game_dir_runtime)

    report = services.status(repo.path)

    assert report.spt_version == ""


def test_status_puts_a_modified_tracked_mod_file_into_other_modified(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """status reports modified mod files under other_modified, not profiles/configs.

    After installing ModA with BepInEx/plugins/ModA.dll and modifying it, status() reports
    the path in report.other_modified while modified_profiles and modified_configs stay empty.
    This guards the category isolation that separates user-profile drift from mod-file drift.
    """
    install_commit(clean_repo, "ModA", files={"BepInEx/plugins/ModA.dll": "v1"})
    (clean_repo.path / "BepInEx/plugins/ModA.dll").write_text("v2", encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.other_modified == [Path("BepInEx/plugins/ModA.dll")]
    assert not report.modified_profiles
    assert not report.modified_configs


def test_status_groups_untracked_mod_folder_as_a_manual_mod(clean_repo: Repo) -> None:
    """status aggregates untracked files below mod folder prefixes into a single ManualMod entry.

    Creating SPT/user/mods/NewMod with package.json and src.js untracked and calling status()
    yields one entry in report.manual_mods with path=SPT/user/mods/NewMod and files holding both.
    This guards the folder-prefix grouping that presents untracked mod directories as atomic
    adoption targets.
    """
    mod_dir = clean_repo.path / "SPT/user/mods/NewMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "NewMod", "version": "1.0.0"}', encoding="utf-8")
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
    """status reports a loose untracked DLL as its own ManualMod entry, not grouped by prefix.

    Creating BepInEx/plugins/Loose.dll untracked and calling status() yields one entry in
    report.manual_mods with path=BepInEx/plugins/Loose.dll and files=[Loose.dll]. Unlike folder
    grouping, loose plugins are isolated as separate entries for individual adoption.
    """
    (clean_repo.path / "BepInEx/plugins/Loose.dll").write_bytes(b"dll-bytes")

    report = services.status(clean_repo.path)

    assert len(report.manual_mods) == 1
    manual = report.manual_mods[0]
    assert manual.path == Path("BepInEx/plugins/Loose.dll")
    assert manual.files == [Path("BepInEx/plugins/Loose.dll")]


@pytest.mark.parametrize(
    "delete",
    [pytest.param(False, id="modified"), pytest.param(True, id="deleted")],
)
def test_status_reports_a_changed_spt_file_as_a_game_file_change(clean_repo: Repo, delete: bool) -> None:
    """status reports a tracked file no mod owns under game_files_changed, not other_modified.

    BepInEx/plugins/spt/spt-common.dll is committed as part of the game baseline (no Mod
    trailer) and then overwritten or deleted, the way an SPT update rewrites its own client
    files; status() must list it under game_files_changed and leave other_modified and
    manual_mods empty. This guards the classification that keeps an SPT update from being
    reported as files sptmod does not manage.
    """
    spt_dll = clean_repo.path / "BepInEx/plugins/spt/spt-common.dll"
    spt_dll.parent.mkdir(parents=True)
    spt_dll.write_bytes(b"4.1.0-build")
    clean_repo.commit("chore: seed SPT client files", paths=[Path("BepInEx/plugins/spt/spt-common.dll")])
    if delete:
        spt_dll.unlink()
    else:
        spt_dll.write_bytes(b"4.2.0-build")

    report = services.status(clean_repo.path)

    assert report.game_files_changed == [Path("BepInEx/plugins/spt/spt-common.dll")]
    assert not report.other_modified
    assert not report.manual_mods


@pytest.mark.parametrize(
    "delete",
    [pytest.param(False, id="modified"), pytest.param(True, id="deleted")],
)
def test_status_reports_a_changed_file_of_an_active_mod_as_mod_settings(
    clean_repo: Repo, install_commit: InstallCommit, delete: bool
) -> None:
    """status reports a changed file below an active mod's directory under modified_mod_settings.

    ModA is installed with SPT/user/mods/ModA/config/config.json, which is then rewritten or
    deleted the way the mod itself does while the game runs; status() must list it under
    modified_mod_settings and leave other_modified and game_files_changed empty. This guards
    the classification that keeps a mod's own settings from being called unmanaged.
    """
    install_commit(clean_repo, "ModA", files={"SPT/user/mods/ModA/config/config.json": '{"v": 1}'})
    config = clean_repo.path / "SPT/user/mods/ModA/config/config.json"
    if delete:
        config.unlink()
    else:
        config.write_text('{"v": 2}', encoding="utf-8")

    report = services.status(clean_repo.path)

    assert report.modified_mod_settings == [Path("SPT/user/mods/ModA/config/config.json")]
    assert not report.other_modified
    assert not report.game_files_changed


# --- export ------------------------------------------------------------------


def test_export_text_lists_name_version_ordered_by_name(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """export_mods with format='text' outputs lines of 'Name Version', sorted case-sensitively.

    After installing Zeta 1.0.0 (minute=1) then Alpha 2.0.0 (minute=2) and calling
    export_mods(format='text'), output.strip().split('\\n') == ['Alpha 2.0.0', 'Zeta 1.0.0'].
    This guards both the text format (space-separated) and alphabetical sort that enable
    diffable list exports.
    """
    install_commit(clean_repo, "Zeta", version="1.0.0", mod_type="client", minute=1)
    install_commit(clean_repo, "Alpha", version="2.0.0", mod_type="client", minute=2)

    output = services.export_mods(clean_repo.path, export_format="text", db_path=tmp_path / "catalog.db")

    lines = output.strip("\n").split("\n")
    assert lines == ["Alpha 2.0.0", "Zeta 1.0.0"]


def test_export_json_structure(clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path) -> None:
    """Export JSON format wraps mods in object with mods array."""
    install_commit(clean_repo, "Alpha", version="2.0.0", mod_type="server")

    output = services.export_mods(clean_repo.path, export_format="json", db_path=tmp_path / "catalog.db")

    data = json.loads(output)
    assert data == {"mods": [{"name": "Alpha", "version": "2.0.0", "type": "server", "link": ""}]}


def test_export_client_only_keeps_client_and_hybrid_mods(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """export_mods with client_only=True excludes server-only mods from the output.

    After installing ServerOnly, ClientOnly, and HybridMod, calling export_mods(client_only=True)
    returns output containing only ClientOnly and HybridMod names. This guards the type=server
    filter that supports client-side export scenarios (e.g., multiplayer imports).
    """
    install_commit(clean_repo, "ServerOnly", mod_type="server", minute=1)
    install_commit(clean_repo, "ClientOnly", mod_type="client", minute=2)
    install_commit(clean_repo, "HybridMod", mod_type="hybrid", minute=3)

    output = services.export_mods(
        clean_repo.path,
        export_format="text",
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
    """export_mods appends the Forge mod detail URL when the installed mod is in the catalog.

    After installing Alpha 2.0.0, seeding the catalog with that name and id=42, and calling
    export_mods(format='text'), output includes the detail_url 'https://forge.sp-tarkov.com/mods/alpha'
    appended to the name/version line. This enables clickable links in text-format exports.
    """
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

    output = services.export_mods(clean_repo.path, export_format="text", db_path=db_path)

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

    output = services.export_mods(clean_repo.path, export_format="text", db_path=db_path)

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

    output = services.export_mods(clean_repo.path, export_format="text", db_path=db_path)

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

    assert [(entry.name, entry.wanted_version, entry.installed_version) for entry in diff.missing] == [
        ("ModC", "3.0.0", "")
    ]
    assert [(entry.name, entry.wanted_version, entry.installed_version) for entry in diff.different] == [
        ("ModB", "1.5.0", "2.0.0")
    ]
    assert [(entry.name, entry.wanted_version, entry.installed_version) for entry in diff.extra] == [
        ("ModD", "", "4.0.0")
    ]


def test_import_matches_names_case_insensitively(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """import_list matches mod names case-insensitively, yielding an empty diff when versions match.

    After installing ModA 1.0.0 and importing content='moda 1.0.0\\n', import_list() returns
    diff == ImportDiff() (no missing/different/extra). This guards the case-folding that lets
    lists and installs use any casing independently.
    """
    install_commit(clean_repo, "ModA", version="1.0.0")
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("moda 1.0.0\n", encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


def test_import_json_format_is_recognised_by_content_not_extension(
    clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path
) -> None:
    """import_list detects JSON format by JSON structure, not by file extension.

    After installing ModA 1.0.0 and importing a file named 'wanted.txt' (text extension)
    containing {'mods': [{'name': 'ModA', 'version': '1.0.0', ...}]} (JSON content), import_list()
    recognizes the JSON structure and returns diff == ImportDiff(). This guards the format detection
    that ignores unreliable file names.
    """
    install_commit(clean_repo, "ModA", version="1.0.0")
    list_file = tmp_path / "wanted.txt"  # ".txt" extension, JSON content
    payload = {"mods": [{"name": "ModA", "version": "1.0.0", "type": "client", "link": ""}]}
    list_file.write_text(json.dumps(payload), encoding="utf-8")

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


def test_import_raises_invalid_metadata_for_a_missing_list_file(clean_repo: Repo, tmp_path: Path) -> None:
    """import_list raises InvalidMetadataError when the given file path does not exist.

    Calling import_list(path, tmp_path / 'does-not-exist.txt') raises InvalidMetadataError
    before any processing. This guards the input validation that rejects missing sources.
    """
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, tmp_path / "does-not-exist.txt")


def test_import_raises_invalid_metadata_when_neither_source_is_given(
    clean_repo: Repo,
) -> None:
    """import_list raises InvalidMetadataError when both list_file and content are None.

    Calling import_list(path) with no arguments raises InvalidMetadataError. This guards
    the signature requirement that at least one source (file or content) must be provided.
    """
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path)


def test_import_raises_invalid_metadata_when_both_sources_are_given(clean_repo: Repo, tmp_path: Path) -> None:
    """import_list raises InvalidMetadataError when both list_file and content are provided.

    Calling import_list(path, list_file, content='...') with both arguments raises
    InvalidMetadataError. This guards the mutual exclusion that prevents ambiguous sources.
    """
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("ModA 1.0.0\n", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, list_file, content="ModA 1.0.0\n")


def test_import_raises_invalid_metadata_for_malformed_json_content(
    clean_repo: Repo,
) -> None:
    """import_list raises InvalidMetadataError when content is malformed JSON.

    Calling import_list(path, content='{not valid json') raises InvalidMetadataError before
    any further parsing. This guards the JSON syntax validation.
    """
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
    """import_list raises InvalidMetadataError when a JSON mods array entry is not an object.

    Calling import_list(path, content=json.dumps({'mods': ['just-a-string']})) raises
    InvalidMetadataError. This guards the mod entry type validation.
    """
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, content=json.dumps({"mods": ["just-a-string"]}))


def test_import_raises_invalid_metadata_for_a_mod_entry_missing_name_or_version(
    clean_repo: Repo,
) -> None:
    """import_list raises InvalidMetadataError when a JSON mod entry is missing name or version.

    Calling import_list(path, content=json.dumps({'mods': [{'name': 'ModA'}]})) (no version)
    raises InvalidMetadataError. This guards the required field validation.
    """
    with pytest.raises(InvalidMetadataError):
        services.import_list(clean_repo.path, content=json.dumps({"mods": [{"name": "ModA"}]}))


def test_import_text_format_skips_blank_lines(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """import_list skips empty lines in text format, yielding empty diff when content matches.

    After installing ModA 1.0.0 and calling import_list(content='\\n\\nModA 1.0.0\\n\\n'),
    returns diff == ImportDiff() (blank lines ignored). This enables readable text lists.
    """
    install_commit(clean_repo, "ModA", version="1.0.0")

    diff = services.import_list(clean_repo.path, content="\n\nModA 1.0.0\n\n")

    assert diff == services.ImportDiff()


def test_import_raises_invalid_metadata_for_a_text_line_with_no_version(
    clean_repo: Repo,
) -> None:
    """import_list raises InvalidMetadataError when text format line lacks a version field.

    Calling import_list(content='JustAName\\n') raises InvalidMetadataError that names
    the offending line ('JustAName' in str(exc_info.value)). This guards the text-format
    structure (two fields: name + version).
    """
    with pytest.raises(InvalidMetadataError) as exc_info:
        services.import_list(clean_repo.path, content="JustAName\n")

    assert "JustAName" in str(exc_info.value)


def test_export_import_text_roundtrip(clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path) -> None:
    """export_mods and import_list round-trip through text format without data loss.

    After installing ModA 1.0.0 (mod_type=client), exporting to text format and re-importing
    yields diff == ImportDiff() (no missing/different/extra). This verifies that the text
    encoding (name + space + version) preserves installed state through export and re-import.
    """
    install_commit(clean_repo, "ModA", version="1.0.0", mod_type="client")
    list_file = tmp_path / "wanted.txt"
    list_file.write_text(
        services.export_mods(clean_repo.path, export_format="text", db_path=tmp_path / "catalog.db"),
        encoding="utf-8",
    )

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


def test_export_import_json_roundtrip(clean_repo: Repo, install_commit: InstallCommit, tmp_path: Path) -> None:
    """export_mods and import_list round-trip through JSON format without data loss.

    After installing ModA 1.0.0 (mod_type=client), exporting to JSON format and re-importing
    yields diff == ImportDiff() (no missing/different/extra). This verifies that the JSON
    structure (mods array with name/version/type/link) preserves installed state through
    export and re-import.
    """
    install_commit(clean_repo, "ModA", version="1.0.0", mod_type="client")
    list_file = tmp_path / "wanted.json"
    list_file.write_text(
        services.export_mods(clean_repo.path, export_format="json", db_path=tmp_path / "catalog.db"),
        encoding="utf-8",
    )

    diff = services.import_list(clean_repo.path, list_file)

    assert diff == services.ImportDiff()


# --- CLI -----------------------------------------------------------------


def test_cli_status_shows_adopt_hint_for_a_manual_mod(fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 'sptmod status' output mentions 'adopt' and the untracked mod path.

    After creating SPT/user/mods/NewMod with package.json and running 'sptmod status' from
    the game dir, output.exit_code == 0 and output.stdout contains 'adopt' and
    'SPT/user/mods/NewMod'. This guides users toward adoption of untracked mods.
    """
    clean_repo_at(fake_game_dir)
    mod_dir = fake_game_dir / "SPT/user/mods/NewMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text('{"name": "NewMod", "version": "1.0.0"}', encoding="utf-8")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "adopt" in result.stdout
    assert "SPT/user/mods/NewMod" in result.stdout


def test_cli_status_reports_changed_mod_settings_of_an_active_mod(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """CLI 'sptmod status' summarises changed files of an active mod as changed mod settings.

    After installing ClientMod, rewriting its BepInEx/plugins/ClientMod/config.json and
    running 'sptmod status' from the game dir, result.exit_code == 0 and the output names the
    file count under changed mod settings, without the adopt hint. This keeps the CLI from
    presenting a mod's own settings as unmanaged files.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(
        repo,
        "ClientMod",
        files={
            "BepInEx/plugins/ClientMod/ClientMod.dll": "v1",
            "BepInEx/plugins/ClientMod/config.json": '{"v": 1}',
        },
    )
    (fake_game_dir / "BepInEx/plugins/ClientMod/config.json").write_text('{"v": 2}', encoding="utf-8")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Mod settings changed since the last snapshot (1 files)." in result.stdout
    assert "adopt" not in result.stdout


def test_cli_status_reports_changed_game_files_as_a_probable_spt_update(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 'sptmod status' summarises changed game files as a probable SPT update.

    After committing BepInEx/plugins/spt/spt-common.dll as part of the baseline, overwriting
    it and running 'sptmod status' from the game dir, result.exit_code == 0 and the output
    names the file count and 'probably an SPT update', without the adopt hint. This keeps the
    CLI from presenting an SPT update as unmanaged files.
    """
    repo = clean_repo_at(fake_game_dir)
    spt_dll = fake_game_dir / "BepInEx/plugins/spt/spt-common.dll"
    spt_dll.parent.mkdir(parents=True)
    spt_dll.write_bytes(b"4.1.0-build")
    repo.commit("chore: seed SPT client files", paths=[Path("BepInEx/plugins/spt/spt-common.dll")])
    spt_dll.write_bytes(b"4.2.0-build")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "(1 files), probably an SPT update." in result.stdout
    assert "adopt" not in result.stdout


def test_cli_export_json_happy_path(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """CLI 'sptmod export --format json' outputs installed mods as JSON to stdout.

    After installing CoolMod and running 'sptmod export --format json' from the game dir,
    result.exit_code == 0 and 'CoolMod' appears in result.stdout. This enables JSON export
    for scripting.
    """
    repo = clean_repo_at(fake_game_dir)
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
    """CLI 'sptmod import <file> --apply' installs missing mods from list by resolving Forge links.

    Given a list file containing 'Import Target 3.0.0' (missing), with the Forge mocked to return
    that mod version with a download link, running 'sptmod import <file> --apply' from the game dir
    yields result.exit_code == 0 and stdout containing 'installed Import Target 3.0.0'. This
    enables one-command list restoration.
    """
    clean_repo_at(fake_game_dir)
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
        respx_router.get("/mod/701/versions").mock(return_value=httpx.Response(200, json=versions_page))
        cat.fetch_versions(seed_client, 701)

    def _echo_dependencies(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    respx_router.get("/mods/dependencies").mock(side_effect=_echo_dependencies)
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    list_file = tmp_path / "wanted.txt"
    list_file.write_text("Import Target 3.0.0\n", encoding="utf-8")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["import", str(list_file), "--apply"])

    assert result.exit_code == 0
    assert "installed Import Target 3.0.0" in result.stdout
