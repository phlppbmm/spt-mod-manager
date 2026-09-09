"""The hidden ``rpc`` command that drives one service function over NDJSON."""

from typing import Annotated

import typer

from sptmod.cli.base import app
from sptmod.server.rpc import main as run_rpc


@app.command(hidden=True)
def rpc(
    name: Annotated[str, typer.Argument(help="Name of the service function to call.")],
) -> None:
    """Call a service function with a JSON request on stdin.

    Writes NDJSON to stdout: one line per progress report, then one result or
    error line. Meant for a driver process, not for interactive use.
    """
    raise typer.Exit(run_rpc(name))
