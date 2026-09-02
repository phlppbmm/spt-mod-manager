"""Tests for package.json parsing, DLL fallbacks, trailer mapping and semver ranges."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sptmod.core.models import CommitInfo, ModInfo
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
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "package.json").write_text(json.dumps(content), encoding="utf-8")


# --- read_package_json -------------------------------------------------------


def test_read_package_json_full_metadata(tmp_path: Path) -> None:
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
    mod_dir = tmp_path / "MinimalMod"
    _write_package_json(mod_dir, {"name": "MinimalMod", "version": "1.0.0"})

    meta = read_package_json(mod_dir)

    assert meta == ModMeta(name="MinimalMod", version="1.0.0")
    assert meta.spt_range == ""
    assert meta.author == ""
    assert meta.guid == ""
    assert meta.dependencies == {}


def test_read_package_json_parses_dependencies_dict(tmp_path: Path) -> None:
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
    mod_dir = tmp_path / "NoPackageJson"
    mod_dir.mkdir()

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_missing_name_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "NoName"
    _write_package_json(mod_dir, {"version": "1.0.0"})

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_empty_name_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "EmptyName"
    _write_package_json(mod_dir, {"name": "", "version": "1.0.0"})

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_non_string_name_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "NonStringName"
    _write_package_json(mod_dir, {"name": 123, "version": "1.0.0"})

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_missing_version_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "NoVersion"
    _write_package_json(mod_dir, {"name": "NoVersion"})

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_empty_version_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "EmptyVersion"
    _write_package_json(mod_dir, {"name": "EmptyVersion", "version": ""})

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_non_string_version_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "NonStringVersion"
    _write_package_json(mod_dir, {"name": "NonStringVersion", "version": 100})

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_invalid_json_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "InvalidJson"
    mod_dir.mkdir()
    (mod_dir / "package.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


def test_read_package_json_non_object_json_raises(tmp_path: Path) -> None:
    mod_dir = tmp_path / "NonObjectJson"
    mod_dir.mkdir()
    (mod_dir / "package.json").write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(InvalidMetadataError):
        read_package_json(mod_dir)


# --- meta_from_dll -------------------------------------------------------


def test_meta_from_dll_uses_stem_as_default_name(tmp_path: Path) -> None:
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path)

    assert meta.name == "AmandsCoolMod"
    assert meta.version == ""


def test_meta_from_dll_explicit_name_overrides_stem(tmp_path: Path) -> None:
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path, name="OverriddenName")

    assert meta.name == "OverriddenName"


def test_meta_from_dll_uses_version_override(tmp_path: Path) -> None:
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path, version="1.2.3")

    assert meta.version == "1.2.3"


def test_meta_from_dll_no_version_override_is_empty(tmp_path: Path) -> None:
    dll_path = tmp_path / "AmandsCoolMod.dll"
    dll_path.write_bytes(b"binary")

    meta = meta_from_dll(dll_path)

    assert meta.version == ""


# --- build_trailers / mod_info_from_commit -------------------------------


def _sample_mod_info(**overrides: object) -> ModInfo:
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
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info)

    from sptmod.core.models import MOD_TRAILER_KEYS

    assert list(trailers.keys()) == list(MOD_TRAILER_KEYS)


def test_build_trailers_maps_field_values() -> None:
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
    mod_info = _sample_mod_info(guid="", forge_id="", forge_version_id="")

    trailers = build_trailers(mod_info)

    assert trailers["Mod-Guid"] == ""
    assert trailers["Mod-Forge-Id"] == ""
    assert trailers["Mod-Forge-Version-Id"] == ""


def test_build_trailers_without_previous_version_omits_the_key() -> None:
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info)

    assert "Mod-Previous-Version" not in trailers


def test_build_trailers_with_previous_version_adds_the_key() -> None:
    mod_info = _sample_mod_info()

    trailers = build_trailers(mod_info, previous_version="3.2.0")

    assert trailers["Mod-Previous-Version"] == "3.2.0"


def test_mod_info_from_commit_returns_none_without_mod_trailer() -> None:
    commit = CommitInfo(
        hash="abc1234",
        subject="snapshot: 2026-01-01",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers={},
    )

    assert mod_info_from_commit(commit) is None


def test_mod_info_from_commit_returns_none_for_unrelated_trailers() -> None:
    commit = CommitInfo(
        hash="abc1234",
        subject="chore: something",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers={"Signed-off-by": "someone"},
    )

    assert mod_info_from_commit(commit) is None


def test_mod_info_from_commit_raises_on_invalid_mod_type() -> None:
    commit = CommitInfo(
        hash="abc1234",
        subject="mod: SAIN 3.2.1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers={
            "Mod": "SAIN",
            "Mod-Version": "3.2.1",
            "Mod-Type": "not-a-type",
            "Mod-Author": "Solarint",
            "Mod-SPT": "~4.1.0",
            "Mod-Source": "",
            "Mod-Forge-Id": "",
            "Mod-Forge-Version-Id": "",
            "Mod-Guid": "",
            "Mod-State": "installed",
        },
    )

    with pytest.raises(InvalidMetadataError):
        mod_info_from_commit(commit)


def test_mod_info_from_commit_raises_on_invalid_mod_state() -> None:
    commit = CommitInfo(
        hash="abc1234",
        subject="mod: SAIN 3.2.1",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        trailers={
            "Mod": "SAIN",
            "Mod-Version": "3.2.1",
            "Mod-Type": "hybrid",
            "Mod-Author": "Solarint",
            "Mod-SPT": "~4.1.0",
            "Mod-Source": "",
            "Mod-Forge-Id": "",
            "Mod-Forge-Version-Id": "",
            "Mod-Guid": "",
            "Mod-State": "not-a-state",
        },
    )

    with pytest.raises(InvalidMetadataError):
        mod_info_from_commit(commit)


def test_build_trailers_roundtrips_through_mod_info_from_commit() -> None:
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
        ("1.5.9", "1.x"),
        ("1.2.3", "1.2.x"),
        ("1.5.0", "1.x"),
        ("1.0.0", ">=1.0.0 <2.0.0"),
        ("1.0.0", ""),
        ("1.5.0", "1"),
        ("1.5.3", "1.5"),
    ],
)
def test_version_matches_true_cases(version: str, range_spec: str) -> None:
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
    assert version_matches(version, range_spec) is False


def test_version_matches_x_range_major_only() -> None:
    assert version_matches("1.9.9", "1.x") is True
    assert version_matches("2.0.0", "1.x") is False


def test_version_matches_x_range_minor() -> None:
    assert version_matches("1.2.9", "1.2.x") is True
    assert version_matches("1.3.0", "1.2.x") is False


def test_version_matches_wildcard_matches_everything() -> None:
    assert version_matches("0.0.1", "*") is True
    assert version_matches("999.999.999", "*") is True


def test_version_matches_empty_range_matches_everything() -> None:
    assert version_matches("0.0.1", "") is True


def test_version_matches_omitted_minor_and_patch() -> None:
    assert version_matches("1.0.0", "1") is True
    assert version_matches("1.5.3", "1") is True
    assert version_matches("2.0.0", "1") is False


def test_version_matches_leading_v_on_version() -> None:
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
    with pytest.raises(UnsupportedRangeError):
        version_matches("1.2.3", range_spec)


def test_version_matches_non_numeric_version_raises() -> None:
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
def test_version_matches_caret_omitted_leading_parts_true(
    version: str, range_spec: str
) -> None:
    assert version_matches(version, range_spec) is True


@pytest.mark.parametrize(
    ("version", "range_spec"),
    [
        ("0.1.0", "^0.0"),  # outside npm's <0.1.0 upper bound
        ("1.0.0", "^0"),  # outside npm's <1.0.0 upper bound
        ("1.0.0", "^0.x"),  # outside npm's <1.0.0 upper bound
    ],
)
def test_version_matches_caret_omitted_leading_parts_false(
    version: str, range_spec: str
) -> None:
    assert version_matches(version, range_spec) is False


# --- review coverage gaps (expected to already pass) -------------------------


def test_read_package_json_dependencies_skips_non_string_ranges(
    tmp_path: Path,
) -> None:
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
    assert version_matches("1.5.0", "1.X") is True
    assert version_matches("2.0.0", "1.X") is False


def test_version_matches_prerelease_version_argument_raises() -> None:
    with pytest.raises(UnsupportedRangeError):
        version_matches("1.2.3-beta", "1.2.3")
