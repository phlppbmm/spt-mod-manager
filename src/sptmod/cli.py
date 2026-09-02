"""Command-line interface for sptmod."""

import typer

app = typer.Typer(help="sptmod: mod manager for SPT (Single Player Tarkov).")


@app.callback()
def callback() -> None:
    """sptmod command-line interface."""


@app.command()
def version() -> None:
    """Print the sptmod version."""
    typer.echo("sptmod 0.1.0")


def main() -> None:
    """Entry point for the sptmod console script."""
    app()
