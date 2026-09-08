"""Starting the SPT server and the game client for one player profile.

This is the second module allowed to use ``subprocess``: it starts programs
that outlive the call, with an environment shaped like the one the official SPT
launcher builds, which is the opposite of the short, captured git calls of
:mod:`sptmod.core.repo`.

What runs is remembered in ``.sptmod-launch.json`` inside the game directory,
next to the apply lock, because every short-lived process involved — the call
that starts the game, the calls that report its state, and the separate watcher
that notices the game closing — only knows the game directory. The file is a
hint: the process table decides what is really running.

What happened during a launch is appended to ``.sptmod-launch.log`` beside it,
because the game and the server are started with all their streams closed: the
log is the only trace a launch that went wrong leaves behind.
"""

from sptmod.core.launch.launcher import (
    LAUNCH_LOCK_FILE_NAME,
    POLL_SECONDS,
    READY_TIMEOUT,
    LaunchResult,
    LaunchStep,
    launch_profile,
)
from sptmod.core.launch.process import TERMINATE_TIMEOUT
from sptmod.core.launch.spawn import (
    CLIENT_ARGS_HEAD,
    CLIENT_EXIT_GRACE_SECONDS,
    tokenize_launch_settings,
)
from sptmod.core.launch.state import (
    CLIENT_MODE,
    LAUNCH_LOG_FILE_NAME,
    LAUNCH_LOG_MAX_BYTES,
    LAUNCH_STATE_FILE_NAME,
)
from sptmod.core.launch.status import (
    LaunchStatus,
    StopResult,
    launch_status,
    stop_game,
)
from sptmod.core.launch.watch import (
    CLIENT_NEVER_STARTED,
    WATCH_GRACE_SECONDS,
    WATCH_POLL_SECONDS,
    watch_game,
)

__all__ = [
    "CLIENT_ARGS_HEAD",
    "CLIENT_EXIT_GRACE_SECONDS",
    "CLIENT_MODE",
    "CLIENT_NEVER_STARTED",
    "LAUNCH_LOCK_FILE_NAME",
    "LAUNCH_LOG_FILE_NAME",
    "LAUNCH_LOG_MAX_BYTES",
    "LAUNCH_STATE_FILE_NAME",
    "POLL_SECONDS",
    "READY_TIMEOUT",
    "TERMINATE_TIMEOUT",
    "WATCH_GRACE_SECONDS",
    "WATCH_POLL_SECONDS",
    "LaunchResult",
    "LaunchStatus",
    "LaunchStep",
    "StopResult",
    "launch_profile",
    "launch_status",
    "stop_game",
    "tokenize_launch_settings",
    "watch_game",
]
