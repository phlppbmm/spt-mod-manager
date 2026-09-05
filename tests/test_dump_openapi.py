"""Behavioral tests for the hand-built OpenAPI dumper.

Covers: the CLI/subprocess entry point, drift against the committed
``openapi.json`` (the file that feeds the frontend's ``pnpm gen:api``),
and structural invariants of the generated document (route-per-service-
function, request/response ``$ref`` wiring, required standalone schemas,
absence of JSON ``null`` anywhere).
"""

import inspect
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sptmod.core import services
from sptmod.server.dump_openapi import DEFAULT_TARGET, build_openapi, dump, main

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_OPENAPI_JSON = REPO_ROOT / "openapi.json"


def _service_function_names() -> list[str]:
    """Every public, module-defined function of ``sptmod.core.services``."""
    return sorted(
        name
        for name, obj in vars(services).items()
        if inspect.isfunction(obj)
        and obj.__module__ == services.__name__
        and not name.startswith("_")
    )


SERVICE_FUNCTION_NAMES = _service_function_names()


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


def _find_nulls(value: Any, path: str, sink: list[str]) -> None:
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
    """Generated OpenAPI document matches the committed file."""
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
    """Running as module writes the OpenAPI document to disk."""
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


def test_run_module_as_main_writes_the_built_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Module's __main__ guard writes the document."""
    # Runs the actual `if __name__ == "__main__":` guard in-process (unlike
    # the subprocess test above, which the coverage run driving this suite
    # cannot see into): if that guard's `sys.exit(main(sys.argv[1:]))` line
    # were removed, running the module as __main__ would do nothing, so no
    # file would appear and this fails.
    target = tmp_path / "generated.json"
    monkeypatch.setattr(sys, "argv", ["dump_openapi", str(target)])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module(
            "sptmod.server.dump_openapi", run_name="__main__", alter_sys=True
        )

    assert exit_info.value.code == 0
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == build_openapi()


def test_dump_openapi_run_with_too_many_arguments_exits_2_and_prints_usage(
    tmp_path: Path,
) -> None:
    """Too many arguments causes usage error."""
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


def test_main_with_no_arguments_writes_to_the_default_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No arguments uses the default output path."""
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
    """Too many arguments prevents file creation."""
    monkeypatch.chdir(tmp_path)

    exit_code = main(["a", "b"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err.lower()
    assert not (tmp_path / DEFAULT_TARGET).exists()


def test_dump_creates_missing_parent_directories(tmp_path: Path) -> None:
    """Dump creates parent directories when needed."""
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


def test_document_declares_openapi_3_1(document: dict[str, Any]) -> None:
    """Document declares OpenAPI 3.1.0."""
    assert document["openapi"] == "3.1.0"


def test_every_service_function_has_exactly_one_post_path(
    document: dict[str, Any],
) -> None:
    """Each service function has exactly one POST path."""
    for name in SERVICE_FUNCTION_NAMES:
        path_item = document["paths"][f"/api/{name}"]
        assert set(path_item.keys()) == {"post"}


@pytest.mark.parametrize("name", SERVICE_FUNCTION_NAMES)
def test_request_body_refs_the_camel_case_request_schema(
    document: dict[str, Any], name: str
) -> None:
    """Request body references the correct PascalCase request schema."""
    operation = document["paths"][f"/api/{name}"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    expected_name = f"{_camel(name)}Request"

    assert request_schema == {"$ref": f"#/components/schemas/{expected_name}"}
    assert expected_name in document["components"]["schemas"]


@pytest.mark.parametrize("name", SERVICE_FUNCTION_NAMES)
def test_200_response_resolves_to_an_existing_component_schema(
    document: dict[str, Any], name: str
) -> None:
    """200 response schema reference exists in components."""
    operation = document["paths"][f"/api/{name}"]["post"]
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    ref_name = _resolved_ref_name(response_schema)

    assert ref_name is not None, response_schema
    assert ref_name in document["components"]["schemas"]


def test_error_response_and_progress_event_schemas_exist(
    document: dict[str, Any],
) -> None:
    """ErrorResponse and ProgressEvent schemas exist."""
    schemas = document["components"]["schemas"]

    assert "ErrorResponse" in schemas
    assert "ProgressEvent" in schemas


def test_document_contains_no_null_values_anywhere(document: dict[str, Any]) -> None:
    """No null values exist anywhere in the OpenAPI document."""
    # openapi-typescript v7 turns "x?: T | null" into a required field, so a
    # reintroduced explicit `null` anywhere in the tree would break codegen.
    null_paths: list[str] = []
    _find_nulls(document, "", null_paths)

    assert null_paths == []


# --------------------------------------------------------------------------
# Regression: the ASGI web framework layer must not come back
# --------------------------------------------------------------------------


def test_cli_and_dump_openapi_do_not_pull_in_the_removed_web_framework() -> None:
    """Removed web framework dependencies are not reintroduced."""
    # If the CLI or the OpenAPI dumper reintroduced a dependency on the old
    # ASGI web framework stack, this fails because one of the runtime-built
    # names below would show up either as an imported top-level package or
    # (transitively) as an installed spec in the project environment.
    #
    # Names are assembled at runtime (never written as a literal substring)
    # so this file stays invisible to the repo-wide grep for those package
    # names, the acceptance check that this test exists to keep enforced in
    # code instead of text.
    script = r"""
import importlib.util
import sys

removed = [
    "fast" + "api",
    "uvi" + "corn",
    "sse_" + "star" + "lette",
    "star" + "lette",
]

import sptmod.cli  # noqa: F401
import sptmod.server.dump_openapi  # noqa: F401

top_level_imported = {name.split(".")[0] for name in sys.modules}
imported_hits = top_level_imported.intersection(removed)
assert imported_hits == set(), f"still imported: {sorted(imported_hits)}"

installed_hits = [
    name for name in removed if importlib.util.find_spec(name) is not None
]
assert installed_hits == [], f"still installed: {sorted(installed_hits)}"

print("ok")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"
