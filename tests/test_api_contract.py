"""Contract tests for the FastAPI adapter: route completeness, health,
OpenAPI schema generation, the dump script, and the FastAPI-native shape of
validation errors.

Covers §9 of the task: every public service function has a matching
``POST /api/<name>`` route, the OpenAPI schema/dump include all 17 service
routes plus ``/api/health`` and ``/api/events``, and a request that fails
Pydantic validation gets FastAPI's own ``{"detail": [...]}`` body rather than
the core error shape.
"""

import dataclasses
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from sptmod.core import services
from sptmod.core.forge.catalog import (
    CatalogEntry,
    CatalogMod,
    CatalogVersion,
    SyncResult,
)
from sptmod.core.models import CommitInfo, Conflict, ModFile, ModInfo, ProfileResidue
from sptmod.server import schemas
from sptmod.server.app import app, router

client = TestClient(app)


def _service_function_names() -> list[str]:
    """Every public, module-defined function of ``sptmod.core.services``.

    Excludes re-exported dataclasses/types and private helpers, leaving
    exactly the operations the HTTP adapter must expose one route per.
    """
    return sorted(
        name
        for name, obj in vars(services).items()
        if inspect.isfunction(obj)
        and obj.__module__ == services.__name__
        and not name.startswith("_")
    )


SERVICE_FUNCTION_NAMES = _service_function_names()


def test_services_module_exposes_the_expected_17_public_functions() -> None:
    # Pins the count the rest of this module's parametrization relies on, so
    # a function silently renamed/removed from services.py is caught here
    # instead of just shrinking the parametrize list unnoticed.
    assert len(SERVICE_FUNCTION_NAMES) == 17


@pytest.mark.parametrize("name", SERVICE_FUNCTION_NAMES)
def test_service_function_has_a_matching_post_route(name: str) -> None:
    post_paths = {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }
    assert f"/api/{name}" in post_paths


def test_health_endpoint_reports_ok() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_contains_every_service_route_plus_health_and_events() -> None:
    schema = app.openapi()

    expected = {f"/api/{name}" for name in SERVICE_FUNCTION_NAMES}
    expected.add("/api/health")
    expected.add("/api/events")
    assert expected.issubset(schema["paths"].keys())


def test_dump_openapi_script_writes_valid_json_with_all_paths(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    result = subprocess.run(
        [sys.executable, "-m", "sptmod.server.dump_openapi", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert target.is_file()
    schema = json.loads(target.read_text(encoding="utf-8"))
    expected = {f"/api/{name}" for name in SERVICE_FUNCTION_NAMES}
    expected.add("/api/health")
    expected.add("/api/events")
    assert expected.issubset(schema["paths"].keys())


# --------------------------------------------------------------------------
# Schema parity: every field of a core dataclass must survive into its
# CoreModel counterpart (from_attributes only copies fields the schema
# declares, so a schema falling behind its dataclass would silently drop
# data instead of erroring). No exclusions are currently needed: every pair
# below is a full field-name match, dataclass side included.
# --------------------------------------------------------------------------

SCHEMA_PARITY_PAIRS: list[tuple[type, type[BaseModel]]] = [
    (ModInfo, schemas.ModInfo),
    (ModFile, schemas.ModFile),
    (CommitInfo, schemas.CommitInfo),
    (Conflict, schemas.Conflict),
    (ProfileResidue, schemas.ProfileResidue),
    (CatalogMod, schemas.CatalogMod),
    (CatalogVersion, schemas.CatalogVersion),
    (CatalogEntry, schemas.CatalogEntry),
    (SyncResult, schemas.SyncResult),
    (services.InitResult, schemas.InitResult),
    (services.InstallResult, schemas.InstallResult),
    (services.RemoveResult, schemas.RemoveResult),
    (services.ToggleResult, schemas.ToggleResult),
    (services.AdoptResult, schemas.AdoptResult),
    (services.ManualMod, schemas.ManualMod),
    (services.StatusReport, schemas.StatusReport),
    (services.SnapshotResult, schemas.SnapshotResult),
    (services.ImportEntry, schemas.ImportEntry),
    (services.ImportDiff, schemas.ImportDiff),
]


@pytest.mark.parametrize(
    "dataclass_type,schema_type",
    SCHEMA_PARITY_PAIRS,
    ids=[dataclass_type.__name__ for dataclass_type, _ in SCHEMA_PARITY_PAIRS],
)
def test_schema_declares_every_field_of_its_core_dataclass(
    dataclass_type: type, schema_type: type[BaseModel]
) -> None:
    dataclass_fields = {field.name for field in dataclasses.fields(dataclass_type)}
    schema_fields = set(schema_type.model_fields.keys())

    assert dataclass_fields <= schema_fields


def test_validation_error_keeps_the_native_fastapi_shape_not_the_core_error_shape() -> (
    None
):
    # "path" is a required field of StatusRequest; omitting it must trigger
    # FastAPI's own 422 request-validation handling, not error_response().
    response = client.post("/api/status", json={})

    assert response.status_code == 422
    body = response.json()
    assert "detail" in body
    assert "error" not in body
    assert "code" not in body
