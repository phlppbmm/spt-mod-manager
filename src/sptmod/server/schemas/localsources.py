"""Contract models of the local mod sources and of their service functions."""

from datetime import datetime
from pathlib import Path

from .base import CoreModel
from .mods import Conflict, ModInfo


class OfferedMod(CoreModel):
    """One mod a project folder offers, in its newest build."""

    name: str
    version: str
    archive: Path
    built_at: datetime
    mod: ModInfo | None = None


class LocalSourceInfo(CoreModel):
    """A registered project folder with the mods it offers right now."""

    folder: Path
    added_at: str
    mods: list[OfferedMod] = []


class LocalInstallResult(CoreModel):
    """Outcome of ``install_local_source``."""

    mod: ModInfo
    archive: Path
    replaced: bool = False
    files: list[Path] = []
    warnings: list[Conflict] = []


class AddLocalSourceRequest(CoreModel):
    """Parameters of ``add_local_source``."""

    path: Path
    folder: Path
    store_path: Path | None = None


class ListLocalSourcesRequest(CoreModel):
    """Parameters of ``list_local_sources``."""

    path: Path
    store_path: Path | None = None


class RemoveLocalSourceRequest(CoreModel):
    """Parameters of ``remove_local_source``."""

    path: Path
    folder: Path
    store_path: Path | None = None


class InstallLocalSourceRequest(CoreModel):
    """Parameters of ``install_local_source``.

    ``mod`` names one of the mods the folder offers. ``operation_id`` names the
    progress stream of this install; a generated one is used and echoed back
    when the caller sends none.
    """

    path: Path
    folder: Path
    mod: str
    store_path: Path | None = None
    operation_id: str | None = None


class InstallLocalSourceResponse(LocalInstallResult):
    """Outcome of ``install_local_source`` plus its progress operation ID."""

    operation_id: str
