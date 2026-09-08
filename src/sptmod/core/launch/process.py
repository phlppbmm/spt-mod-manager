"""Starting, finding and closing the processes one launch is made of."""

import subprocess
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import IO, Final

import psutil

from sptmod.core import game_dir
from sptmod.core.errors import LaunchError
from sptmod.core.game_dir import GameProcess
from sptmod.core.launch.state import ProcessRef

TERMINATE_TIMEOUT: Final = 10.0
def _open_detached(
    argv: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    stderr: IO[bytes] | int,
) -> subprocess.Popen[bytes]:
    """Start ``argv`` detached from this process and return the raw handle.

    The child gets its own session (or, on Windows, its own process group and
    no console), so it neither receives the signals of the caller nor leaves a
    window behind, and it reads and writes nothing except what ``stderr``
    names. The handle is deliberately not closed: the process must outlive
    this one.
    """
    if sys.platform == "win32":
        return subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW,
        )
    return subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        start_new_session=True,
    )


def spawn_detached(
    argv: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    *,
    stderr: IO[bytes] | int = subprocess.DEVNULL,
) -> subprocess.Popen[bytes]:
    """Start ``argv`` so it keeps running after this process is gone."""
    try:
        return _open_detached(argv, cwd, environment, stderr)
    except OSError as error:
        raise LaunchError(f"{argv[0]} could not be started: {error}") from error


def process_ref(process: subprocess.Popen[bytes]) -> ProcessRef:
    """Remember a freshly started process by number and start time."""
    return ProcessRef(pid=process.pid, started_at=_started_at(process.pid))


def _started_at(pid: int) -> float:
    """Return the start time of ``pid``, or ``0.0`` when it is already gone."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return 0.0


def terminate(reference: ProcessRef, timeout: float = TERMINATE_TIMEOUT) -> bool:
    """Close the remembered process and everything it started.

    Returns whether the process was still the remembered one and got asked to
    close; survivors of the friendly request are killed. Whether it still runs
    is the one question :func:`live_ref` answers, so what a status call calls
    stoppable is what this closes; a process that has ended and only waits to
    be reaped counts as gone for both.

    Only descendants that began no earlier than the remembered process are
    closed with it. Something the launch really started cannot predate the
    process that started it, so an older one below it in the tree is a
    stranger that inherited a process number, and it is left alone.
    """
    try:
        process = psutil.Process(reference.pid)
        targets = [
            *_descendants_since(process, reference.started_at),
            process,
        ]
    except psutil.Error:
        return False
    if live_ref(reference) is None:
        return False
    for target in targets:
        with suppress(psutil.Error):
            target.terminate()
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for survivor in alive:
        with suppress(psutil.Error):
            survivor.kill()
    return True


def _descendants_since(
    process: psutil.Process, started_at: float
) -> list[psutil.Process]:
    """Return the processes below ``process`` that began no earlier than ``started_at``."""
    children = process.children(recursive=True)
    return [child for child in children if _started_at(child.pid) >= started_at]


def live_ref(reference: ProcessRef | None) -> ProcessRef | None:
    """Return ``reference`` while it still names the process it was made for."""
    if reference is None or not game_dir.process_alive(
        reference.pid, reference.started_at
    ):
        return None
    return reference


def running_pid(
    reference: ProcessRef | None, running: Sequence[GameProcess], kind: str
) -> int:
    """Return the process number of the running server or client, or ``0``.

    The remembered process is the fast, certain answer; the process table is
    asked for a process of ``kind`` when that one is gone, so a game hosted by
    Wine is found even after the program that started it exited.
    """
    reference = live_ref(reference) or running_ref(running, kind)
    return reference.pid if reference is not None else 0


def running_ref(running: Sequence[GameProcess], kind: str) -> ProcessRef | None:
    """Return the first scanned process of ``kind`` as a remembered process."""
    for process in running:
        if process.kind == kind:
            return ProcessRef(pid=process.pid, started_at=process.started_at)
    return None
