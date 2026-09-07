"""In-process tests for ``sptmod rpc <name>`` (typer ``CliRunner``): the NDJSON
envelope contract on stdout, progress/result/error line ordering, operation ID
echoing, wrapped string results, the error → ``{code, details}`` mapping, and
the request-error exit path. Runs against the real service functions except
for the error-mapping cases, where the dispatched function is swapped for a
small stub raising a specific core exception -- the cheapest way to reach
every ``error_details`` branch without reproducing each error's real
precondition.
"""

import dataclasses
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from conftest import clean_repo_at, invoke_rpc
from fixture_types import InstallCommit, JsonDict, MakeArchive, TrailerInstallCommit
from forge_json import mod_json_full, mod_json_minimal, owner_json, page_envelope, version_json
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import (
    AmbiguousModError,
    ArchiveError,
    ConflictError,
    DependencyError,
    ForgeApiError,
    GitError,
    InvalidPathError,
    PartialInstallError,
    ProfileResidueError,
    UnresolvableDependencyError,
)
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import DEFAULT_BASE_URL
from sptmod.core.models import (
    Conflict,
    ModInfo,
    ModState,
    ModType,
    ProfileResidue,
    ResidueCategory,
)
from sptmod.core.repo import Repo
from sptmod.core.workspace import download_cache_dir
from sptmod.server import rpc

pytestmark = pytest.mark.usefixtures("not_running")

SPT_CONSTRAINT = "^4.0.0"


def _mod_info(name: str) -> ModInfo:
    """A fully populated ``ModInfo`` for the named mod."""
    return ModInfo(
        name=name,
        version="1.0.0",
        mod_type=ModType.CLIENT,
        author="tester",
        spt_range="~4.1.0",
        state=ModState.INSTALLED,
        commit="",
        date=datetime(2024, 1, 1, tzinfo=UTC),
        forge_id="",
        forge_version_id="",
        guid="",
        source="",
    )


runner = CliRunner()


@pytest.fixture
def inject_error(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, Exception], None]:
    """Swap a spec's function for one raising ``error`` for every call.

    The stub only needs to accept the request's own fields as keyword
    arguments; ``status`` (single field ``path``) is used for every case here,
    so its stub always takes just ``path``.
    """

    def _inject(name: str, error: Exception) -> None:
        original = rpc.RPC_FUNCTIONS[name]

        def _raise(**_kwargs: object) -> object:
            raise error

        monkeypatch.setitem(rpc.RPC_FUNCTIONS, name, dataclasses.replace(original, function=_raise))

    return _inject


# --------------------------------------------------------------------------
# Envelope shape, line ordering, well-formedness.
# --------------------------------------------------------------------------


def test_success_envelope_carries_event_result_and_the_service_return_value(
    tmp_path: Path,
) -> None:
    """The RPC response envelope contains 'event' and 'data' fields with the
    service function's result."""
    (tmp_path / "Sub").mkdir()

    exit_code, lines = invoke_rpc("list_directories", {"path": str(tmp_path)})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": {
                "path": str(tmp_path.resolve()),
                "parent": str(tmp_path.resolve().parent),
                "entries": [
                    {
                        "name": "Sub",
                        "path": str((tmp_path / "Sub").resolve()),
                        "is_game_dir": False,
                    }
                ],
            },
        }
    ]


def _install_request(fake_game_dir: Path, make_archive: MakeArchive, **overrides: Any) -> dict[str, Any]:
    """Return a dictionary that ``sptmod rpc install_from_archive`` expects on stdin.

    Creates a zip archive containing SPT/user/mods/CoolMod/package.json with
    minimal mod metadata and returns request={path, archive} plus any caller
    overrides (used in operation_id tests to pass a specific caller ID).
    """
    clean_repo_at(fake_game_dir)
    archive = make_archive(
        {"SPT/user/mods/CoolMod/package.json": json.dumps({"name": "CoolMod", "version": "1.2.3", "author": "Acme"})},
        "zip",
    )
    request = {"path": str(fake_game_dir), "archive": str(archive)}
    request.update(overrides)
    return request


def test_progress_lines_precede_the_result_line_and_every_line_is_valid_json(
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """install_from_archive emits 8 progress events followed by one result event.

    All lines are valid JSON with event field; INSTALL_STEPS enumerate exactly
    8 install steps, and the final line has event=result instead of progress.
    """
    request = _install_request(fake_game_dir, make_archive)

    result = runner.invoke(app, ["rpc", "install_from_archive"], input=json.dumps(request))
    raw_lines = [line for line in result.stdout.splitlines() if line]

    assert result.exit_code == 0
    parsed = [json.loads(line) for line in raw_lines]
    events = [doc["event"] for doc in parsed]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
    assert len(events) == 9  # 8 install steps + 1 result, per INSTALL_STEPS


def test_operation_id_supplied_by_the_caller_is_echoed_on_every_line(
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """When operation_id=\"caller-op\" is supplied, every progress line echoes it.

    The result envelope wraps operation_id in data, while progress lines have it
    as a top-level field.
    """
    request = _install_request(fake_game_dir, make_archive, operation_id="caller-op")

    exit_code, lines = invoke_rpc("install_from_archive", request)

    assert exit_code == 0
    assert all(doc["operation_id"] == "caller-op" for doc in lines[:-1])
    assert lines[-1]["data"]["operation_id"] == "caller-op"


def test_operation_id_is_generated_as_32_hex_chars_when_absent_and_identical_everywhere(
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """When operation_id is omitted, main() generates 32 hex chars via uuid4().hex.

    The generated ID appears on all progress lines and in the result envelope
    data field, consistent throughout the response.
    """
    request = _install_request(fake_game_dir, make_archive)

    exit_code, lines = invoke_rpc("install_from_archive", request)

    assert exit_code == 0
    generated = lines[0]["operation_id"]
    assert len(generated) == 32
    assert all(char in "0123456789abcdef" for char in generated)
    assert all(doc["operation_id"] == generated for doc in lines[:-1])
    assert lines[-1]["data"]["operation_id"] == generated


# --------------------------------------------------------------------------
# Wrapped plain-string results.
# --------------------------------------------------------------------------


def test_set_spt_version_wraps_its_plain_string_result_under_spt_version(
    fake_game_dir: Path,
) -> None:
    """set_spt_version returns plain string \"4.2.0\"; RPC wraps it as data.spt_version.

    The RpcSpec for set_spt_version declares wrap=\"spt_version\", so the
    core service's string result is put under that field in the JSON response.
    """
    repo = clean_repo_at(fake_game_dir)

    exit_code, lines = invoke_rpc("set_spt_version", {"path": str(repo.path), "version": "4.2.0"})

    assert exit_code == 0
    assert lines == [{"event": "result", "data": {"spt_version": "4.2.0"}}]


def test_export_mods_wraps_its_plain_string_result_under_content(fake_game_dir: Path, tmp_path: Path) -> None:
    """export_mods returns plain string \"\"; RPC wraps it as data.content.

    The RpcSpec for export_mods declares wrap=\"content\", so the service's
    string result becomes data.content=\"\" in the JSON response.
    """
    repo = clean_repo_at(fake_game_dir)
    db_path = tmp_path / "unused-catalog.db"

    exit_code, lines = invoke_rpc("export_mods", {"path": str(repo.path), "db_path": str(db_path)})

    assert exit_code == 0
    assert lines == [{"event": "result", "data": {"content": ""}}]


def test_list_favorites_wraps_its_list_result_under_mod_ids(tmp_path: Path) -> None:
    """A list result is wrapped under the 'mod_ids' field."""
    favorites_path = tmp_path / "favorites.json"

    exit_code, lines = invoke_rpc("list_favorites", {"favorites_path": str(favorites_path)})

    assert exit_code == 0
    assert lines == [{"event": "result", "data": {"mod_ids": []}}]


def test_set_favorite_and_list_favorites_round_trip_through_the_rpc_layer(
    tmp_path: Path,
) -> None:
    """If the body is `pass`, this fails: the second list_favorites call would
    still see [] instead of [42], since nothing was actually written."""
    favorites_path = tmp_path / "favorites.json"

    exit_code, lines = invoke_rpc(
        "set_favorite",
        {"mod_id": 42, "favorite": True, "favorites_path": str(favorites_path)},
    )
    assert exit_code == 0
    assert lines == [{"event": "result", "data": {"mod_ids": [42]}}]

    exit_code, lines = invoke_rpc("list_favorites", {"favorites_path": str(favorites_path)})
    assert exit_code == 0
    assert lines == [{"event": "result", "data": {"mod_ids": [42]}}]


# --------------------------------------------------------------------------
# Error → {code, details} mapping, one case per error_details() branch.
# --------------------------------------------------------------------------


def test_conflict_error_details_carry_only_the_conflicts_list(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """ConflictError details include the conflicts list and empty residues."""
    conflict = Conflict(
        kind="tracked-file",
        severity="error",
        message="clashes with X",
        path=Path("BepInEx/plugins/x.dll"),
        mod="X",
    )
    inject_error("status", ConflictError("boom", conflicts=[conflict]))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "CONFLICT"
    assert data["details"]["conflicts"] == [
        {
            "kind": "tracked-file",
            "severity": "error",
            "message": "clashes with X",
            "path": "BepInEx/plugins/x.dll",
            "mod": "X",
        }
    ]
    assert data["details"]["residues"] == []


def test_dependency_error_details_carry_only_the_conflicts_list(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """DependencyError details include the conflicts list and a dependency
    field."""
    conflict = Conflict(kind="missing-dependency", severity="error", message="needs Y")
    inject_error("status", DependencyError("boom", conflicts=[conflict]))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "DEPENDENCY"
    assert data["details"]["conflicts"][0]["kind"] == "missing-dependency"
    assert data["details"]["dependency"] == ""


def test_unresolvable_dependency_error_details_carry_dependency_and_reason(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """UnresolvableDependencyError(\"DepMod\", \"no compatible version...\") maps to code.

    details contain dependency=\"DepMod\", reason=\"no compatible version...\",
    and conflicts=[]; error_details() extracts these fields from the exception.
    """
    inject_error(
        "status",
        UnresolvableDependencyError("DepMod", "no compatible version published"),
    )

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "UNRESOLVABLE_DEPENDENCY"
    assert data["details"]["dependency"] == "DepMod"
    assert data["details"]["reason"] == "no compatible version published"
    assert data["details"]["conflicts"] == []


def test_partial_install_error_details_carry_the_installed_mod_names(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """PartialInstallError with installed=[First, Second] maps to details.installed.

    The error_details() function extracts error.installed and returns the list
    of mod names, so details.installed=[\"First\", \"Second\"].
    """
    installed = [
        services.InstallResult(mod=_mod_info("First")),
        services.InstallResult(mod=_mod_info("Second")),
    ]
    inject_error("status", PartialInstallError("boom", installed, RuntimeError("cause")))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "PARTIAL_INSTALL"
    assert data["details"]["installed"] == ["First", "Second"]


def test_profile_residue_error_details_carry_the_residues_list(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """ProfileResidueError with residues=[residue] maps to details.residues JSON.

    The error_details() function validates each residue through
    schemas.ProfileResidue.model_validate() and dumps them as JSON objects.
    """
    residue = ProfileResidue(profile="abc", category=ResidueCategory.ITEM, ids=["1", "2"], count=2)
    inject_error("status", ProfileResidueError("boom", residues=[residue]))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "PROFILE_RESIDUE"
    assert data["details"]["residues"] == [
        {
            "profile": "abc",
            "category": "item",
            "ids": ["1", "2"],
            "count": 2,
            "character": "",
            "path": "",
        }
    ]


def test_git_error_details_carry_the_conflicting_paths(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """GitError(conflicting_paths=[Path(\"a/b.txt\"), Path(\"c.txt\")]) maps to details.

    The error_details() function returns the error.conflicting_paths list as
    strings, so details.conflicting_paths=[\"a/b.txt\", \"c.txt\"].
    """
    inject_error("status", GitError("boom", conflicting_paths=[Path("a/b.txt"), Path("c.txt")]))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "GIT_ERROR"
    assert data["details"]["conflicting_paths"] == ["a/b.txt", "c.txt"]


def test_ambiguous_mod_error_details_carry_name_and_candidates_unsorted(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """AmbiguousModError(\"Foo\", [\"FooB\", \"FooA\"]) maps to details.name and candidates.

    error_details() returns the error's own candidate order untouched, so
    details.candidates=[\"FooB\", \"FooA\"] preserves insertion order.
    """
    inject_error("status", AmbiguousModError("Foo", ["FooB", "FooA"]))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "AMBIGUOUS_MOD"
    assert data["details"]["name"] == "Foo"
    # error_details() passes the error's own (unsorted) candidate order
    # through untouched; only the exception's message text sorts them.
    assert data["details"]["candidates"] == ["FooB", "FooA"]


def test_forge_api_error_details_carry_the_upstream_code_and_status(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """ForgeApiError(\"NOT_FOUND\", \"mod is gone\", 404) maps to details fields.

    The error code (FORGE_API_ERROR) names the exception class, not the upstream
    code; details.api_code=\"NOT_FOUND\" and details.api_status=404 preserve both.
    """
    inject_error("status", ForgeApiError("NOT_FOUND", "mod is gone", 404))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    # The envelope code names the exception class, not the upstream code.
    assert data["code"] == "FORGE_API_ERROR"
    assert data["error"] == "mod is gone"
    assert data["details"]["api_code"] == "NOT_FOUND"
    assert data["details"]["api_status"] == 404


def test_error_without_structured_payload_gets_empty_details(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """ArchiveError(\"archive is corrupt\") hits error_details default branch.

    The function returns schemas.ErrorDetails() with all fields set to empty
    defaults (conflicts=[], residues=[], api_status=0, etc.).
    """
    inject_error("status", ArchiveError("archive is corrupt"))

    exit_code, lines = invoke_rpc("status", {"path": "/whatever"})

    assert exit_code == 1
    data = lines[0]["data"]
    assert data["code"] == "ARCHIVE_ERROR"
    assert data["details"] == {
        "conflicts": [],
        "residues": [],
        "conflicting_paths": [],
        "installed": [],
        "name": "",
        "candidates": [],
        "dependency": "",
        "reason": "",
        "api_code": "",
        "api_status": 0,
    }


def test_unexpected_exception_maps_to_internal_error_with_a_well_formed_line(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """Unhandled exceptions map to INTERNAL_ERROR with the exception message."""
    inject_error("status", RuntimeError("kaboom"))

    result = runner.invoke(app, ["rpc", "status"], input=json.dumps({"path": "/x"}))
    lines = [line for line in result.stdout.splitlines() if line]

    assert result.exit_code == 1
    assert len(lines) == 1
    doc = json.loads(lines[0])  # raises if malformed
    assert doc["event"] == "error"
    assert doc["data"]["code"] == "INTERNAL_ERROR"
    assert doc["data"]["error"] == "kaboom"


# --------------------------------------------------------------------------
# Request errors: exit 2, code VALIDATION_ERROR.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "stdin", "expected_substring"),
    [
        ("status", "{not json", None),
        ("status", "", None),
        ("not_a_real_function", "{}", "not_a_real_function"),
        # StatusRequest requires "path"; omitting it triggers pydantic
        # validation, not a core error.
        ("status", "{}", None),
    ],
    ids=[
        "malformed-json",
        "empty-stdin",
        "unknown-function",
        "missing-required-field",
    ],
)
def test_request_errors_exit_two_with_validation_error(name: str, stdin: str, expected_substring: str | None) -> None:
    """Malformed requests, unknown functions, and validation failures exit
    with code 2 and code VALIDATION_ERROR."""
    result = runner.invoke(app, ["rpc", name], input=stdin)

    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["event"] == "error"
    assert doc["data"]["code"] == "VALIDATION_ERROR"
    if expected_substring is not None:
        assert expected_substring in doc["data"]["error"]


# --------------------------------------------------------------------------
# Forge-backed function, mocked with respx; cwd independence.
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


def test_rpc_output_does_not_depend_on_the_current_working_directory(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """status returns the same data whether cwd is the game dir or elsewhere.

    The test changes to a different directory and invokes status via RPC; the
    result is independent of cwd since paths are resolved from the request.
    """
    repo = clean_repo_at(fake_game_dir)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)

    exit_code, lines = invoke_rpc("status", {"path": str(repo.path)})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": {
                "spt_version": "4.1.0",
                "modified_profiles": [],
                "modified_configs": [],
                "modified_mod_settings": [],
                "manual_mods": [],
                "game_files_changed": [],
                "other_modified": [],
                "other_untracked": [],
            },
        }
    ]


def test_rpc_status_reports_an_empty_spt_version_when_it_cannot_be_resolved(
    fake_game_dir_runtime: Path,
) -> None:
    """status via RPC serialises an unresolvable spt_version as "", not null.

    fake_game_dir_runtime's core.json carries no sptVersion field and there is
    no SPT.Server.exe or stored version, so the fallback chain resolves to
    None; the RPC envelope must carry the empty string sptmod's schema
    default, not JSON null or the literal "None".
    """
    repo = clean_repo_at(fake_game_dir_runtime)

    exit_code, lines = invoke_rpc("status", {"path": str(repo.path)})

    assert exit_code == 0
    assert lines[-1]["data"]["spt_version"] == ""


# --------------------------------------------------------------------------
# Directory browsing / init: locate_directory, init_game_dir.
# --------------------------------------------------------------------------


def test_locate_directory_serialises_every_matched_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route renders the service's result list as JSON objects.

    Which directories the search finds is pinned at the service level; here
    the dispatched function is swapped for a stub returning known matches so
    that only the transport is under test.
    """
    matches = [
        services.LocatedDirectory(path=tmp_path / "games" / "SPT", is_game_dir=True),
        services.LocatedDirectory(path=tmp_path / "backup" / "SPT", is_game_dir=False),
    ]
    original = rpc.RPC_FUNCTIONS["locate_directory"]

    def locate(**_kwargs: object) -> list[services.LocatedDirectory]:
        return matches

    monkeypatch.setitem(
        rpc.RPC_FUNCTIONS, "locate_directory", dataclasses.replace(original, function=locate)
    )

    exit_code, lines = invoke_rpc("locate_directory", {"name": "SPT", "fingerprint": ["BepInEx", "cache"]})

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": [
                {"path": str(tmp_path / "games" / "SPT"), "is_game_dir": True},
                {"path": str(tmp_path / "backup" / "SPT"), "is_game_dir": False},
            ],
        }
    ]


def test_init_game_dir_reports_spt_version_and_no_pre_existing_mods(
    fake_game_dir: Path,
) -> None:
    """init_game_dir detects SPT 4.1.0 and returns pre_existing_mods=[], adopted=[].

    The test directory has no mods installed, so init_game_dir reports empty
    lists and a non-empty commit hash for the initial commit.
    """
    exit_code, lines = invoke_rpc("init_game_dir", {"path": str(fake_game_dir)})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["game_dir"] == str(fake_game_dir)
    assert data["spt_version"] == "4.1.0"
    assert data["pre_existing_mods"] == []
    assert data["adopted"] == []
    assert data["commit"] != ""


# --------------------------------------------------------------------------
# Mod lifecycle: list_mods, mod_files, history, disable_mod, enable_mod,
# remove_mod, check_profiles, adopt_mod, snapshot.
# --------------------------------------------------------------------------


def test_list_mods_returns_the_installed_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """list_mods returns all installed mods with their version and state."""
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("list_mods", {"path": str(clean_repo.path)})

    assert exit_code == 0
    data = lines[0]["data"]
    assert [mod["name"] for mod in data] == ["CoolMod"]
    assert data[0]["version"] == "1.2.3"
    assert data[0]["state"] == "installed"


def test_mod_files_returns_the_files_of_the_install_commit(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """mod_files returns the files added by a mod's install commit with change
    annotations."""
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("mod_files", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    assert lines[0]["data"] == [{"path": "BepInEx/plugins/CoolMod.dll", "change": "added"}]


def test_history_without_name_returns_commits_newest_first(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """history without name filter returns all commits with subjects in order.

    After install_commit adds \"mod: CoolMod 1.2.3\", history returns both it
    and the baseline \"chore: clean SPT 4.1.0\" with newest first.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("history", {"path": str(clean_repo.path)})

    assert exit_code == 0
    subjects = [commit["subject"] for commit in lines[0]["data"]]
    assert subjects == ["mod: CoolMod 1.2.3", "chore: clean SPT 4.1.0"]


def test_history_with_name_matches_case_insensitively(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """history(name=\"coolmod\") filters to the matching mod ignoring case.

    After install_commit adds CoolMod, history with name=\"coolmod\" returns
    only the install commit, not the baseline.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("history", {"path": str(clean_repo.path), "name": "coolmod"})

    assert exit_code == 0
    subjects = [commit["subject"] for commit in lines[0]["data"]]
    assert subjects == ["mod: CoolMod 1.2.3"]


def test_disable_mod_reverts_the_install_commit_and_removes_it_from_list_mods(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """disable_mod(\"CoolMod\") reverts the install commit and state becomes disabled.

    After disabling, list_mods returns empty list; the revert commit hash and
    deleted file list appear in the response.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("disable_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "CoolMod"
    assert data["state"] == "disabled"
    assert data["commit"] != ""
    assert data["files"] == ["BepInEx/plugins/CoolMod.dll"]

    _, listed_lines = invoke_rpc("list_mods", {"path": str(clean_repo.path)})
    assert listed_lines[0]["data"] == []


def test_enable_mod_restores_a_disabled_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """enable_mod restores a previously disabled mod and it reappears in
    list_mods."""
    install_commit(clean_repo, "CoolMod", "1.2.3")
    invoke_rpc("disable_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    exit_code, lines = invoke_rpc("enable_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "CoolMod"
    assert data["state"] == "enabled"
    assert data["commit"] != ""

    _, listed_lines = invoke_rpc("list_mods", {"path": str(clean_repo.path)})
    assert [mod["name"] for mod in listed_lines[0]["data"]] == ["CoolMod"]


def test_remove_mod_deletes_the_files_and_reports_them(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """remove_mod(\"CoolMod\") deletes BepInEx/plugins/CoolMod.dll from disk.

    The result reports the file path in the files list and the new commit hash;
    the actual file no longer exists after the call.
    """
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("remove_mod", {"path": str(clean_repo.path), "name": "CoolMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["name"] == "CoolMod"
    assert data["commit"] != ""
    assert data["files"] == ["BepInEx/plugins/CoolMod.dll"]
    assert not (clean_repo.path / "BepInEx/plugins/CoolMod.dll").exists()


def test_check_profiles_reports_no_residue_for_a_db_free_mod(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """check_profiles reports no residue for mods without database
    templates."""
    install_commit(clean_repo, "CoolMod", "1.2.3")

    exit_code, lines = invoke_rpc("check_profiles", {"path": str(clean_repo.path)})

    assert exit_code == 0
    assert lines[0]["data"] == {"residues": {}, "warnings": []}


def test_plan_profile_cleanup_reports_removals_through_rpc(clean_repo: Repo, install_commit: InstallCommit) -> None:
    """plan_profile_cleanup reports items that would be removed from profiles
    if the mod is uninstalled."""
    item_id = "5c0d1b8b86f7744bb2683cf7"
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps({item_id: {"_id": item_id, "_name": "item"}})
        },
    )
    profile: JsonDict = {
        "characters": {
            "pmc": {
                "Inventory": {"items": [{"_id": "i1", "_tpl": item_id, "slotId": "main"}]},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
    profile_relpath = Path("SPT/user/profiles/abc.json")
    (clean_repo.path / profile_relpath).write_text(json.dumps(profile), encoding="utf-8")
    clean_repo.commit("chore: profile snapshot", paths=[profile_relpath])

    exit_code, lines = invoke_rpc(
        "plan_profile_cleanup",
        {"path": str(clean_repo.path), "mod_name": "ServerMod"},
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["mod"] == "ServerMod"
    assert data["profiles"] == ["abc"]
    assert [r["category"] for r in data["removals"]] == ["item"]


def test_clean_profile_writes_the_profile_and_reports_a_snapshot_through_rpc(
    clean_repo: Repo, install_commit: InstallCommit
) -> None:
    """clean_profile removes residual database entries from profiles and
    reports a snapshot commit."""
    item_id = "5c0d1b8b86f7744bb2683cf8"
    install_commit(
        clean_repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT/user/mods/ServerMod/db/templates/items.json": json.dumps({item_id: {"_id": item_id, "_name": "item"}})
        },
    )
    profile: JsonDict = {
        "characters": {
            "pmc": {
                "Inventory": {"items": [{"_id": "i1", "_tpl": item_id, "slotId": "main"}]},
                "TradersInfo": {},
                "Quests": [],
            }
        }
    }
    profile_relpath = Path("SPT/user/profiles/abc.json")
    profile_path = clean_repo.path / profile_relpath
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    clean_repo.commit("chore: profile snapshot", paths=[profile_relpath])

    exit_code, lines = invoke_rpc("clean_profile", {"path": str(clean_repo.path), "mod_name": "ServerMod"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["profiles"] == ["abc"]
    assert data["snapshot"] != ""
    cleaned = json.loads(profile_path.read_text(encoding="utf-8"))
    assert cleaned["characters"]["pmc"]["Inventory"]["items"] == []


def test_clean_profile_maps_invalid_path_error_to_the_invalid_path_code(
    inject_error: Callable[[str, Exception], None],
) -> None:
    """clean_profile raises InvalidPathError(\"no such profile\") and error code becomes INVALID_PATH.

    The ERROR_MAPPING at errors.py maps InvalidPathError to \"INVALID_PATH\" code
    for the RPC envelope.
    """
    inject_error("clean_profile", InvalidPathError("no such profile"))

    exit_code, lines = invoke_rpc("clean_profile", {"path": "/whatever", "mod_name": "ServerMod"})

    assert exit_code == 1
    assert lines[0]["data"]["code"] == "INVALID_PATH"


def test_adopt_mod_commits_an_untracked_mod_folder(clean_repo: Repo) -> None:
    """adopt_mod(target=\"SPT/user/mods/ManualMod\") commits the directory.

    The mod's package.json is read for metadata; the response includes the mod
    info (name, version, state=installed) and the committed files list.
    """
    mod_dir = clean_repo.path / "SPT" / "user" / "mods" / "ManualMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        json.dumps({"name": "ManualMod", "version": "0.1.0", "author": "me"}),
        encoding="utf-8",
    )

    exit_code, lines = invoke_rpc(
        "adopt_mod",
        {"path": str(clean_repo.path), "target": "SPT/user/mods/ManualMod"},
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["mod"]["name"] == "ManualMod"
    assert data["mod"]["version"] == "0.1.0"
    assert data["mod"]["state"] == "installed"
    assert data["files"] == ["SPT/user/mods/ManualMod/package.json"]


def test_snapshot_commits_a_modified_profile(clean_repo: Repo) -> None:
    """snapshot commits a profile modification with a user-supplied message."""
    profile_path = clean_repo.path / "SPT/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    exit_code, lines = invoke_rpc("snapshot", {"path": str(clean_repo.path), "message": "test snapshot"})

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["message"] == "test snapshot"
    assert data["files"] == ["SPT/user/profiles/abc.json"]
    assert data["commit"] != ""


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
    events = [line["event"] for line in lines]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
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
    events = [line["event"] for line in lines]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
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
    events = [line["event"] for line in lines]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
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
