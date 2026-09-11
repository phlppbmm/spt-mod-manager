"""Command-line interface for sptmod.

The command groups live in sibling modules; importing them here is what
registers their commands on ``app``. The module names sort in the order typer
must print them in ``sptmod --help``.
"""

from sptmod.cli.base import (
    EXIT_CODES,
    GENERIC_EXIT_CODE,
    app,
    callback,
    console,
    error_console,
    local_app,
    main,
    spt_app,
)
from sptmod.cli.gamedir import init, version
from sptmod.cli.local import local_add, local_install, local_list, local_remove
from sptmod.cli.mods import (
    adopt,
    disable,
    enable,
    files,
    install,
    list_mods,
    remove,
    update,
)
from sptmod.cli.repo import export, import_mods, snapshot, status
from sptmod.cli.reports import check, history
from sptmod.cli.rpc import rpc
from sptmod.cli.sources import browse, search, sync
from sptmod.cli.spt import spt_downgrade, spt_list, spt_update

__all__ = [
    "EXIT_CODES",
    "GENERIC_EXIT_CODE",
    "adopt",
    "app",
    "browse",
    "callback",
    "check",
    "console",
    "disable",
    "enable",
    "error_console",
    "export",
    "files",
    "history",
    "import_mods",
    "init",
    "install",
    "list_mods",
    "local_add",
    "local_app",
    "local_install",
    "local_list",
    "local_remove",
    "main",
    "remove",
    "rpc",
    "search",
    "snapshot",
    "spt_app",
    "spt_downgrade",
    "spt_list",
    "spt_update",
    "status",
    "sync",
    "update",
    "version",
]
