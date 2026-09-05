"""Smoke test for the sptmod CLI."""

from typer.testing import CliRunner

from sptmod.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    """sptmod --help exits 0; result is the Typer-rendered usage output.

    Smoke test: ensures the CLI app is syntactically valid and doesn't crash
    on the basic --help flag.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
