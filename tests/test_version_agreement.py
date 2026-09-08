"""Pin the single source of truth for the release version.

The release version must have exactly one source (``[project].version`` in
``pyproject.toml``) so packaged artefacts cannot drift from it: the Python
package's ``__version__``, the Tauri desktop crate's ``Cargo.toml`` version,
the CLI's ``sptmod version`` output, and the absence of a competing
``version`` key in ``tauri.conf.json`` (Tauri v2 falls back to reading
``Cargo.toml`` only when that key is missing) must all agree.
"""

import json
import tomllib
from pathlib import Path
from typing import cast

from typer.testing import CliRunner

from sptmod import __version__
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


def test_python_package_version_matches_pyproject() -> None:
    """If ``sptmod.__version__`` were hand-edited out of sync with
    ``pyproject.toml``, this fails: the two strings would differ."""
    assert __version__ == _pyproject_version()


def test_tauri_crate_version_matches_pyproject() -> None:
    """If ``src-tauri/Cargo.toml``'s ``[package].version`` drifted from
    ``pyproject.toml``, this fails: the bundled desktop app would report a
    different version than the Python core it ships."""
    with (REPO_ROOT / "src-tauri" / "Cargo.toml").open("rb") as handle:
        cargo = tomllib.load(handle)
    package = cast(dict[str, object], cargo["package"])
    cargo_version = package["version"]
    assert isinstance(cargo_version, str)

    assert cargo_version == _pyproject_version()


def test_tauri_conf_declares_no_competing_version_key() -> None:
    """Tauri v2 reads the app version from ``Cargo.toml`` only when
    ``tauri.conf.json`` has no ``version`` key of its own. If a ``version``
    key were reintroduced there, this fails: it would silently override
    ``Cargo.toml`` and become a second, driftable source of truth."""
    with (REPO_ROOT / "src-tauri" / "tauri.conf.json").open(encoding="utf-8") as handle:
        conf = json.load(handle)

    assert "version" not in conf


def test_cli_version_command_reports_the_pyproject_version() -> None:
    """If ``sptmod version`` printed a hard-coded or stale string, this
    fails: it would not contain the version read live from
    ``pyproject.toml``."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert f"sptmod {_pyproject_version()}" in result.stdout
