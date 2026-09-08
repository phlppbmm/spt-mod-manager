"""In-process tests for ``sptmod rpc <name>`` (typer ``CliRunner``): the NDJSON
envelope contract on stdout, progress/result/error line ordering, operation ID
echoing, wrapped string results, the error → ``{code, details}`` mapping, and
the request-error exit path. Every case that can be provoked from a real
precondition -- conflicting installs, an ambiguous mod name, a Forge error
envelope, an unreadable archive -- is; the ``inject_error`` fixture covers only
the errors listed in its docstring, whose preconditions no public call reaches.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from conftest import clean_repo_at, invoke_rpc
from fixture_types import InstallCommit, MakeArchive
from fixtures_rpc import assert_progress_then_result, install_request
from sptmod.cli import app
from sptmod.core import services
from sptmod.core.errors import (
    DependencyError,
    GitError,
    PartialInstallError,
    ProfileResidueError,
    UnresolvableDependencyError,
)
from sptmod.core.forge.client import DEFAULT_BASE_URL
from sptmod.core.models import (
    Conflict,
    Json,
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
    """install_from_archive reports every published install step, then the result.

    Installs a one-file archive through ``sptmod rpc install_from_archive`` and parses
    every stdout line as JSON; asserts that all lines but the last carry event=progress,
    that the last carries event=result, that the reported ``step`` names are exactly
    ``services.INSTALL_STEPS`` in order followed by a closing line on the final step at
    percent=100. Reason: a caller drives its progress display off these step names and
    the 100-percent line, so the published step sequence is the observable contract.
    """
    request = install_request(fake_game_dir, make_archive)

    result = runner.invoke(app, ["rpc", "install_from_archive"], input=json.dumps(request))
    raw_lines = [line for line in result.stdout.splitlines() if line]

    assert result.exit_code == 0
    parsed = [json.loads(line) for line in raw_lines]
    assert_progress_then_result(parsed)
    expected_steps = [step.value for step in services.INSTALL_STEPS]
    assert [doc["step"] for doc in parsed[:-1]] == [*expected_steps, expected_steps[-1]]
    assert parsed[-2]["percent"] == 100


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


_WRAPPED_RESULT_CASES: list[tuple[str, Callable[[Path, Path], dict[str, str]], dict[str, Json]]] = [
    (
        "set_spt_version",
        lambda repo_path, _tmp_path: {"path": str(repo_path), "version": "4.2.0"},
        {"spt_version": "4.2.0"},
    ),
    (
        "export_mods",
        lambda repo_path, tmp_path: {"path": str(repo_path), "db_path": str(tmp_path / "unused-catalog.db")},
        {"content": ""},
    ),
    (
        "list_favorites",
        lambda _repo_path, tmp_path: {"favorites_path": str(tmp_path / "favorites.json")},
        {"mod_ids": []},
    ),
]


@pytest.mark.parametrize(
    ("name", "build_request", "expected_data"),
    _WRAPPED_RESULT_CASES,
    ids=["spt_version-string", "content-string", "mod_ids-list"],
)
def test_service_results_that_are_not_objects_are_wrapped_under_a_named_field(
    fake_game_dir: Path,
    tmp_path: Path,
    name: str,
    build_request: Callable[[Path, Path], dict[str, str]],
    expected_data: dict[str, Json],
) -> None:
    """Service functions returning a bare string or list still answer with a JSON object.

    Sets up a clean repository and invokes each of the three RPC functions whose service
    returns a plain string or list; asserts the single result line carries the value under
    the field the contract names for it. Reason: every RPC response must be a JSON object
    so the generated client types stay stable, so a bare value has to be wrapped.
    """
    repo = clean_repo_at(fake_game_dir)

    exit_code, lines = invoke_rpc(name, build_request(repo.path, tmp_path))

    assert exit_code == 0
    assert lines == [{"event": "result", "data": expected_data}]


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
    fake_game_dir: Path, make_archive: MakeArchive
) -> None:
    """A second mod claiming an installed mod's file answers with the blocking conflict.

    Installs an archive providing BepInEx/plugins/Shared.dll, then installs a second
    archive under another mod name providing the same file; asserts the RPC call exits 1
    with code CONFLICT and details listing the tracked-file conflict (path plus the mod
    that already owns it) and no residues. Reason: the caller renders this list to explain
    why the install was refused, so both the conflict entries and the empty residues
    matter.
    """
    clean_repo_at(fake_game_dir)
    first = make_archive({"BepInEx/plugins/Shared.dll": b"first-bytes"}, "zip")
    assert invoke_rpc("install_from_archive", {"path": str(fake_game_dir), "archive": str(first)})[0] == 0
    second = make_archive({"BepInEx/plugins/Shared.dll": b"second-bytes"}, "zip")

    exit_code, lines = invoke_rpc(
        "install_from_archive",
        {"path": str(fake_game_dir), "archive": str(second), "name_override": "OtherMod"},
    )

    assert exit_code == 1
    data = lines[-1]["data"]
    assert data["code"] == "CONFLICT"
    assert data["details"]["conflicts"] == [
        {
            "kind": "tracked-file",
            "severity": "error",
            "message": "BepInEx/plugins/Shared.dll is already provided by mod Shared",
            "path": "BepInEx/plugins/Shared.dll",
            "mod": "Shared",
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


def test_ambiguous_mod_error_details_carry_the_requested_name_and_the_candidates(
    fake_game_dir: Path, install_commit: InstallCommit
) -> None:
    """Removing a mod by a name that matches two installs reports both candidates.

    Installs "CoolMod" and "coolmod" into one game directory and asks
    ``sptmod rpc remove_mod`` to remove "COOLMOD"; asserts exit 1, code AMBIGUOUS_MOD and
    details carrying the name as typed plus both matching mod names. Reason: the caller
    turns these candidates into a disambiguation prompt, so it needs the requested name
    and every match.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod", minute=1)
    install_commit(repo, "coolmod", minute=2)

    exit_code, lines = invoke_rpc("remove_mod", {"path": str(fake_game_dir), "name": "COOLMOD"})

    assert exit_code == 1
    data = lines[-1]["data"]
    assert data["code"] == "AMBIGUOUS_MOD"
    assert data["details"]["name"] == "COOLMOD"
    assert data["details"]["candidates"] == ["CoolMod", "coolmod"]


def test_forge_api_error_details_carry_the_upstream_code_and_status(tmp_path: Path) -> None:
    """A Forge error envelope is passed on with its own code and HTTP status.

    Mocks the Forge answering the version list of mod 42 with an unsuccessful envelope
    (code NOT_FOUND, message "mod is gone") under HTTP 404 and asks
    ``sptmod rpc get_mod_details`` for that mod; asserts exit 1, the envelope code naming
    the exception class, the upstream message as the error text, and details preserving
    the upstream api_code and api_status. Reason: the caller needs the upstream code and
    status to tell a deleted mod from a temporary Forge failure.
    """
    error_envelope = {"success": False, "code": "NOT_FOUND", "message": "mod is gone"}
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        router.get("/mod/42/versions").respond(404, json=error_envelope)
        router.get("/mod/42").respond(404, json=error_envelope)

        exit_code, lines = invoke_rpc("get_mod_details", {"mod_id": 42, "db_path": str(tmp_path / "catalog.db")})

    assert exit_code == 1
    data = lines[-1]["data"]
    # The envelope code names the exception class, not the upstream code.
    assert data["code"] == "FORGE_API_ERROR"
    assert data["error"] == "mod is gone"
    assert data["details"]["api_code"] == "NOT_FOUND"
    assert data["details"]["api_status"] == 404


def test_error_without_structured_payload_gets_empty_details(fake_game_dir: Path, tmp_path: Path) -> None:
    """An unreadable archive answers with every details field at its empty default.

    Points ``sptmod rpc install_from_archive`` at a file that carries a zip signature but
    no readable archive; asserts exit 1, code ARCHIVE_ERROR and a details object whose
    every field is present and empty. Reason: the generated client types declare all
    details fields as required, so an error carrying no structured payload must still
    serialise the complete object rather than omitting fields or emitting null.
    """
    clean_repo_at(fake_game_dir)
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 truncated before any entry")

    exit_code, lines = invoke_rpc("install_from_archive", {"path": str(fake_game_dir), "archive": str(broken)})

    assert exit_code == 1
    data = lines[-1]["data"]
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
