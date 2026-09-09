"""Pin the CLI's reported version to the single source of truth.

``[project].version`` in ``pyproject.toml`` is the only place the release
version is written; this module asserts that ``sptmod version`` reports that
value rather than a hard-coded string.
"""

import tomllib
from pathlib import Path
from typing import cast

from typer.testing import CliRunner

from sptmod.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]

runner = CliRunner()


def _pyproject_version() -> str:
    """Read ``[project].version`` from the repository's ``pyproject.toml``."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    project = cast(dict[str, object], data["project"])
    version = project["version"]
    assert isinstance(version, str)
    return version


def test_cli_version_command_reports_the_pyproject_version() -> None:
    """If ``sptmod version`` printed a hard-coded or stale string, this
    fails: it would not contain the version read live from
    ``pyproject.toml``."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert f"sptmod {_pyproject_version()}" in result.stdout
