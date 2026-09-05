"""Smoke test for the sptmod CLI."""

from typer.testing import CliRunner

from sptmod.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    """--help exits 0 and outputs the command interface."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
