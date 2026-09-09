"""Tests for the standalone helpers of ``sptmod.core.forge.catalog``.

Covers the compatibility and newer-version predicates the catalog filters with,
and the default database location. ``default_db_path()`` must only report a
location, never create the file.
"""

from pathlib import Path

import pytest

from sptmod.core.forge.catalog import default_db_path, is_compatible, is_newer


@pytest.mark.parametrize(
    ("constraint", "spt_version", "expected"),
    [
        pytest.param("^3.8.0", "3.8.5", True, id="caret-allows-a-later-patch"),
        pytest.param("^3.8.0", "3.9.0", True, id="caret-allows-a-later-minor"),
        pytest.param("^3.8.0", "4.0.0", False, id="caret-stops-at-the-next-major"),
        pytest.param("", "3.8.5", False, id="empty-constraint-is-unknown"),
        pytest.param("   ", "3.8.5", False, id="blank-constraint-is-unknown"),
        pytest.param("^3.8.0 || ^4.0.0", "3.8.5", False, id="unsupported-or-syntax"),
    ],
)
def test_is_compatible_matches_semver_constraint(constraint: str, spt_version: str, expected: bool) -> None:
    """A version passes the filter only when the constraint clearly allows it.

    Feeds one constraint and one SPT version per row and asserts the answer.
    Reason: the caret bumps at the first non-zero part, so ``^3.8.0`` covers the
    whole 3.x range but not 4.0.0. A missing constraint means "unknown", not
    "works with everything" — old hub imports ship versions without one and must
    not pass the filter. Syntax the matcher does not support (``||``) must be
    answered with False instead of letting the parse error break a filter run.
    """
    assert is_compatible(constraint, spt_version) is expected


@pytest.mark.parametrize(
    ("candidate", "installed", "expected"),
    [
        pytest.param("1.1.0", "1.0.0", True, id="candidate-is-a-later-minor"),
        pytest.param("1.0.0", "1.0.0", False, id="same-version-is-not-newer"),
        pytest.param("1.0.0", "1.1.0", False, id="candidate-is-older"),
        pytest.param("2.0.0", "1.9.9", True, id="candidate-is-a-later-major"),
        pytest.param("bad-version", "1.0.0", False, id="unparseable-candidate"),
    ],
)
def test_is_newer_compares_versions_strictly(candidate: str, installed: str, expected: bool) -> None:
    """An update is offered only for a strictly greater, parseable version.

    Compares a candidate against an installed version per row and asserts the
    answer. Reason: an equal version must not be advertised as an update, and a
    candidate without numeric parts ("bad-version") must degrade to False rather
    than crash the update check.
    """
    assert is_newer(candidate, installed) is expected


def test_the_default_catalog_database_lives_next_to_the_other_settings_of_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalog database is reported inside the app's configuration directory.

    Redirects the configuration home and asserts the full reported path, and that
    nothing is created there. Reason: the assertion must name the file the app
    writes rather than the developer's real one, and merely asking for the
    location must not leave a database behind.
    """
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    path = default_db_path()

    assert path == config_home / "sptmod" / "forge-catalog.db"
    assert not path.exists()
