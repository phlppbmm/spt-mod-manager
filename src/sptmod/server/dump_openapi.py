"""Build the OpenAPI schema of the API from pydantic and write it to disk.

The document is assembled by hand from the request and response models, so it
stays generatable without a web framework.
"""

import json
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import JsonSchemaMode

from sptmod.server import schemas

USAGE = "usage: python -m sptmod.server.dump_openapi [outfile]"

DEFAULT_TARGET = Path("openapi.json")

PACKAGE_NAME = "sptmod"
DESCRIPTION = "Request and response contract of the sptmod core services."
REF_TEMPLATE = "#/components/schemas/{model}"

# The response slot holds parametrised generics such as ``list[ModInfo]``
# alongside model classes; pydantic types the same position as ``Any``.
ROUTES: Final[list[tuple[str, type[BaseModel], Any]]] = [
    ("list_directories", schemas.ListDirectoriesRequest, schemas.DirectoryListing),
    (
        "locate_directory",
        schemas.LocateDirectoryRequest,
        list[schemas.LocatedDirectory],
    ),
    ("init_game_dir", schemas.InitGameDirRequest, schemas.InitResult),
    ("set_spt_version", schemas.SetSptVersionRequest, schemas.SetSptVersionResponse),
    (
        "install_from_archive",
        schemas.InstallFromArchiveRequest,
        schemas.InstallFromArchiveResponse,
    ),
    ("list_mods", schemas.ListModsRequest, list[schemas.ModInfo]),
    ("mod_files", schemas.ModFilesRequest, list[schemas.ModFile]),
    ("remove_mod", schemas.RemoveModRequest, schemas.RemoveResult),
    ("check_profiles", schemas.CheckProfilesRequest, schemas.CheckResult),
    ("history", schemas.HistoryRequest, list[schemas.CommitInfo]),
    ("sync_catalog", schemas.SyncCatalogRequest, schemas.SyncCatalogResponse),
    ("search_catalog", schemas.SearchCatalogRequest, list[schemas.CatalogEntry]),
    ("browse_catalog", schemas.BrowseCatalogRequest, list[schemas.CatalogEntry]),
    ("disable_mod", schemas.DisableModRequest, schemas.ToggleResult),
    ("enable_mod", schemas.EnableModRequest, schemas.ToggleResult),
    ("adopt_mod", schemas.AdoptModRequest, schemas.AdoptResult),
    ("status", schemas.StatusRequest, schemas.StatusReport),
    ("snapshot", schemas.SnapshotRequest, schemas.SnapshotResult),
    ("export_mods", schemas.ExportModsRequest, schemas.ExportModsResponse),
    ("import_list", schemas.ImportListRequest, schemas.ImportDiff),
    ("clear_catalog_cache", schemas.ClearCatalogCacheRequest, schemas.ClearCacheResult),
    ("get_mod_details", schemas.GetModDetailsRequest, schemas.ModDetails),
    ("plan_mod_install", schemas.PlanModInstallRequest, schemas.InstallPlan),
    ("install_mod", schemas.InstallModRequest, schemas.InstallModResponse),
    ("check_updates", schemas.CheckUpdatesRequest, schemas.UpdatesReport),
    ("update_mod", schemas.UpdateModRequest, schemas.UpdateModResponse),
    ("list_favorites", schemas.ListFavoritesRequest, schemas.FavoritesResponse),
    ("set_favorite", schemas.SetFavoriteRequest, schemas.FavoritesResponse),
]

STANDALONE: Final[list[tuple[str, Any]]] = [
    ("ErrorResponse", schemas.ErrorResponse),
    ("ProgressEvent", schemas.ProgressEvent),
]


def build_openapi() -> dict[str, Any]:
    """Return the OpenAPI document describing every API route."""
    inputs: list[tuple[str, JsonSchemaMode, TypeAdapter[Any]]] = [
        (name, "serialization", TypeAdapter(model)) for name, model in STANDALONE
    ]
    for name, request, response in ROUTES:
        inputs.append((f"{name}.request", "validation", TypeAdapter(request)))
        inputs.append((f"{name}.response", "serialization", TypeAdapter(response)))
    schemas_by_key, definitions = TypeAdapter.json_schemas(
        inputs, ref_template=REF_TEMPLATE
    )
    components: dict[str, Any] = definitions.get("$defs", {})

    paths: dict[str, Any] = {}
    for name, _, _ in ROUTES:
        paths[f"/api/{name}"] = _post_operation(
            name,
            schemas_by_key[(f"{name}.request", "validation")],
            schemas_by_key[(f"{name}.response", "serialization")],
        )

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": PACKAGE_NAME,
            "description": DESCRIPTION,
            "version": version(PACKAGE_NAME),
        },
        "paths": paths,
        "components": {"schemas": dict(sorted(components.items()))},
    }
    return _without_none(document)


def _post_operation(
    name: str, request_schema: dict[str, Any], response_schema: dict[str, Any]
) -> dict[str, Any]:
    operation_id = f"{name}_api_{name}_post"
    if "$ref" not in response_schema:
        response_schema = response_schema | {
            "title": _title(f"Response_{operation_id}")
        }
    return {
        "post": {
            "summary": _title(name),
            "operationId": operation_id,
            "requestBody": {
                "content": {"application/json": {"schema": request_schema}},
                "required": True,
            },
            "responses": {
                "200": _json_response("Successful Response", response_schema),
                "default": _error_response(),
            },
        }
    }


def _json_response(description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": schema}},
    }


def _error_response() -> dict[str, Any]:
    return _json_response(
        "Default Response", {"$ref": REF_TEMPLATE.format(model="ErrorResponse")}
    )


def _title(name: str) -> str:
    return " ".join(word.capitalize() for word in name.split("_"))


def _without_none(document: dict[str, Any]) -> dict[str, Any]:
    """Return ``document`` with every ``None`` mapping entry dropped."""
    return {
        key: _stripped(value) for key, value in document.items() if value is not None
    }


def _stripped(value: Any) -> Any:
    if isinstance(value, dict):
        return _without_none(value)
    if isinstance(value, list):
        return [_stripped(item) for item in value]
    return value


def dump(target: Path) -> None:
    """Write the OpenAPI schema of the application to ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_openapi(), indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str]) -> int:
    """Run the dump for the command line arguments ``argv``."""
    if len(argv) > 1:
        print(USAGE, file=sys.stderr)
        return 2
    dump(Path(argv[0]) if argv else DEFAULT_TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
