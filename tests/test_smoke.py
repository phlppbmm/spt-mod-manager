"""Smoke test for the sptmod CLI."""

import re

from typer.testing import CliRunner

from sptmod.cli import app

runner = CliRunner()

_COMMAND_LINE = re.compile(r"^\S (\w[\w-]*)\s")


def command_names(help_output: str) -> list[str]:
    """Return the command names listed in the Commands panel of a help screen."""
    names: list[str] = []
    in_commands = False
    for line in help_output.splitlines():
        if "Commands" in line and not in_commands:
            in_commands = True
            continue
        if not in_commands:
            continue
        match = _COMMAND_LINE.match(line)
        if match is not None:
            names.append(match.group(1))
    return names


def test_cli_help() -> None:
    """sptmod --help exits 0; result is the Typer-rendered usage output.

    Smoke test: ensures the CLI app is syntactically valid and doesn't crash
    on the basic --help flag.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_cli_help_lists_the_commands_in_the_intended_order() -> None:
    """``sptmod --help`` prints the commands from setup over daily work to Forge.

    The registration order is an implicit consequence of the alphabetical import
    order of the command modules, so renaming one of them silently reorders the
    help screen; this pins the order a reader of the help is meant to see. The
    hidden ``rpc`` command must stay out of it, since it is machine-facing.
    """
    result = runner.invoke(app, ["--help"])

    assert command_names(result.output) == [
        "version",
        "init",
        "install",
        "update",
        "list",
        "files",
        "remove",
        "disable",
        "enable",
        "adopt",
        "status",
        "snapshot",
        "export",
        "import",
        "check",
        "history",
        "sync",
        "search",
        "browse",
        "spt",
    ]


def test_cli_spt_help_lists_the_version_commands_in_the_intended_order() -> None:
    """``sptmod spt --help`` prints listing before the two version changes.

    Same reason as for the top-level help: the order is worth pinning because
    nothing in the code states it.
    """
    result = runner.invoke(app, ["spt", "--help"])

    assert command_names(result.output) == ["list", "update", "downgrade"]
