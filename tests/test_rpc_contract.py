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

from conftest import service_function_names
from sptmod.core import errors as core_errors
from sptmod.core import services
from sptmod.core.forge.catalog import (
    CatalogEntry,
    CatalogMod,
    CatalogVersion,
    SyncResult,
)
from sptmod.core.launch import LaunchResult, LaunchStatus, StopResult
from sptmod.core.models import (
    CommitInfo,
    Conflict,
    ModFile,
    ModInfo,
    ProfileResidue,
    ProfileSummary,
)
from sptmod.server import schemas
from sptmod.server.errors import ERROR_MAPPING, ErrorCode, error_code
from sptmod.server.rpc import RPC_FUNCTIONS

SERVICE_FUNCTION_NAMES = service_function_names()


def _camel(name: str) -> str:
    """Convert snake_case to PascalCase by title-casing each part.

    install_from_archive becomes InstallFromArchiveRequest in the schemas module.
    """
    return "".join(part.title() for part in name.split("_"))


def test_every_service_function_is_an_rpc_key_and_vice_versa() -> None:
    """RPC_FUNCTIONS.keys() == SERVICE_FUNCTION_NAMES (no extra, no missing).

    SERVICE_FUNCTION_NAMES is the list of public functions in sptmod.core.services;
    this pins the contract between core and RPC adapter.
    """
    assert set(RPC_FUNCTIONS.keys()) == set(SERVICE_FUNCTION_NAMES)


def test_rpc_functions_has_exactly_thirtyfour_entries() -> None:
    """len(RPC_FUNCTIONS) == 34; pins the endpoint count against regressions.

    Adding or removing an endpoint must update this assertion to keep the
    contract explicit.
    """
    assert len(RPC_FUNCTIONS) == 34


@pytest.mark.parametrize("name", sorted(RPC_FUNCTIONS))
def test_spec_function_is_the_same_named_service_function(name: str) -> None:
    """RPC_FUNCTIONS[name].function is the same object as services.<name>.

    Prevents accidental replication of logic across the adapter boundary.
    """
    assert RPC_FUNCTIONS[name].function is getattr(services, name)


@pytest.mark.parametrize("name", sorted(RPC_FUNCTIONS))
def test_spec_request_resolves_the_same_named_camel_case_request_model(
    name: str,
) -> None:
    """RPC_FUNCTIONS[name].request is schemas.<CamelCase>Request (e.g., InitGameDirRequest).

    Prevents request model typos and ensures the schema naming convention.
    """
    expected = getattr(schemas, f"{_camel(name)}Request")
    assert RPC_FUNCTIONS[name].request is expected


# Keyword-only parameters install_from_archive accepts for internal callers
# (install_mod fills them from the resolved plan) but does not expose on its
# RPC request; locate_directory likewise accepts search roots only in-process.
# Every other function's request fields exactly match its service parameters
# minus progress/client.
_INTERNAL_ONLY_PARAMS: dict[str, set[str]] = {
    "install_from_archive": {"forge_id", "forge_version_id", "source"},
    "locate_directory": {"roots"},
}


@pytest.mark.parametrize("name", sorted(RPC_FUNCTIONS))
def test_request_fields_match_service_parameters_minus_progress_and_client(
    name: str,
) -> None:
    """RPC request fields exactly match service function parameters, excluding
    progress/client callbacks and internal-only fields."""
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
    (ProfileSummary, schemas.ProfileSummary),
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
    (services.CleanupPlan, schemas.CleanupPlan),
    (services.CleanupResult, schemas.CleanupResult),
    (services.UntouchedResidue, schemas.UntouchedResidue),
    (LaunchResult, schemas.LaunchResult),
    (LaunchStatus, schemas.LaunchStatus),
    (StopResult, schemas.StopResult),
]


@pytest.mark.parametrize(
    "dataclass_type,schema_type",
    SCHEMA_PARITY_PAIRS,
    ids=[dataclass_type.__name__ for dataclass_type, _ in SCHEMA_PARITY_PAIRS],
)
def test_schema_declares_every_field_of_its_core_dataclass(dataclass_type: type, schema_type: type[BaseModel]) -> None:
    """Core dataclass fields ⊆ Pydantic schema fields (one-way coverage).

    A schema may have extra fields (e.g., operation_id), but all dataclass
    fields must be serializable through the schema.
    """
    dataclass_fields = {field.name for field in dataclasses.fields(dataclass_type)}
    schema_fields = set(schema_type.model_fields.keys())

    assert dataclass_fields <= schema_fields


# --------------------------------------------------------------------------
# Error mapping completeness.
# --------------------------------------------------------------------------


def _concrete_core_error_classes() -> list[type[Exception]]:
    """Every concrete exception class defined in ``sptmod.core.errors``."""
    return [
        obj
        for _, obj in vars(core_errors).items()
        if inspect.isclass(obj) and issubclass(obj, Exception) and obj.__module__ == core_errors.__name__
    ]


CONCRETE_CORE_ERROR_CLASSES = _concrete_core_error_classes()


def test_there_are_twentyfive_concrete_core_error_classes() -> None:
    """len(CONCRETE_CORE_ERROR_CLASSES) == 25 to pin corpus against regressions.

    Adding a new exception class to core.errors requires updating this count
    so the change is caught by parametrized error tests.
    """
    # Pins the corpus this test enumerates so a newly added exception class
    # is caught here rather than silently skipped by the parametrization.
    assert len(CONCRETE_CORE_ERROR_CLASSES) == 25


@pytest.mark.parametrize(
    "error_type",
    CONCRETE_CORE_ERROR_CLASSES,
    ids=[cls.__name__ for cls in CONCRETE_CORE_ERROR_CLASSES],
)
def test_every_concrete_core_error_maps_to_a_non_fallback_code(
    error_type: type[Exception],
) -> None:
    """error_code(error_type) is not INTERNAL_ERROR and error_type in ERROR_MAPPING.

    All core exceptions must have machine-readable codes; INTERNAL_ERROR
    is only for unhandled exceptions outside the core domain.
    """
    code = error_code(error_type)

    assert code != ErrorCode.INTERNAL_ERROR
    assert error_type in ERROR_MAPPING


def test_operation_in_progress_maps_to_its_own_specific_code() -> None:
    """OperationInProgressError maps to OPERATION_IN_PROGRESS code."""
    assert error_code(core_errors.OperationInProgressError) == "OPERATION_IN_PROGRESS"


# --------------------------------------------------------------------------
# operation_id / wrap bookkeeping.
# --------------------------------------------------------------------------


def test_exactly_the_responses_declaring_operation_id_are_marked_in_the_spec() -> None:
    """Specs with operation_id=True have operation_id field; unmarked specs do not.

    The five marked specs (install_from_archive, sync_catalog, install_mod,
    update_mod, launch_profile) are the only ones with long-running operations.
    """
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
        "launch_profile",
    }


def test_exactly_the_wrapped_specs_wrap_a_result_under_a_named_field() -> None:
    """Four specs wrap plain results: set_spt_version, export_mods, list/set_favorite.

    Wrapping ensures JSON responses are always objects; plain list/string results
    are nested under named fields (spt_version, content, mod_ids).
    """
    # Most wrapped results are plain strings; list_favorites/set_favorite wrap
    # a plain list[int] the same way, under "mod_ids".
    wrapped = {name: spec.wrap for name, spec in RPC_FUNCTIONS.items() if spec.wrap}

    assert wrapped == {
        "set_spt_version": "spt_version",
        "export_mods": "content",
        "list_favorites": "mod_ids",
        "set_favorite": "mod_ids",
    }
