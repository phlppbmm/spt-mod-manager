"""Reflection-only contract tests for ``sptmod.server.rpc`` (no subprocess,
no CLI invocation): ``RPC_FUNCTIONS`` is checked against the service module,
the request/response schemas and the core error hierarchy purely by
introspection.

It carries the route-completeness and schema-parity spec for the adapter.
"""

import dataclasses
import inspect

import pytest
from pydantic import BaseModel

from sptmod.core import errors as core_errors
from sptmod.core import services
from sptmod.core.forge.catalog import (
    CatalogEntry,
    CatalogMod,
    CatalogVersion,
    SyncResult,
)
from sptmod.core.models import CommitInfo, Conflict, ModFile, ModInfo, ProfileResidue
from sptmod.server import schemas
from sptmod.server.errors import ERROR_MAPPING, FALLBACK_ERROR_CODE, error_code
from sptmod.server.rpc import RPC_FUNCTIONS


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
    return "".join(part.title() for part in name.split("_"))


def test_every_service_function_is_an_rpc_key_and_vice_versa() -> None:
    assert set(RPC_FUNCTIONS.keys()) == set(SERVICE_FUNCTION_NAMES)


def test_rpc_functions_has_exactly_twenty_six_entries() -> None:
    assert len(RPC_FUNCTIONS) == 26


@pytest.mark.parametrize("name", sorted(RPC_FUNCTIONS))
def test_spec_function_is_the_same_named_service_function(name: str) -> None:
    assert RPC_FUNCTIONS[name].function is getattr(services, name)


@pytest.mark.parametrize("name", sorted(RPC_FUNCTIONS))
def test_spec_request_resolves_the_same_named_camel_case_request_model(
    name: str,
) -> None:
    expected = getattr(schemas, f"{_camel(name)}Request")
    assert RPC_FUNCTIONS[name].request is expected


# Keyword-only parameters install_from_archive accepts for internal callers
# (install_mod fills them from the resolved plan) but does not expose on its
# RPC request; every other function's request fields exactly match its
# service parameters minus progress/client.
_INTERNAL_ONLY_PARAMS: dict[str, set[str]] = {
    "install_from_archive": {"forge_id", "forge_version_id", "source"},
}


@pytest.mark.parametrize("name", sorted(RPC_FUNCTIONS))
def test_request_fields_match_service_parameters_minus_progress_and_client(
    name: str,
) -> None:
    spec = RPC_FUNCTIONS[name]
    params = set(inspect.signature(spec.function).parameters) - {
        "progress",
        "client",
    }
    params -= _INTERNAL_ONLY_PARAMS.get(name, set())
    request_fields = set(spec.request.model_fields.keys()) - {"operation_id"}
    assert request_fields == params


# --------------------------------------------------------------------------
# Schema parity: every field of a core dataclass must survive into its
# CoreModel counterpart.
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


# --------------------------------------------------------------------------
# Error mapping completeness.
# --------------------------------------------------------------------------


def _concrete_core_error_classes() -> list[type[Exception]]:
    return [
        obj
        for _, obj in vars(core_errors).items()
        if inspect.isclass(obj)
        and issubclass(obj, Exception)
        and obj.__module__ == core_errors.__name__
    ]


CONCRETE_CORE_ERROR_CLASSES = _concrete_core_error_classes()


def test_there_are_twentyone_concrete_core_error_classes() -> None:
    # Pins the corpus this test enumerates so a newly added exception class
    # is caught here rather than silently skipped by the parametrization.
    assert len(CONCRETE_CORE_ERROR_CLASSES) == 21


@pytest.mark.parametrize(
    "error_type",
    CONCRETE_CORE_ERROR_CLASSES,
    ids=[cls.__name__ for cls in CONCRETE_CORE_ERROR_CLASSES],
)
def test_every_concrete_core_error_maps_to_a_non_fallback_code(
    error_type: type[Exception],
) -> None:
    code = error_code(error_type)

    assert code != FALLBACK_ERROR_CODE
    assert error_type in ERROR_MAPPING


def test_operation_in_progress_maps_to_its_own_specific_code() -> None:
    assert error_code(core_errors.OperationInProgressError) == "OPERATION_IN_PROGRESS"


# --------------------------------------------------------------------------
# operation_id / wrap bookkeeping.
# --------------------------------------------------------------------------


def test_exactly_the_responses_declaring_operation_id_are_marked_in_the_spec() -> None:
    declares_operation_id = {
        name
        for name, spec in RPC_FUNCTIONS.items()
        if inspect.isclass(spec.response)
        and issubclass(spec.response, BaseModel)
        and "operation_id" in spec.response.model_fields
    }
    marked = {name for name, spec in RPC_FUNCTIONS.items() if spec.operation_id}

    assert marked == declares_operation_id
    assert marked == {
        "install_from_archive",
        "sync_catalog",
        "install_mod",
        "update_mod",
    }


def test_exactly_the_wrapped_specs_wrap_a_plain_string_result() -> None:
    wrapped = {name: spec.wrap for name, spec in RPC_FUNCTIONS.items() if spec.wrap}

    assert wrapped == {
        "set_spt_version": "spt_version",
        "export_mods": "content",
    }
