"""Tests for ``sptmod.core.archive.find_anchor``.

Builds extracted trees directly (no archive needed, ``find_anchor`` operates
on a directory) and checks the normalized :class:`ArchiveLayout`: canonical
target casing, detected mod type, the ``files`` list, and that ``mappings``
actually reconstruct those files from real content on disk.
"""

from pathlib import Path

import pytest

from sptmod.core.archive import (
    CLIENT_PLUGINS_TARGET,
    SERVER_MODS_TARGET,
    AnchorMapping,
    ArchiveLayout,
    find_anchor,
)
from sptmod.core.errors import ArchiveError
from sptmod.core.models import ModType


def _write_tree(root: Path, entries: dict[str, str]) -> None:
    for relpath, content in entries.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _resolve_source(layout: ArchiveLayout, target: Path) -> Path:
    """Find the mapping covering ``target`` and resolve it to a source path.

    Works regardless of whether ``mappings`` is one entry per anchor root or
    one entry per file -- both are valid readings of the stub's docstring.
    """
    candidates = [
        m
        for m in layout.mappings
        if target == m.target or target.is_relative_to(m.target)
    ]
    assert candidates, (
        f"target {target} is not covered by any mapping in {layout.mappings}"
    )
    mapping = max(candidates, key=lambda m: len(m.target.parts))
    if target == mapping.target:
        return mapping.source
    return mapping.source / target.relative_to(mapping.target)


def _assert_layout_matches(
    layout: ArchiveLayout,
    extracted_root: Path,
    expected: dict[Path, str],
    expected_mod_type: ModType,
) -> None:
    assert layout.mod_type == expected_mod_type
    assert set(layout.files) == set(expected)

    for mapping in layout.mappings:
        assert isinstance(mapping, AnchorMapping)
        assert mapping.source.is_absolute()
        assert mapping.source.is_relative_to(extracted_root)
        assert not mapping.target.is_absolute()
        target_str = mapping.target.as_posix()
        assert target_str.startswith(SERVER_MODS_TARGET.as_posix()) or (
            target_str.startswith(CLIENT_PLUGINS_TARGET.as_posix())
        )

    for target, content in expected.items():
        target_str = target.as_posix()
        assert target_str.startswith(SERVER_MODS_TARGET.as_posix()) or (
            target_str.startswith(CLIENT_PLUGINS_TARGET.as_posix())
        )
        source_path = _resolve_source(layout, target)
        assert source_path.is_file(), (
            f"{source_path} (for target {target}) is not a file"
        )
        assert source_path.read_text(encoding="utf-8") == content


LAYOUT_VARIANTS = [
    pytest.param(
        {
            "SPT/user/mods/CoolMod/package.json": "server-package",
            "SPT/user/mods/CoolMod/mod.dll": "server-dll",
        },
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
            Path("SPT/user/mods/CoolMod/mod.dll"): "server-dll",
        },
        "server",
        id="correct-layout",
    ),
    pytest.param(
        {
            "SAIN-3.2.1/SPT/user/mods/CoolMod/package.json": "server-package",
        },
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
        },
        "server",
        id="single-wrapper-dir",
    ),
    pytest.param(
        {
            "Wrapper1/Wrapper2/Wrapper3/SPT/user/mods/CoolMod/package.json": (
                "server-package"
            ),
        },
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
        },
        "server",
        id="multiple-nested-wrappers",
    ),
    pytest.param(
        {
            "user/mods/CoolMod/package.json": "server-package",
        },
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
        },
        "server",
        id="user-mods-without-spt-prefix",
    ),
    pytest.param(
        {
            "plugins/CoolPlugin.dll": "client-dll",
        },
        {
            Path("BepInEx/plugins/CoolPlugin.dll"): "client-dll",
        },
        "client",
        id="plugins-without-bepinex-prefix",
    ),
    pytest.param(
        {
            "spt/USER/mods/CoolMod/package.json": "server-package",
        },
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
        },
        "server",
        id="mixed-odd-casing-server",
    ),
    pytest.param(
        {
            "bepinex/Plugins/CoolPlugin.dll": "client-dll",
        },
        {
            Path("BepInEx/plugins/CoolPlugin.dll"): "client-dll",
        },
        "client",
        id="mixed-odd-casing-client",
    ),
    pytest.param(
        {
            "SPT/user/mods/CoolMod/package.json": "server-package",
            "BepInEx/plugins/CoolPlugin.dll": "client-dll",
        },
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
            Path("BepInEx/plugins/CoolPlugin.dll"): "client-dll",
        },
        "hybrid",
        id="hybrid-both-trees",
    ),
    pytest.param(
        {
            "BepInEx/plugins/CoolPlugin.dll": "bare-dll",
        },
        {
            Path("BepInEx/plugins/CoolPlugin.dll"): "bare-dll",
        },
        "client",
        id="client-only-bare-dll-under-plugins",
    ),
    pytest.param(
        {
            "BepInEx/plugins/CoolPluginFolder/CoolPlugin.dll": "folder-dll",
            "BepInEx/plugins/CoolPluginFolder/data.bin": "folder-data",
        },
        {
            Path("BepInEx/plugins/CoolPluginFolder/CoolPlugin.dll"): "folder-dll",
            Path("BepInEx/plugins/CoolPluginFolder/data.bin"): "folder-data",
        },
        "client",
        id="client-mod-as-folder-under-plugins",
    ),
]


@pytest.mark.parametrize(
    ("tree_entries", "expected_files", "expected_mod_type"), LAYOUT_VARIANTS
)
def test_find_anchor_layout_variants(
    tmp_path: Path,
    tree_entries: dict[str, str],
    expected_files: dict[Path, str],
    expected_mod_type: ModType,
) -> None:
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(extracted_root, tree_entries)

    layout = find_anchor(extracted_root)

    _assert_layout_matches(layout, extracted_root, expected_files, expected_mod_type)


def test_find_anchor_raises_when_no_anchor_present(tmp_path: Path) -> None:
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "Readme.txt": "please read me",
            "InstallInstructions/steps.txt": "1. copy the folder",
        },
    )

    with pytest.raises(ArchiveError) as excinfo:
        find_anchor(extracted_root)

    message = str(excinfo.value)
    assert "Readme.txt" in message
    assert "InstallInstructions" in message


def test_find_anchor_raises_on_empty_directory(tmp_path: Path) -> None:
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()

    with pytest.raises(ArchiveError):
        find_anchor(extracted_root)


# The three cases below guard against false anchors: a directory that merely
# happens to be *named* "plugins" or end in "user/mods" is not automatically
# a client/server anchor -- only such a directory reachable from the
# extracted root without passing through an already-detected anchor first
# (i.e. genuinely part of the top-level archive layout) qualifies. An
# unrelated "plugins" or "user/mods" folder nested under something else
# (screenshots, readmes, or inside an already-found mod folder) must not be
# picked up as a second anchor.


def test_find_anchor_ignores_unrelated_bare_plugins_dir(tmp_path: Path) -> None:
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "SPT/user/mods/CoolMod/package.json": "server-package",
            "ReadmeAssets/plugins/icon.png": "not-a-mod-icon",
        },
    )

    layout = find_anchor(extracted_root)

    assert layout.mod_type == "server"
    assert set(layout.files) == {Path("SPT/user/mods/CoolMod/package.json")}
    assert Path("BepInEx/plugins/icon.png") not in layout.files
    assert not any(
        m.target.as_posix().startswith(CLIENT_PLUGINS_TARGET.as_posix())
        for m in layout.mappings
    )


def test_find_anchor_ignores_unrelated_bare_user_mods_dir(tmp_path: Path) -> None:
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "SPT/user/mods/CoolMod/package.json": "server-package",
            "docs/user/mods/screenshot.png": "fake-png-bytes",
        },
    )

    layout = find_anchor(extracted_root)

    _assert_layout_matches(
        layout,
        extracted_root,
        {Path("SPT/user/mods/CoolMod/package.json"): "server-package"},
        "server",
    )


def test_find_anchor_nested_capitalized_plugins_dir_stays_part_of_server_mod(
    tmp_path: Path,
) -> None:
    """A 'Plugins' folder *inside* the already-found server mod folder is
    just part of that mod's shipped content, not a second client anchor.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "SPT/user/mods/CoolMod/package.json": "server-package",
            "SPT/user/mods/CoolMod/Plugins/foo.dll": "inner-dll",
        },
    )

    layout = find_anchor(extracted_root)

    _assert_layout_matches(
        layout,
        extracted_root,
        {
            Path("SPT/user/mods/CoolMod/package.json"): "server-package",
            Path("SPT/user/mods/CoolMod/Plugins/foo.dll"): "inner-dll",
        },
        "server",
    )
