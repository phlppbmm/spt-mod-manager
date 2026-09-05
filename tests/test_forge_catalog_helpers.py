"""Tests for the standalone helpers of forge/catalog.py.

is_compatible/is_newer are already implemented (not stubs); these are the
declaration checks that must pass. default_db_path() only checks path shape,
per the task's parallel-work constraints, and must never create the file.
"""

import pytest

from sptmod.core.forge.catalog import (
    APP_NAME,
    DB_FILE_NAME,
    default_db_path,
    is_compatible,
    is_newer,
)


@pytest.mark.parametrize(
    ("constraint", "spt_version", "expected"),
    [
        ("^3.8.0", "3.8.5", True),
        # Caret bumps at the first non-zero part (major=3), so ^3.8.0 allows
        # the whole 3.x range up to (but excluding) 4.0.0.
        ("^3.8.0", "3.9.0", True),
        ("^3.8.0", "4.0.0", False),
        # An absent constraint means "unknown", not "works with everything":
        # old hub imports ship versions without one and must not pass a
        # compatibility filter.
        ("", "3.8.5", False),
        ("   ", "3.8.5", False),
    ],
)
def test_is_compatible_matches_semver_constraint(
    constraint: str, spt_version: str, expected: bool
) -> None:
    """is_compatible() enforces semver caret ranges; empty constraint is unknown."""
    assert is_compatible(constraint, spt_version) is expected


def test_is_compatible_returns_false_for_unsupported_range_syntax() -> None:
    """Constraint syntax outside the supported semver range (e.g. "||") returns
    False instead of raising; the function gracefully downgrades invalid syntax
    to a mismatch, preventing a parse error from breaking a compatibility check.
    """
    # "||" is explicitly outside the semver matcher's supported syntax and
    # raises UnsupportedRangeError; is_compatible must swallow it as False.
    assert is_compatible("^3.8.0 || ^4.0.0", "3.8.5") is False


@pytest.mark.parametrize(
    ("candidate", "installed", "expected"),
    [
        ("1.1.0", "1.0.0", True),
        ("1.0.0", "1.0.0", False),
        ("1.0.0", "1.1.0", False),
        ("2.0.0", "1.9.9", True),
    ],
)
def test_is_newer_compares_versions_strictly(
    candidate: str, installed: str, expected: bool
) -> None:
    """is_newer() returns True only if candidate is strictly greater than installed."""
    assert is_newer(candidate, installed) is expected


def test_is_newer_returns_false_for_unparseable_candidate() -> None:
    """A candidate version "bad-version" that cannot be parsed as semver
    returns False from is_newer(), not raising UnsupportedRangeError; this
    prevents version comparison errors from breaking version resolution.
    """
    # "bad-version" has no numeric parts and is not a dotted numeric version;
    # is_newer must swallow the resulting UnsupportedRangeError as False.
    assert is_newer("bad-version", "1.0.0") is False


def test_default_db_path_has_expected_file_name() -> None:
    """default_db_path() returns a path whose final component is the constant
    DB_FILE_NAME ("forge-catalog.db"); this ensures the constant and the
    implementation stay synchronized for both tests and production code.
    """
    path = default_db_path()

    assert path.name == DB_FILE_NAME == "forge-catalog.db"


def test_default_db_path_lives_under_the_app_config_directory() -> None:
    """default_db_path() returns a path whose immediate parent directory is
    named "sptmod" (APP_NAME), and "sptmod" appears in the full path;
    this ensures the catalog is placed in the app's config directory, not
    some shared or system location.
    """
    path = default_db_path()

    assert APP_NAME in path.parts
    assert path.parent.name == APP_NAME
