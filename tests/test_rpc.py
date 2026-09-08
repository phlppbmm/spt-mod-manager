"""In-process tests for ``sptmod rpc <name>`` (typer ``CliRunner``): the NDJSON
envelope contract on stdout, progress/result/error line ordering, operation ID
echoing, wrapped string results, the error → ``{code, details}`` mapping, and
the request-error exit path. Runs against the real service functions except
for the error-mapping cases, where the dispatched function is swapped for a
small stub raising a specific core exception -- the cheapest way to reach
every ``error_details`` branch without reproducing each error's real
precondition.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import clean_repo_at, invoke_rpc
from fixture_types import MakeArchive
from fixtures_rpc import install_request
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import (
    AmbiguousModError,
    ArchiveError,
    ConflictError,
    DependencyError,
    ForgeApiError,
    GitError,
    PartialInstallError,
    ProfileResidueError,
    UnresolvableDependencyError,
)
from sptmod.core.models import (
    Conflict,
    ModInfo,
    ModState,
    ModType,
    ProfileResidue,
    ResidueCategory,
)


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


def test_progress_lines_precede_the_result_line_and_every_line_is_valid_json(
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """install_from_archive emits 8 progress events followed by one result event.

    All lines are valid JSON with event field; INSTALL_STEPS enumerate exactly
    8 install steps, and the final line has event=result instead of progress.
    """
    request = install_request(fake_game_dir, make_archive)

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
    request = install_request(fake_game_dir, make_archive, operation_id="caller-op")

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
    request = install_request(fake_game_dir, make_archive)

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
        "missing": [],
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
# cwd independence.
# --------------------------------------------------------------------------


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
