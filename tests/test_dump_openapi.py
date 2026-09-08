"""Behavioral tests for the hand-built OpenAPI dumper.

Covers: the CLI/subprocess entry point, drift against the committed
``openapi.json`` (the file that feeds the frontend's ``pnpm gen:api``),
and structural invariants of the generated document (route-per-service-
function, request/response ``$ref`` wiring, required standalone schemas,
absence of JSON ``null`` anywhere).
"""

import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import service_function_names
from sptmod.core.models import Json
from sptmod.server.dump_openapi import DEFAULT_TARGET, build_openapi, dump, main

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_OPENAPI_JSON = REPO_ROOT / "openapi.json"


SERVICE_FUNCTION_NAMES = service_function_names()


def _camel(name: str) -> str:
    """Convert ``snake_case`` to ``PascalCase``, matching the schema naming."""
    return "".join(word.capitalize() for word in name.split("_"))


def _resolved_ref_name(schema: dict[str, Any]) -> str | None:
    """Return the component name a response schema resolves to, if any.

    Handles both a bare ``$ref`` (single-model responses) and a JSON Schema
    array wrapping a ``$ref`` in ``items`` (``list[Model]`` responses).
    """
    if "$ref" in schema:
        ref: str = schema["$ref"]
        return ref.rsplit("/", 1)[-1]
    if schema.get("type") == "array" and "$ref" in schema.get("items", {}):
        items_ref: str = schema["items"]["$ref"]
        return items_ref.rsplit("/", 1)[-1]
    return None


def _find_nulls(value: Json, path: str, sink: list[str]) -> None:
    """Collect JSON-Pointer-ish paths of every ``None`` value under ``value``."""
    if value is None:
        sink.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            _find_nulls(item, f"{path}/{key}", sink)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _find_nulls(item, f"{path}[{index}]", sink)


# --------------------------------------------------------------------------
# Drift against the committed file
# --------------------------------------------------------------------------


def test_build_openapi_matches_the_committed_openapi_json() -> None:
    """The built OpenAPI document byte-for-byte equals the committed openapi.json.

    Calls build_openapi() and compares the dict result against the committed
    COMMITTED_OPENAPI_JSON parsed from disk; any divergence (routing, schema,
    response wrapping) fails the test.
    """
    # If build_openapi() body were `pass` (returns None), this fails because
    # the committed document, parsed from disk, is a non-trivial dict that
    # can never equal None; any schema drift also breaks the equality.
    committed = json.loads(COMMITTED_OPENAPI_JSON.read_text(encoding="utf-8"))

    assert build_openapi() == committed


# --------------------------------------------------------------------------
# Subprocess / CLI behavior
# --------------------------------------------------------------------------


def test_dump_openapi_run_as_a_module_writes_the_built_document(
    tmp_path: Path,
) -> None:
    """Running the module with -m writes the OpenAPI document to the given path.

    Executes ``python -m sptmod.server.dump_openapi <target>`` as a subprocess;
    must exit 0, create the file, and its JSON content must match build_openapi().
    """
    # If main()/dump() did nothing, the process would exit 0 with no file
    # written, or the file would be empty/invalid JSON, or its content would
    # not equal build_openapi(); each of those breaks one assertion below.
    target = tmp_path / "generated.json"

    result = subprocess.run(
        [sys.executable, "-m", "sptmod.server.dump_openapi", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert target.is_file()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == build_openapi()


def test_run_module_as_main_writes_the_built_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The module's __name__ == "__main__" guard invokes main() and calls sys.exit.

    Uses runpy.run_module() to execute the module's __main__ block in-process;
    must exit with code 0 and write the built OpenAPI document.
    """
    # Runs the actual `if __name__ == "__main__":` guard in-process (unlike
    # the subprocess test above, which the coverage run driving this suite
    # cannot see into): if that guard's `sys.exit(main(sys.argv[1:]))` line
    # were removed, running the module as __main__ would do nothing, so no
    # file would appear and this fails.
    target = tmp_path / "generated.json"
    monkeypatch.setattr(sys, "argv", ["dump_openapi", str(target)])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("sptmod.server.dump_openapi", run_name="__main__", alter_sys=True)

    assert exit_info.value.code == 0
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == build_openapi()


def test_dump_openapi_run_with_too_many_arguments_exits_2_and_prints_usage(
    tmp_path: Path,
) -> None:
    """Passing two arguments exits 2 with usage message to stderr.

    Runs with two positional arguments (one more than allowed); must exit
    with code 2, print "usage:" to stderr, and stdout must be empty.
    """
    result = subprocess.run(
        [sys.executable, "-m", "sptmod.server.dump_openapi", "a", "b"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert result.stdout == ""


def test_main_with_no_arguments_writes_to_the_default_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling main() with no arguments writes to DEFAULT_TARGET (openapi.json).

    Changes directory to a scratch path and calls main([]); must return 0 and
    write the document to DEFAULT_TARGET resolved in the current directory.
    """
    # DEFAULT_TARGET is a relative path resolved against the current working
    # directory; chdir into a scratch directory so this never touches the
    # repository's own openapi.json.
    monkeypatch.chdir(tmp_path)

    exit_code = main([])

    assert exit_code == 0
    written_target = tmp_path / DEFAULT_TARGET
    assert written_target.is_file()
    assert json.loads(written_target.read_text(encoding="utf-8")) == build_openapi()


def test_main_with_too_many_arguments_does_not_write_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Calling main() with two arguments returns 2 and writes nothing.

    Calls main(["a", "b"]) from a scratch directory; must return 2, print
    "usage:" to stderr, and DEFAULT_TARGET must not be created.
    """
    monkeypatch.chdir(tmp_path)

    exit_code = main(["a", "b"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err.lower()
    assert not (tmp_path / DEFAULT_TARGET).exists()


def test_dump_creates_missing_parent_directories(tmp_path: Path) -> None:
    """The dump() function creates missing parent directories.

    Calls dump() with a path three levels deep (nested/dir/openapi.json) with
    no parent directories existing; must create all parents and write the file.
    """
    target = tmp_path / "nested" / "dir" / "openapi.json"

    dump(target)

    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == build_openapi()


# --------------------------------------------------------------------------
# Structural invariants of build_openapi()'s document
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    """Build the OpenAPI document once for all structural tests in the module."""
    return build_openapi()


def test_every_service_function_has_exactly_one_post_path(
    document: dict[str, Any],
) -> None:
    """Each service function maps to exactly one /api/{name} POST path.

    Iterates over all public service functions and asserts document["paths"]
    has one entry per function containing only a "post" operation (no get/put/delete).
    """
    for name in SERVICE_FUNCTION_NAMES:
        path_item = document["paths"][f"/api/{name}"]
        assert set(path_item.keys()) == {"post"}


@pytest.mark.parametrize("name", SERVICE_FUNCTION_NAMES)
def test_request_body_refs_the_camel_case_request_schema(document: dict[str, Any], name: str) -> None:
    """Request body references the correct PascalCase request schema."""
    operation = document["paths"][f"/api/{name}"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    expected_name = f"{_camel(name)}Request"

    assert request_schema == {"$ref": f"#/components/schemas/{expected_name}"}
    assert expected_name in document["components"]["schemas"]


@pytest.mark.parametrize("name", SERVICE_FUNCTION_NAMES)
def test_200_response_resolves_to_an_existing_component_schema(document: dict[str, Any], name: str) -> None:
    """Each service function's 200 response $ref points to an existing schema.

    Parametrized over all service functions; extracts the response schema's
    $ref (handling both bare refs and array wrapping) and asserts it exists
    in components/schemas.
    """
    operation = document["paths"][f"/api/{name}"]["post"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    ref_name = _resolved_ref_name(response_schema)

    assert ref_name is not None, response_schema
    assert ref_name in document["components"]["schemas"]


def test_error_response_and_progress_event_schemas_exist(
    document: dict[str, Any],
) -> None:
    """The components/schemas has both ErrorResponse and ProgressEvent entries.

    Checks that the two shared response schemas used by the RPC transport are
    present in the OpenAPI document.
    """
    schemas = document["components"]["schemas"]

    assert "ErrorResponse" in schemas
    assert "ProgressEvent" in schemas


def test_document_contains_no_null_values_anywhere(document: dict[str, Any]) -> None:
    """The document tree contains no JSON null values anywhere.

    Recursively scans the document; if null is found, the paths are collected
    and asserted to be empty (openapi-typescript v7 mishandles explicit nulls).
    """
    # openapi-typescript v7 turns "x?: T | null" into a required field, so a
    # reintroduced explicit `null` anywhere in the tree would break codegen.
    null_paths: list[str] = []
    _find_nulls(document, "", null_paths)

    assert not null_paths
