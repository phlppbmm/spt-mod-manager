"""Tests for package.json parsing, DLL fallbacks, trailer mapping and semver ranges."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sptmod.core.models import (
    CommitInfo,
    ModInfo,
    ModState,
    ModType,
)
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
from sptmod.core.repo import Repo


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


@pytest.mark.parametrize(
    ("kwargs", "expected_name", "expected_version"),
    [
        pytest.param({}, "AmandsCoolMod", "", id="stem-as-name"),
        pytest.param({"name": "OverriddenName"}, "OverriddenName", "", id="explicit-name-wins"),
        pytest.param({"version": "1.2.3"}, "AmandsCoolMod", "1.2.3", id="explicit-version"),
    ],
)
def test_meta_from_dll_derives_name_and_version(
    tmp_path: Path, kwargs: dict[str, str], expected_name: str, expected_version: str
) -> None:
    """meta_from_dll falls back to the DLL stem and empty version unless overridden.

    Creates AmandsCoolMod.dll and calls meta_from_dll with no override
    ("stem-as-name": name is the stem, version stays empty), with name=
    "OverriddenName" ("explicit-name-wins": the override replaces the stem) and
    with version="1.2.3" ("explicit-version": the override reaches ModMeta.version).
    Asserted because the DLL fallback is the only metadata source for client-only mods.
    """
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path, **kwargs)

    assert meta.name == expected_name
    assert meta.version == expected_version


# --- build_trailers / mod_info_from_commit -------------------------------


_SAMPLE_MOD_INFO = ModInfo(
    name="SAIN",
    version="3.2.1",
    mod_type=ModType.HYBRID,
    author="Solarint",
    spt_range="~4.1.0",
    state=ModState.INSTALLED,
    commit="abc1234",
    date=datetime(2026, 1, 1, tzinfo=UTC),
    forge_id="1234",
    forge_version_id="56789",
    guid="",
    source="https://example.invalid/SAIN-3.2.1.7z",
)


def test_build_trailers_writes_the_trailer_block_in_the_documented_order(initialized_repo: Repo) -> None:
    """A commit written from build_trailers carries its trailer keys in a fixed order.

    Commits the trailers of a fully populated ModInfo with Repo.commit and reads them
    back with Repo.log, so the assertion sees the order the trailer block really has in
    the commit message. The expected list is hardcoded rather than derived from
    MOD_TRAILER_KEYS: the layout of the trailer block is part of the on-disk format the
    Rust port must reproduce, so reordering the keys has to fail here.
    """
    mod_dir = initialized_repo.path / "SPT" / "user" / "mods" / "SAIN"
    mod_dir.mkdir(parents=True)
    (mod_dir / "plugin.dll").write_bytes(b"content")

    initialized_repo.commit("mod: SAIN 3.2.1", trailers=build_trailers(_SAMPLE_MOD_INFO))

    assert list(initialized_repo.log()[0].trailers) == [
        "Mod",
        "Mod-Version",
        "Mod-Type",
        "Mod-Author",
        "Mod-SPT",
        "Mod-Source",
        "Mod-Forge-Id",
        "Mod-Forge-Version-Id",
        "Mod-Guid",
        "Mod-State",
    ]


def test_build_trailers_maps_field_values() -> None:
    """build_trailers maps ModInfo fields to trailer keys with exact values.

    build_trailers(_SAMPLE_MOD_INFO) produces: trailers["Mod"]="SAIN",
    trailers["Mod-Version"]="3.2.1", trailers["Mod-Author"]="Solarint",
    trailers["Mod-Type"]="hybrid", trailers["Mod-SPT"]="~4.1.0", etc.
    """
    mod_info = _SAMPLE_MOD_INFO

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
    mod_info = replace(_SAMPLE_MOD_INFO, guid="", forge_id="", forge_version_id="")

    trailers = build_trailers(mod_info)

    assert trailers["Mod-Guid"] == ""
    assert trailers["Mod-Forge-Id"] == ""
    assert trailers["Mod-Forge-Version-Id"] == ""


def test_build_trailers_without_previous_version_omits_the_key() -> None:
    """Without previous_version parameter, Mod-Previous-Version key is absent."""
    mod_info = _SAMPLE_MOD_INFO

    trailers = build_trailers(mod_info)

    assert "Mod-Previous-Version" not in trailers


def test_build_trailers_with_previous_version_adds_the_key() -> None:
    """With previous_version parameter, Mod-Previous-Version key is added."""
    mod_info = _SAMPLE_MOD_INFO

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

    Converts _SAMPLE_MOD_INFO → build_trailers() → CommitInfo → mod_info_from_commit().
    Result matches original ModInfo exactly, proving serialization is lossless.
    """
    mod_info = _SAMPLE_MOD_INFO
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
    "mod_info",
    [
        replace(_SAMPLE_MOD_INFO, guid="com.sain.mod"),
        replace(_SAMPLE_MOD_INFO, forge_id="9999", forge_version_id="8888"),
        replace(_SAMPLE_MOD_INFO, mod_type=ModType.SERVER),
        replace(_SAMPLE_MOD_INFO, mod_type=ModType.CLIENT),
        replace(_SAMPLE_MOD_INFO, state=ModState.DISABLED),
        replace(_SAMPLE_MOD_INFO, state=ModState.REMOVED),
    ],
    ids=["guid", "forge-ids", "type-server", "type-client", "state-disabled", "state-removed"],
)
def test_build_trailers_roundtrips_with_variant_fields(mod_info: ModInfo) -> None:
    """Roundtrip works for all enum variants (mod_type, state) and forge fields.

    Parametrized with guid="com.sain.mod", forge_id/forge_version_id, and all
    combinations of mod_type (server/client/hybrid) and state (disabled/removed).
    Each variant survives the trailers→commit→parse cycle identically.
    """
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
        pytest.param("1.2.3", "1.2.3", id="exact-match"),
        pytest.param("1.2.3", "v1.2.3", id="leading-v-on-range"),
        pytest.param("1.2.3", "^1.2.0", id="caret-lower-bound"),
        pytest.param("1.9.9", "^1.2.0", id="caret-below-upper-bound"),
        pytest.param("1.2.3", "~1.2.0", id="tilde-lower-bound"),
        pytest.param("1.2.9", "~1.2.0", id="tilde-below-upper-bound"),
        pytest.param("1.2.3", ">=1.2.3", id="at-least-equal"),
        pytest.param("1.3.0", ">=1.2.3", id="at-least-greater"),
        pytest.param("1.2.4", ">1.2.3", id="greater-than"),
        pytest.param("1.2.3", "<=1.2.3", id="at-most-equal"),
        pytest.param("1.2.2", "<=1.2.3", id="at-most-smaller"),
        pytest.param("1.2.2", "<1.2.3", id="less-than"),
        pytest.param("1.2.3", "*", id="star-any"),
        pytest.param("0.0.1", "*", id="star-lowest"),
        pytest.param("999.999.999", "*", id="star-highest"),
        pytest.param("1.5.9", "1.x", id="minor-wildcard"),
        pytest.param("1.9.9", "1.x", id="minor-wildcard-high-minor"),
        pytest.param("1.2.3", "1.2.x", id="patch-wildcard"),
        pytest.param("1.2.9", "1.2.x", id="patch-wildcard-high-patch"),
        pytest.param("1.5.0", "1.x", id="minor-wildcard-zero-patch"),
        pytest.param("1.0.0", ">=1.0.0 <2.0.0", id="two-comparators-lower-bound"),
        pytest.param("1.0.0", "", id="empty-range-any"),
        pytest.param("0.0.1", "", id="empty-range-lowest"),
        pytest.param("1.5.0", "1", id="bare-major"),
        pytest.param("1.0.0", "1", id="bare-major-zero-minor"),
        pytest.param("1.5.3", "1", id="bare-major-with-patch"),
        pytest.param("1.5.3", "1.5", id="bare-major-minor"),
        pytest.param("v1.2.3", "1.2.3", id="leading-v-on-version"),
        pytest.param("1.5.0", "1.X", id="uppercase-x-wildcard"),
        pytest.param("1.2.3", "=1.2.3", id="bare-equals"),
        pytest.param("5.3.1", "^x", id="caret-without-numeric-parts"),
        pytest.param("0.0.0", "^x", id="caret-without-numeric-parts-zero-version"),
        # npm caret semantics: the upper bound comes from the first explicitly
        # given non-zero part, so omitted parts act as wildcards.
        pytest.param("0.0.5", "^0.0", id="caret-0-0"),  # ^0.0 := >=0.0.0 <0.1.0
        pytest.param("0.5.0", "^0", id="caret-0"),  # ^0 := >=0.0.0 <1.0.0
        pytest.param("0.5.0", "^0.x", id="caret-0-x"),  # ^0.x := >=0.0.0 <1.0.0
    ],
)
def test_version_matches_true_cases(version: str, range_spec: str) -> None:
    """version_matches returns True for versions satisfying range specifications.

    Covers exact matches (1.2.3), a leading "v" on either argument, semver ranges
    (^1.2.0, ~1.2.0), comparators (>=, <, <=, >, bare =), wildcards (1.x, 1.X, *,
    ^x), multi-part ranges (>=1.0.0 <2.0.0), caret ranges with omitted/zero leading
    parts, and edge cases (empty range, bare major version). Asserted because these
    ranges come verbatim from mod package.json files.
    """
    assert version_matches(version, range_spec) is True


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        pytest.param("1.3.0", "1.2.3", id="exact-mismatch"),
        pytest.param("2.0.0", "^1.2.0", id="caret-upper-bound"),
        pytest.param("1.3.0", "~1.2.0", id="tilde-upper-bound"),
        pytest.param("1.2.2", ">=1.2.3", id="at-least-violated"),
        pytest.param("1.2.3", ">1.2.3", id="greater-than-violated"),
        pytest.param("1.2.4", "<=1.2.3", id="at-most-violated"),
        pytest.param("1.2.3", "<1.2.3", id="less-than-violated"),
        pytest.param("2.0.0", "1.x", id="minor-wildcard-wrong-major"),
        pytest.param("1.3.0", "1.2.x", id="patch-wildcard-wrong-minor"),
        pytest.param("2.0.0", ">=1.0.0 <2.0.0", id="two-comparators-upper-bound"),
        pytest.param("2.0.1", ">=1.0.0 <2.0.0", id="two-comparators-above-upper-bound"),
        pytest.param("2.0.0", "1", id="bare-major-mismatch"),
        pytest.param("1.6.0", "1.5", id="bare-major-minor-mismatch"),
        pytest.param("1.2.4", "=1.2.3", id="bare-equals-mismatch"),
        pytest.param("2.0.0", "1.X", id="uppercase-x-wildcard-miss"),
        pytest.param("0.1.0", "^0.0", id="caret-0-0-upper-bound"),
        pytest.param("1.0.0", "^0", id="caret-0-upper-bound"),
        pytest.param("1.0.0", "^0.x", id="caret-0-x-upper-bound"),
    ],
)
def test_version_matches_false_cases(version: str, range_spec: str) -> None:
    """version_matches returns False for versions outside specified ranges.

    Covers exact mismatch (1.3.0 vs 1.2.3), bare = mismatch, caret overflow (2.0.0
    vs ^1.2.0), tilde overflow (1.3.0 vs ~1.2.0), comparator violations, wildcard
    mismatches (2.0.0 vs 1.x and 1.X), multi-part range boundary violations
    (2.0.0/2.0.1 vs >=1.0.0 <2.0.0) and the npm caret upper bounds for ranges with
    omitted/zero leading parts. Asserted so an incompatible mod is never accepted.
    """
    assert version_matches(version, range_spec) is False


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        pytest.param("1.2.3", "1.0.0 || 2.0.0", id="or-operator"),
        pytest.param("1.2.3", "1.0.0 - 2.0.0", id="hyphen-range"),
        pytest.param("1.2.3", "1.2.3-beta", id="prerelease-range"),
        pytest.param("1.2.3", "1.2.3+build.1", id="build-metadata-range"),
        pytest.param("not-a-version", "1.2.3", id="non-numeric-version"),
        pytest.param("1.2.3-beta", "1.2.3", id="prerelease-version"),
        pytest.param("v", "1.2.3", id="bare-v-as-version"),
        pytest.param("1.2.3", "v", id="bare-v-as-range-token"),
        pytest.param("x", "1.2.3", id="bare-wildcard-version"),
    ],
)
def test_version_matches_unsupported_input_raises(version: str, range_spec: str) -> None:
    """version_matches raises UnsupportedRangeError for input it cannot decide.

    Covers npm range syntax the parser does not implement (||, hyphen ranges,
    prerelease and build-metadata ranges) and version arguments that carry no
    comparable numeric parts (non-numeric text, a prerelease suffix, a bare "v" on
    either side, a bare wildcard). Asserted because guessing a match here would
    silently install an incompatible mod.
    """
    with pytest.raises(UnsupportedRangeError):
        version_matches(version, range_spec)


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
