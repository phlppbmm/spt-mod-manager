"""Contract models of the mod sets and of their service functions."""

from pathlib import Path

from sptmod.core import modsets as core_modsets

from .base import CoreModel


class ModSetEntry(CoreModel):
    """One mod of a mod set and the state the set gives it."""

    forge_id: str
    name: str
    version: str
    enabled: bool


class ModSet(CoreModel):
    """A named list of mods with the state each of them should have."""

    name: str
    created_at: str
    entries: list[ModSetEntry] = []


class ProfileBinding(CoreModel):
    """The mod set one player profile is played with."""

    profile_id: str
    set_name: str


class ModSetOverview(CoreModel):
    """The mod sets of one game directory and the profiles bound to them."""

    sets: list[ModSet] = []
    bindings: list[ProfileBinding] = []


class ModSetChange(CoreModel):
    """One mod a set would turn on or off, or names in another version."""

    name: str
    version: str
    installed_version: str = ""
    reason: str = ""


class ModSetPlan(CoreModel):
    """What applying a mod set would change."""

    name: str
    enable: list[ModSetChange] = []
    disable: list[ModSetChange] = []
    missing: list[ModSetEntry] = []
    different: list[ModSetChange] = []
    unchanged: list[str] = []


class ModSetApplyResult(CoreModel):
    """Outcome of ``apply_mod_set``."""

    name: str
    enabled: list[str] = []
    disabled: list[str] = []
    missing: list[str] = []
    notices: list[str] = []


class ModSetExport(CoreModel):
    """A mod set rendered for sharing, as file content and as a share code."""

    name: str
    content: str
    code: str


class ListModSetsRequest(CoreModel):
    """Parameters of ``list_mod_sets``."""

    path: Path
    store_path: Path | None = None


class SaveModSetRequest(CoreModel):
    """Parameters of ``save_mod_set``.

    Without ``entries`` the set is taken from the installed mods.
    """

    path: Path
    name: str
    entries: list[ModSetEntry] | None = None
    store_path: Path | None = None

    def service_kwargs(self) -> dict[str, object]:
        """Return the request with its mods as the core type the service takes."""
        kwargs = super().service_kwargs()
        if self.entries is not None:
            kwargs["entries"] = [
                core_modsets.ModSetEntry(
                    forge_id=entry.forge_id,
                    name=entry.name,
                    version=entry.version,
                    enabled=entry.enabled,
                )
                for entry in self.entries
            ]
        return kwargs


class DeleteModSetRequest(CoreModel):
    """Parameters of ``delete_mod_set``."""

    path: Path
    name: str
    store_path: Path | None = None


class RenameModSetRequest(CoreModel):
    """Parameters of ``rename_mod_set``."""

    path: Path
    name: str
    new_name: str
    store_path: Path | None = None


class ExportModSetRequest(CoreModel):
    """Parameters of ``export_mod_set``."""

    path: Path
    name: str
    store_path: Path | None = None


class ImportModSetRequest(CoreModel):
    """Parameters of ``import_mod_set``.

    Exactly one of ``set_file`` and ``content`` carries the mod set.
    """

    path: Path
    set_file: Path | None = None
    content: str | None = None
    name: str | None = None
    store_path: Path | None = None


class BindProfileSetRequest(CoreModel):
    """Parameters of ``bind_profile_set``.

    Without ``set_name`` the profile is unbound.
    """

    path: Path
    profile_id: str
    set_name: str | None = None
    store_path: Path | None = None


class PlanModSetRequest(CoreModel):
    """Parameters of ``plan_mod_set``."""

    path: Path
    name: str
    store_path: Path | None = None


class ApplyModSetRequest(CoreModel):
    """Parameters of ``apply_mod_set``.

    ``operation_id`` names the progress stream of this change; a generated one
    is used and echoed back when the caller sends none.
    """

    path: Path
    name: str
    ignore_missing: bool = False
    store_path: Path | None = None
    operation_id: str | None = None


class ApplyModSetResponse(ModSetApplyResult):
    """Outcome of ``apply_mod_set`` plus its progress operation ID."""

    operation_id: str
