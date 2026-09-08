"""The launch state file and the launch log of one game directory.

Both live in the game directory, because every short-lived process involved --
the call that starts the game, the calls that report its state and the separate
watcher -- only knows that directory.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Final

from sptmod.core.models import Json

LAUNCH_STATE_FILE_NAME: Final = ".sptmod-launch.json"
LAUNCH_LOG_FILE_NAME: Final = ".sptmod-launch.log"
LAUNCH_LOG_MAX_BYTES: Final = 200 * 1024
CLIENT_MODE: Final = "client"
_TEMP_PREFIX: Final = ".sptmod-launch-"
@dataclass(frozen=True)
class ProcessRef:
    """A remembered process: the pair survives a reused process number."""

    pid: int
    started_at: float


@dataclass(frozen=True)
class LaunchState:
    """The contents of the launch state file.

    ``failure`` names why the launch did not end up with a running game,
    empty while nothing went wrong. ``mode`` is :data:`CLIENT_MODE` when the
    game was started on another player's server and empty otherwise.
    ``adopted_client`` is the game the watcher found running for this launch,
    so it can still be closed when the process that started it is gone.
    """

    profile_id: str = ""
    stop_server_on_exit: bool = True
    server: ProcessRef | None = None
    server_started_by_sptmod: bool = False
    client: ProcessRef | None = None
    adopted_client: ProcessRef | None = None
    watcher: ProcessRef | None = None
    failure: str = ""
    mode: str = ""
def _state_file(path: Path) -> Path:
    """Return the launch state file of the installation at ``path``."""
    return path / LAUNCH_STATE_FILE_NAME
def read_state(path: Path) -> LaunchState:
    """Return the remembered launch state, empty when there is none.

    A missing, unreadable or damaged file means "nothing is running": the file
    is a hint, and the process table is the truth.
    """
    try:
        payload: Json = json.loads(_state_file(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return LaunchState()
    if not isinstance(payload, dict):
        return LaunchState()
    profile_id = payload.get("profile_id")
    server = payload.get("server")
    failure = payload.get("failure")
    mode = payload.get("mode")
    return LaunchState(
        profile_id=profile_id if isinstance(profile_id, str) else "",
        stop_server_on_exit=payload.get("stop_server_on_exit") is not False,
        server=_reference(server),
        server_started_by_sptmod=isinstance(server, dict)
        and server.get("started_by_sptmod") is True,
        client=_reference(payload.get("client")),
        adopted_client=_reference(payload.get("adopted_client")),
        watcher=_reference(payload.get("watcher")),
        failure=failure if isinstance(failure, str) else "",
        mode=mode if isinstance(mode, str) else "",
    )


def _reference(value: Json) -> ProcessRef | None:
    """Read one remembered process, ``None`` when the entry is unusable."""
    if not isinstance(value, dict):
        return None
    pid = value.get("pid")
    started_at = value.get("started_at")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if not isinstance(started_at, int | float) or isinstance(started_at, bool):
        return None
    return ProcessRef(pid=pid, started_at=float(started_at))
def write_state(path: Path, state: LaunchState) -> None:
    """Write the launch state of ``path`` in one piece.

    Raises:
        OSError: The game directory cannot be written.
    """
    payload: dict[str, object] = {
        "profile_id": state.profile_id,
        "stop_server_on_exit": state.stop_server_on_exit,
        "client": _reference_payload(state.client),
        "adopted_client": _reference_payload(state.adopted_client),
        "watcher": _reference_payload(state.watcher),
        "failure": state.failure,
        "mode": state.mode,
    }
    server = _reference_payload(state.server)
    if server is not None:
        server["started_by_sptmod"] = state.server_started_by_sptmod
    payload["server"] = server
    descriptor, temp_name = tempfile.mkstemp(dir=path, prefix=_TEMP_PREFIX)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(payload, temp_file)
        os.replace(temp_path, _state_file(path))
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _reference_payload(reference: ProcessRef | None) -> dict[str, object] | None:
    """Render one remembered process for the state file."""
    if reference is None:
        return None
    return {"pid": reference.pid, "started_at": reference.started_at}
def log(path: Path, message: str) -> None:
    """Append one line to the launch log of ``path``.

    A launch must not fail because its log cannot be written, so every failure
    of the file system is swallowed here.
    """
    log_file = path / LAUNCH_LOG_FILE_NAME
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with suppress(OSError):
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
        _trim_log(log_file)


@contextmanager
def log_stream(path: Path) -> Generator[IO[bytes] | int]:
    """Hand the error output of the game to the launch log of ``path``.

    Falls back to dropping that output when the log cannot be opened, because
    a launch must not fail over its own bookkeeping.
    """
    try:
        handle = (path / LAUNCH_LOG_FILE_NAME).open("ab")
    except OSError:
        yield subprocess.DEVNULL
        return
    try:
        yield handle
    finally:
        handle.close()


def _trim_log(log_file: Path) -> None:
    """Drop the oldest whole lines until the log fits its size limit."""
    if log_file.stat().st_size <= LAUNCH_LOG_MAX_BYTES:
        return
    _, _, kept = log_file.read_bytes()[-LAUNCH_LOG_MAX_BYTES:].partition(b"\n")
    log_file.write_bytes(kept)
def clear_state(path: Path) -> None:
    """Forget what was running in the installation at ``path``."""
    with suppress(OSError):
        _state_file(path).unlink(missing_ok=True)
