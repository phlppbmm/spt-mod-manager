"""Tests for package.json parsing, DLL fallbacks, trailer mapping and semver ranges."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sptmod.core.models import MOD_TRAILER_KEYS, CommitInfo, ModInfo
from sptmod.core.modmeta import (
    InvalidMetadataError,
    ModMeta,
    UnsupportedRangeError,
    build_trailers,
    meta_from_dll,
    mod_info_from_commit,
    read_package_json,
    version_matches,
)


def _write_package_json(mod_dir: Path, content: dict[str, object]) -> None:
    """Write a package.json file to the mod directory."""
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "package.json").write_text(json.dumps(content), encoding="utf-8")


# --- read_package_json -------------------------------------------------------


def test_read_package_json_full_metadata(tmp_path: Path) -> None:
    """read_package_json parses all optional fields from package.json correctly.

    Creates mod_dir/SAIN/package.json with name, version, sptVersion (~4.1.0),
    author, guid, and dependencies (BigBrain: ^1.0.0). read_package_json returns
    ModMeta with all fields populated matching the input exactly.
    """
    mod_dir = tmp_path / "SAIN"
    _write_package_json(
        mod_dir,
        {
            "name": "SAIN",
            "version": "3.2.1",
            "sptVersion": "~4.1.0",
            "author": "Solarint",
            "guid": "com.sain.mod",
            "dependencies": {"BigBrain": "^1.0.0"},
        },
    )

    meta = read_package_json(mod_dir)

    assert meta == ModMeta(
        name="SAIN",
        version="3.2.1",
        spt_range="~4.1.0",
        author="Solarint",
        guid="com.sain.mod",
        dependencies={"BigBrain": "^1.0.0"},
    )


def test_read_package_json_minimal_metadata_uses_defaults(tmp_path: Path) -> None:
    """Mandatory fields only; optional fields default to empty."""
    mod_dir = tmp_path / "MinimalMod"
    _write_package_json(mod_dir, {"name": "MinimalMod", "version": "1.0.0"})

    meta = read_package_json(mod_dir)

    assert meta == ModMeta(name="MinimalMod", version="1.0.0")
    assert meta.spt_range == ""
    assert meta.author == ""
    assert meta.guid == ""
    assert not meta.dependencies


def test_read_package_json_parses_dependencies_dict(tmp_path: Path) -> None:
    """Parse dependencies dict with multiple mod-name to semver-range mappings."""
    mod_dir = tmp_path / "DependentMod"
    _write_package_json(
        mod_dir,
        {
            "name": "DependentMod",
            "version": "2.0.0",
            "dependencies": {"BigBrain": "^1.0.0", "Waypoints": "~2.1.0"},
        },
    )

    meta = read_package_json(mod_dir)

    assert meta.dependencies == {"BigBrain": "^1.0.0", "Waypoints": "~2.1.0"}


def test_read_package_json_missing_file_raises(tmp_path: Path) -> None:
    """Missing package.json raises InvalidMetadataError."""
    mod_dir = tmp_path / "NoPackageJson"
    mod_dir.mkdir()

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


@pytest.mark.parametrize(
    "content",
    [
        {"version": "1.0.0"},
        {"name": "", "version": "1.0.0"},
        {"name": 123, "version": "1.0.0"},
        {"name": "Mod"},
        {"name": "Mod", "version": ""},
        {"name": "Mod", "version": 100},
    ],
    ids=[
        "name-missing",
        "name-empty",
        "name-non-string",
        "version-missing",
        "version-empty",
        "version-non-string",
    ],
)
def test_read_package_json_invalid_mandatory_field_raises(tmp_path: Path, content: dict[str, object]) -> None:
    """Missing/empty/wrong-type name or version raises InvalidMetadataError."""
    mod_dir = tmp_path / "Mod"
    _write_package_json(mod_dir, content)

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_invalid_json_raises(tmp_path: Path) -> None:
    """Malformed JSON in package.json raises InvalidMetadataError."""
    mod_dir = tmp_path / "InvalidJson"
    mod_dir.mkdir()
    (mod_dir / "package.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_non_object_json_raises(tmp_path: Path) -> None:
    """JSON array (non-object) as root raises InvalidMetadataError."""
    mod_dir = tmp_path / "NonObjectJson"
    mod_dir.mkdir()
    (mod_dir / "package.json").write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


# --- meta_from_dll -------------------------------------------------------


def test_meta_from_dll_uses_stem_as_default_name(tmp_path: Path) -> None:
    """meta_from_dll uses DLL filename stem as mod name when no override given.

    Creates dll_path (AmandsCoolMod.dll) with no name override. Returns ModMeta
    with name="AmandsCoolMod" (stem only, not full filename) and version="".
    """
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path)

    assert meta.name == "AmandsCoolMod"
    assert meta.version == ""


def test_meta_from_dll_explicit_name_overrides_stem(tmp_path: Path) -> None:
    """meta_from_dll name parameter overrides DLL filename stem.

    Creates AmandsCoolMod.dll but passes name="OverriddenName". Returns ModMeta
    with name="OverriddenName", not "AmandsCoolMod".
    """
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path, name="OverriddenName")

    assert meta.name == "OverriddenName"


def test_meta_from_dll_uses_version_override(tmp_path: Path) -> None:
    """meta_from_dll version parameter sets ModMeta.version field.

    Creates AmandsCoolMod.dll and passes version="1.2.3". Returns ModMeta with
    version="1.2.3".
    """
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path, version="1.2.3")

    assert meta.version == "1.2.3"


def test_meta_from_dll_no_version_override_is_empty(tmp_path: Path) -> None:
    """meta_from_dll without version parameter leaves version field empty.

    Creates AmandsCoolMod.dll with no version override. Returns ModMeta with
    version="" (empty string).
    """
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path)

    assert meta.version == ""


# --- build_trailers / mod_info_from_commit -------------------------------


def _sample_mod_info(**overrides: object) -> ModInfo:
    """Construct a ModInfo with fixed defaults, allowing keyword overrides.

    Sets standard values: name="SAIN", version="3.2.1", mod_type="hybrid",
    author="Solarint", spt_range="~4.1.0", state="installed", commit="abc1234",
    date=2026-01-01, forge_id="1234", forge_version_id="56789". Any provided
    override replaces the corresponding default before instantiation.
    """
    defaults: dict[str, object] = {
        "name": "SAIN",
        "version": "3.2.1",
        "mod_type": "hybrid",
        "author": "Solarint",
        "spt_range": "~4.1.0",
        "state": "installed",
        "commit": "abc1234",
        "date": datetime(2026, 1, 1, tzinfo=UTC),
        "forge_id": "1234",
        "forge_version_id": "56789",
        "guid": "",
        "source": "https://example.invalid/SAIN-3.2.1.7z",
    }
    defaults.update(overrides)
    return ModInfo(**defaults)  # type: ignore[arg-type]


def test_build_trailers_contains_all_keys_in_order() -> None:
    """build_trailers returns dict with all MOD_TRAILER_KEYS in exact order.

    Calls build_trailers(_sample_mod_info()). Resulting dict keys must be
    identical to MOD_TRAILER_KEYS (both keys and order match).
    """
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info)

    assert list(trailers.keys()) == list(MOD_TRAILER_KEYS)


def test_build_trailers_maps_field_values() -> None:
    """build_trailers maps ModInfo fields to trailer keys with exact values.

    build_trailers(_sample_mod_info()) produces: trailers["Mod"]="SAIN",
    trailers["Mod-Version"]="3.2.1", trailers["Mod-Author"]="Solarint",
    trailers["Mod-Type"]="hybrid", trailers["Mod-SPT"]="~4.1.0", etc.
    """
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info)

    assert trailers["Mod"] == "SAIN"
    assert trailers["Mod-Version"] == "3.2.1"
    assert trailers["Mod-Type"] == "hybrid"
    assert trailers["Mod-Author"] == "Solarint"
    assert trailers["Mod-SPT"] == "~4.1.0"
    assert trailers["Mod-Source"] == "https://example.invalid/SAIN-3.2.1.7z"
    assert trailers["Mod-Forge-Id"] == "1234"
    assert trailers["Mod-Forge-Version-Id"] == "56789"
    assert trailers["Mod-State"] == "installed"


def test_build_trailers_keeps_empty_values() -> None:
    """build_trailers preserves empty string values in trailer dict.

    Calls build_trailers with guid="", forge_id="", forge_version_id="".
    Resulting trailers["Mod-Guid"]="", trailers["Mod-Forge-Id"]="",
    trailers["Mod-Forge-Version-Id"]="" (not omitted, present and empty).
    """
    mod_info = _sample_mod_info(guid="", forge_id="", forge_version_id="")

    trailers = build_trailers(mod_info)

    assert trailers["Mod-Guid"] == ""
    assert trailers["Mod-Forge-Id"] == ""
    assert trailers["Mod-Forge-Version-Id"] == ""


def test_build_trailers_without_previous_version_omits_the_key() -> None:
    """Without previous_version parameter, Mod-Previous-Version key is absent."""
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info)

    assert "Mod-Previous-Version" not in trailers


def test_build_trailers_with_previous_version_adds_the_key() -> None:
    """With previous_version parameter, Mod-Previous-Version key is added."""
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info, previous_version="3.2.0")

    assert trailers["Mod-Previous-Version"] == "3.2.0"


def test_mod_info_from_commit_returns_none_without_mod_trailer() -> None:
    """mod_info_from_commit returns None when Mod trailer is absent.

    Creates CommitInfo with subject "snapshot: 2026-01-01" and empty trailers dict.
    mod_info_from_commit returns None (not a mod install commit).
    """
    commit = CommitInfo(
        hash="abc1234",
        subject="snapshot: 2026-01-01",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers={},
    )

    assert mod_info_from_commit(commit) is None


def test_mod_info_from_commit_returns_none_for_unrelated_trailers() -> None:
    """Commit with non-mod trailers (e.g., Signed-off-by) returns None."""
    commit = CommitInfo(
        hash="abc1234",
        subject="chore: something",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers={"Signed-off-by": "someone"},
    )

    assert mod_info_from_commit(commit) is None


@pytest.mark.parametrize(
    ("trailer", "value"),
    [("Mod-Type", "not-a-type"), ("Mod-State", "not-a-state")],
    ids=["invalid-mod-type", "invalid-mod-state"],
)
def test_mod_info_from_commit_raises_on_an_out_of_enum_trailer(trailer: str, value: str) -> None:
    """Invalid enum value in Mod-Type or Mod-State raises InvalidMetadataError."""
    trailers = {
        "Mod": "SAIN",
        "Mod-Version": "3.2.1",
        "Mod-Type": "hybrid",
        "Mod-Author": "Solarint",
        "Mod-SPT": "~4.1.0",
        "Mod-Source": "",
        "Mod-Forge-Id": "",
        "Mod-Forge-Version-Id": "",
        "Mod-Guid": "",
        "Mod-State": "installed",
    }
    trailers[trailer] = value
    commit = CommitInfo(
        hash="abc1234",
        subject="mod: SAIN 3.2.1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers=trailers,
    )

    with pytest.raises(InvalidMetadataError):
        mod_info_from_commit(commit)


def test_build_trailers_roundtrips_through_mod_info_from_commit() -> None:
    """build_trailers and mod_info_from_commit roundtrip ModInfo identically.

    Converts _sample_mod_info() → build_trailers() → CommitInfo → mod_info_from_commit().
    Result matches original ModInfo exactly, proving serialization is lossless.
    """
    mod_info = _sample_mod_info()
    trailers = build_trailers(mod_info)
    commit = CommitInfo(
        hash=mod_info.commit,
        subject=f"mod: {mod_info.name} {mod_info.version}",
        date=mod_info.date,
        trailers=trailers,
    )

    result = mod_info_from_commit(commit)

    assert result == mod_info


@pytest.mark.parametrize(
    "overrides",
    [
        {"guid": "com.sain.mod"},
        {"forge_id": "9999", "forge_version_id": "8888"},
        {"mod_type": "server"},
        {"mod_type": "client"},
        {"state": "disabled"},
        {"state": "removed"},
    ],
)
def test_build_trailers_roundtrips_with_variant_fields(
    overrides: dict[str, object],
) -> None:
    """Roundtrip works for all enum variants (mod_type, state) and forge fields.

    Parametrized with guid="com.sain.mod", forge_id/forge_version_id, and all
    combinations of mod_type (server/client/hybrid) and state (disabled/removed).
    Each variant survives the trailers→commit→parse cycle identically.
    """
    mod_info = _sample_mod_info(**overrides)
    trailers = build_trailers(mod_info)
    commit = CommitInfo(
        hash=mod_info.commit,
        subject=f"mod: {mod_info.name} {mod_info.version}",
        date=mod_info.date,
        trailers=trailers,
    )

    result = mod_info_from_commit(commit)

    assert result == mod_info


# --- version_matches -------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        ("1.2.3", "1.2.3"),
        ("1.2.3", "v1.2.3"),
        ("1.2.3", "^1.2.0"),
        ("1.9.9", "^1.2.0"),
        ("1.2.3", "~1.2.0"),
        ("1.2.9", "~1.2.0"),
        ("1.2.3", ">=1.2.3"),
        ("1.3.0", ">=1.2.3"),
        ("1.2.4", ">1.2.3"),
        ("1.2.3", "<=1.2.3"),
        ("1.2.2", "<=1.2.3"),
        ("1.2.2", "<1.2.3"),
        ("1.2.3", "*"),
        ("0.0.1", "*"),
        ("999.999.999", "*"),
        ("1.5.9", "1.x"),
        ("1.9.9", "1.x"),
        ("1.2.3", "1.2.x"),
        ("1.2.9", "1.2.x"),
        ("1.5.0", "1.x"),
        ("1.0.0", ">=1.0.0 <2.0.0"),
        ("1.0.0", ""),
        ("0.0.1", ""),
        ("1.5.0", "1"),
        ("1.0.0", "1"),
        ("1.5.3", "1"),
        ("1.5.3", "1.5"),
    ],
)
def test_version_matches_true_cases(version: str, range_spec: str) -> None:
    """version_matches returns True for versions satisfying range specifications.

    Parametrized with 27 cases including exact matches (1.2.3), semver ranges
    (^1.2.0, ~1.2.0), comparators (>=, <, <=, >), wildcards (1.x, *), multi-part
    ranges (>=1.0.0 <2.0.0), and edge cases (empty range, bare major version).
    """
    assert version_matches(version, range_spec) is True


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        ("1.3.0", "1.2.3"),
        ("2.0.0", "^1.2.0"),
        ("1.3.0", "~1.2.0"),
        ("1.2.2", ">=1.2.3"),
        ("1.2.3", ">1.2.3"),
        ("1.2.4", "<=1.2.3"),
        ("1.2.3", "<1.2.3"),
        ("2.0.0", "1.x"),
        ("1.3.0", "1.2.x"),
        ("2.0.0", ">=1.0.0 <2.0.0"),
        ("2.0.1", ">=1.0.0 <2.0.0"),
        ("2.0.0", "1"),
        ("1.6.0", "1.5"),
    ],
)
def test_version_matches_false_cases(version: str, range_spec: str) -> None:
    """version_matches returns False for versions outside specified ranges.

    Parametrized with 13 rejection cases: exact mismatch (1.3.0 vs 1.2.3), caret
    overflow (2.0.0 vs ^1.2.0), tilde overflow (1.3.0 vs ~1.2.0), comparator
    violations, wildcard mismatches (2.0.0 vs 1.x), and multi-part range boundary
    violations (2.0.0/2.0.1 vs >=1.0.0 <2.0.0).
    """
    assert version_matches(version, range_spec) is False


def test_version_matches_leading_v_on_the_version_argument() -> None:
    """version_matches strips leading 'v' from version argument before comparing.

    Calls version_matches("v1.2.3", "1.2.3") and expects True (leading v is
    discarded, then 1.2.3 matches 1.2.3 exactly).
    """
    assert version_matches("v1.2.3", "1.2.3") is True


@pytest.mark.parametrize(
    "range_spec",
    [
        "1.0.0 || 2.0.0",
        "1.0.0 - 2.0.0",
        "1.2.3-beta",
        "1.2.3+build.1",
    ],
)
def test_version_matches_unsupported_range_syntax_raises(range_spec: str) -> None:
    """Unsupported npm range syntax (||, -, prerelease, build) raises error."""
    with pytest.raises(UnsupportedRangeError):
        version_matches("1.2.3", range_spec)


def test_version_matches_non_numeric_version_raises() -> None:
    """Non-numeric version string raises UnsupportedRangeError."""
    with pytest.raises(UnsupportedRangeError):
        version_matches("not-a-version", "1.2.3")


# --- caret ranges with omitted/zero leading parts (npm semantics) -----------
#
# Caret upper bounds are determined from the first explicitly given non-zero
# part, allowing omitted parts to act as wildcards. ^0.0 allows <0.1.0, ^0
# allows <1.0.0, and ^0.x treats the wildcard as omitted and also allows <1.0.0.


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        ("0.0.5", "^0.0"),  # npm: ^0.0 := >=0.0.0 <0.1.0
        ("0.5.0", "^0"),  # npm: ^0 := >=0.0.0 <1.0.0
        ("0.5.0", "^0.x"),  # npm: ^0.x := >=0.0.0 <1.0.0
    ],
)
def test_version_matches_caret_omitted_leading_parts_true(version: str, range_spec: str) -> None:
    """Caret with leading zero and omitted parts follows npm semantics."""
    assert version_matches(version, range_spec) is True


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        ("0.1.0", "^0.0"),  # outside npm's <0.1.0 upper bound
        ("1.0.0", "^0"),  # outside npm's <1.0.0 upper bound
        ("1.0.0", "^0.x"),  # outside npm's <1.0.0 upper bound
    ],
)
def test_version_matches_caret_omitted_leading_parts_false(version: str, range_spec: str) -> None:
    """version_matches rejects versions outside caret bounds (npm semantics).

    Tests: "0.1.0" vs "^0.0" (outside <0.1.0), "1.0.0" vs "^0" (outside <1.0.0),
    "1.0.0" vs "^0.x" (outside <1.0.0). Each must return False per npm caret rules.
    """
    assert version_matches(version, range_spec) is False


# --- review coverage gaps (expected to already pass) -------------------------


def test_read_package_json_dependencies_skips_non_string_ranges(
    tmp_path: Path,
) -> None:
    """Non-string dependency values (e.g., numeric) are silently skipped."""
    mod_dir = tmp_path / "MixedDeps"
    _write_package_json(
        mod_dir,
        {
            "name": "MixedDeps",
            "version": "1.0.0",
            "dependencies": {"A": "^1.0.0", "B": 5},
        },
    )

    meta = read_package_json(mod_dir)

    assert meta.dependencies == {"A": "^1.0.0"}


def test_version_matches_uppercase_x_wildcard() -> None:
    """version_matches treats uppercase X as wildcard identical to lowercase x.

    Tests version_matches("1.5.0", "1.X") returns True and version_matches("2.0.0",
    "1.X") returns False, proving uppercase X wildcards work like lowercase x.
    """
    assert version_matches("1.5.0", "1.X") is True
    assert version_matches("2.0.0", "1.X") is False


def test_version_matches_prerelease_version_argument_raises() -> None:
    """Prerelease version argument (e.g., 1.2.3-beta) raises error."""
    with pytest.raises(UnsupportedRangeError):
        version_matches("1.2.3-beta", "1.2.3")


# --- edge cases in the numeric-part / comparator / caret parsing -----------


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [("v", "1.2.3"), ("1.2.3", "v")],
    ids=["bare-v-as-version", "bare-v-as-range-token"],
)
def test_version_matches_bare_v_with_nothing_after_it_raises(version: str, range_spec: str) -> None:
    """Bare 'v' without numeric parts raises UnsupportedRangeError."""
    with pytest.raises(UnsupportedRangeError):
        version_matches(version, range_spec)


def test_version_matches_bare_equals_comparator_true() -> None:
    """version_matches treats bare = as exact-match comparator (success case).

    version_matches("1.2.3", "=1.2.3") returns True (= prefix means exact match).
    """
    assert version_matches("1.2.3", "=1.2.3") is True


def test_version_matches_bare_equals_comparator_false() -> None:
    """version_matches treats bare = as exact-match comparator (failure case).

    version_matches("1.2.4", "=1.2.3") returns False (exact match required).
    """
    assert version_matches("1.2.4", "=1.2.3") is False


def test_version_matches_caret_with_no_numeric_parts_matches_everything() -> None:
    """version_matches with ^x (no numeric parts) matches any version.

    Caret with no numeric parts reduces to an unconstrained range. version_matches
    ("5.3.1", "^x") and version_matches("0.0.0", "^x") both return True.
    """
    # "^x" reduces to zero numeric parts, which _token_matches treats the
    # same as an unconstrained caret range.
    assert version_matches("5.3.1", "^x") is True
    assert version_matches("0.0.0", "^x") is True


def test_version_matches_bare_wildcard_version_argument_raises() -> None:
    """version_matches raises UnsupportedRangeError when version is bare 'x'.

    Calling version_matches("x", "1.2.3") raises UnsupportedRangeError because
    the version argument cannot be a bare wildcard.
    """
    with pytest.raises(UnsupportedRangeError):
        version_matches("x", "1.2.3")
