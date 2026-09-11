"""Contract models of Fika, of launching the game and of the SPT releases."""

from datetime import datetime
from pathlib import Path

from sptmod.core.services import FikaRole, ModSetMode

from .base import CoreModel
from .mods import InstallPlan, ModFile


class FikaSettings(CoreModel):
    """The Fika role of one game directory and the host it plays on."""

    role: FikaRole
    host_address: str = ""
    base_url: str = ""


class HostProfile(CoreModel):
    """One player profile a Fika host offers to the players joining it."""

    profile_id: str
    username: str
    nickname: str = ""
    level: int = 0
    side: str = ""
    has_password: bool = False


class FikaInstallPlan(CoreModel):
    """What playing co-op needs: the two halves of Fika and their one plan."""

    server_mod_id: int
    client_mod_id: int
    plan: InstallPlan


class GetFikaSettingsRequest(CoreModel):
    """Parameters of ``get_fika_settings``."""

    path: Path
    fika_path: Path | None = None


class SetFikaSettingsRequest(CoreModel):
    """Parameters of ``set_fika_settings``."""

    path: Path
    role: FikaRole = "none"
    host_address: str = ""
    fika_path: Path | None = None


class ListHostProfilesRequest(CoreModel):
    """Parameters of ``list_host_profiles``."""

    path: Path
    fika_path: Path | None = None


class PlanFikaInstallRequest(CoreModel):
    """Parameters of ``plan_fika_install``."""

    path: Path
    db_path: Path | None = None


class LaunchProfileRequest(CoreModel):
    """Parameters of ``launch_profile``.

    ``operation_id`` names the progress stream of this launch; a generated one
    is used and echoed back when the caller sends none.
    """

    path: Path
    profile_id: str
    stop_server_on_exit: bool = True
    mod_set: ModSetMode = "apply"
    store_path: Path | None = None
    fika_path: Path | None = None
    operation_id: str | None = None


class LaunchResult(CoreModel):
    """What one launch started."""

    profile_id: str
    nickname: str
    server_pid: int
    client_pid: int
    server_started: bool


class LaunchProfileResponse(LaunchResult):
    """Outcome of ``launch_profile`` plus its progress operation ID."""

    operation_id: str


class LaunchStatusRequest(CoreModel):
    """Parameters of ``launch_status``."""

    path: Path


class LaunchStatus(CoreModel):
    """What of the installation is running right now.

    The ``_stoppable`` flags say that sptmod can close this process: it started
    it and it is still that same process. A process found only by its name, and
    a server that was already running when the launch began, are reported but
    never closed.
    """

    server_running: bool
    client_running: bool
    server_pid: int
    client_pid: int
    profile_id: str
    server_stoppable: bool
    client_stoppable: bool


class StopGameRequest(CoreModel):
    """Parameters of ``stop_game``."""

    path: Path


class StopResult(CoreModel):
    """What stopping the game closed, and how to say so to the player."""

    server_stopped: bool
    client_stopped: bool
    message: str


class ListSptReleasesRequest(CoreModel):
    """Parameters of ``list_spt_releases``."""

    path: Path


class SptReleaseInfo(CoreModel):
    """One published SPT release, seen from the installed one."""

    version: str
    client_build: str
    published_at: datetime
    url: str
    downloadable: bool
    content_length: int | None
    installed: bool
    newer: bool


class SptReleaseList(CoreModel):
    """What ``list_spt_releases`` found for a game directory."""

    current_version: str = ""
    client_build: str = ""
    releases: list[SptReleaseInfo] = []
    latest: str = ""


class CheckSptCompatibilityRequest(CoreModel):
    """Parameters of ``check_spt_compatibility``."""

    path: Path
    version: str
    db_path: Path | None = None


class SptCompatibilityReport(CoreModel):
    """How the installed mods stand to an SPT version the game could change to."""

    spt_version: str
    compatible: list[str] = []
    incompatible: list[str] = []
    skipped: list[str] = []
    checked: bool = True
    notice: str = ""


class ApplySptReleaseRequest(CoreModel):
    """Parameters of ``apply_spt_release``.

    ``operation_id`` names the progress stream of this version change; a
    generated one is used and echoed back when the caller sends none.
    ``allow_incompatible_mods`` changes the version even when installed mods
    have no version for it.
    """

    path: Path
    version: str
    db_path: Path | None = None
    allow_incompatible_mods: bool = False
    operation_id: str | None = None


class SptApplyResult(CoreModel):
    """Outcome of an SPT version change."""

    previous_version: str
    version: str
    commit: str = ""
    files: list[ModFile] = []


class ApplySptReleaseResponse(SptApplyResult):
    """Outcome of ``apply_spt_release`` plus its progress operation ID."""

    operation_id: str


class RevertSptUpdateRequest(CoreModel):
    """Parameters of ``revert_spt_update``."""

    path: Path
