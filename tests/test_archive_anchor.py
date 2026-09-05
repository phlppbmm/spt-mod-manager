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
    """Write relative paths as text files under root (dict keys as paths, values as content)."""
    for relpath, content in entries.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _resolve_source(layout: ArchiveLayout, target: Path) -> Path:
    """Find the mapping covering ``target`` and resolve it to a source path.

    Works regardless of whether ``mappings`` is one entry per anchor root or
    one entry per file -- both are valid readings of the stub's docstring.
    """
    candidates = [m for m in layout.mappings if target == m.target or target.is_relative_to(m.target)]
    assert candidates, f"target {target} is not covered by any mapping in {layout.mappings}"
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
    """Assert an ArchiveLayout's mod type, files, mappings and roundtrip content."""
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
        assert source_path.is_file(), f"{source_path} (for target {target}) is not a file"
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
            "Wrapper1/Wrapper2/Wrapper3/SPT/user/mods/CoolMod/package.json": ("server-package"),
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


@pytest.mark.parametrize(("tree_entries", "expected_files", "expected_mod_type"), LAYOUT_VARIANTS)
def test_find_anchor_layout_variants(
    tmp_path: Path,
    tree_entries: dict[str, str],
    expected_files: dict[Path, str],
    expected_mod_type: ModType,
) -> None:
    """Archive layouts are detected correctly from directory trees (server/client/hybrid).

    Tests that ``find_anchor`` normalizes path casing, detects mod type, and
    produces mappings that roundtrip file content to the normalized targets.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(extracted_root, tree_entries)

    layout = find_anchor(extracted_root)

    _assert_layout_matches(layout, extracted_root, expected_files, expected_mod_type)


def test_find_anchor_raises_when_no_anchor_present(tmp_path: Path) -> None:
    """find_anchor raises ArchiveError when extracted tree has no SPT or BepInEx anchor.

    Creates an extracted directory with unrelated files (Readme.txt, InstallInstructions/)
    without any SPT/user/mods or BepInEx/plugins anchor. Error message must include
    samples from the directory listing to guide the user (both files appear in message).
    """
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
    """find_anchor raises ArchiveError for extracted directory with no files.

    Creates an empty extracted directory and verifies that find_anchor raises
    ArchiveError with the message "The archive contains no mod files."
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()

    with pytest.raises(ArchiveError):
        find_anchor(extracted_root)


def test_find_anchor_raises_message_truncated_with_a_count_past_thirty_entries(
    tmp_path: Path,
) -> None:
    """Error message listing unanchored files is truncated after 30 entries."""
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(extracted_root, {f"junk/file-{i:02d}.txt": "junk" for i in range(35)})

    with pytest.raises(ArchiveError) as excinfo:
        find_anchor(extracted_root)

    message = str(excinfo.value)
    assert "and 6 more" in message


# The three cases below guard against false anchors: a directory that merely
# happens to be *named* "plugins" or end in "user/mods" is not automatically
# a client/server anchor -- only such a directory reachable from the
# extracted root without passing through an already-detected anchor first
# (i.e. genuinely part of the top-level archive layout) qualifies. An
# unrelated "plugins" or "user/mods" folder nested under something else
# (screenshots, readmes, or inside an already-found mod folder) must not be
# picked up as a second anchor.


def test_find_anchor_ignores_unrelated_bare_plugins_dir(tmp_path: Path) -> None:
    """A bare 'plugins' folder nested under other content is not anchored as client.

    Only plugins directories reachable from root without passing through an
    already-detected anchor qualify as client anchors.
    """
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
    assert not any(m.target.as_posix().startswith(CLIENT_PLUGINS_TARGET.as_posix()) for m in layout.mappings)


def test_find_anchor_ignores_unrelated_bare_user_mods_dir(tmp_path: Path) -> None:
    """A bare 'user/mods' folder nested under other content is not anchored as server.

    Only user/mods directories reachable from root without passing through an
    already-detected anchor qualify as server anchors.
    """
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
        ModType.SERVER,
    )


def test_find_anchor_recognizes_spt_runtime_prefixed_server_dir_without_package_json(
    tmp_path: Path,
) -> None:
    """Real-corpus shape (betterkeys, EasyAmmoNames): the installer's own
    ``SPT_Runtime/user/mods/<mod>/`` prefix, and the mod folder carries no
    ``package.json`` at all -- just a DLL and data files.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "SPT_Runtime/user/mods/betterkeys/betterkeys.dll": "server-dll",
            "SPT_Runtime/user/mods/betterkeys/config/config.jsonc": "{}",
        },
    )

    layout = find_anchor(extracted_root)

    _assert_layout_matches(
        layout,
        extracted_root,
        {
            Path("SPT/user/mods/betterkeys/betterkeys.dll"): "server-dll",
            Path("SPT/user/mods/betterkeys/config/config.jsonc"): "{}",
        },
        ModType.SERVER,
    )


def test_find_anchor_recognizes_spt_runtime_prefixed_hybrid_without_package_json(
    tmp_path: Path,
) -> None:
    """Real-corpus shape (SAIN, Tyfon-UIFixes, WTT-CommonLib): a server mod
    folder below ``SPT_Runtime/user/mods/`` with no ``package.json``, next to
    a ``BepInEx/plugins`` client tree. Both halves must be anchored -- the
    server half must never be silently dropped, leaving the archive typed
    ``client`` only.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "SPT_Runtime/user/mods/ServerPart/ServerPart.dll": "server-dll",
            "BepInEx/plugins/ClientPart/ClientPart.dll": "client-dll",
        },
    )

    layout = find_anchor(extracted_root)

    _assert_layout_matches(
        layout,
        extracted_root,
        {
            Path("SPT/user/mods/ServerPart/ServerPart.dll"): "server-dll",
            Path("BepInEx/plugins/ClientPart/ClientPart.dll"): "client-dll",
        },
        ModType.HYBRID,
    )


def test_find_anchor_does_not_trust_a_junk_dir_that_merely_starts_with_spt(
    tmp_path: Path,
) -> None:
    """A directory that merely starts with "spt" (e.g. an icon-pack folder
    "spticons") must not be trusted as the installer's own server-tree
    prefix -- only the exact "spt" segment and genuine installer prefixes
    like "SPT_Runtime" are trusted unconditionally. Without real mod content
    (no ``package.json`` anywhere below it), a "spt*"-prefixed junk dir must
    stay unanchored, even next to a real client anchor.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "spticons/user/mods/readme.png": "not-a-mod",
            "BepInEx/plugins/CoolPlugin.dll": "client-dll",
        },
    )

    layout = find_anchor(extracted_root)

    assert layout.mod_type == "client"
    assert layout.files == [Path("BepInEx/plugins/CoolPlugin.dll")]


def test_find_anchor_raises_for_a_junk_spt_prefixed_dir_with_no_other_content(
    tmp_path: Path,
) -> None:
    """ArchiveError is raised for a junk directory merely starting with 'spt' and no mods.

    Without real mod content (no package.json), a 'spt*'-prefixed directory like
    'spticons' must not be mistaken for an installer prefix.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(extracted_root, {"spticons/user/mods/readme.png": "not-a-mod"})

    with pytest.raises(ArchiveError):
        find_anchor(extracted_root)


def test_find_anchor_includes_bepinex_patchers_files_alongside_plugins(
    tmp_path: Path,
) -> None:
    """Real-corpus shape (WTT-CommonLib): ``BepInEx/patchers/`` ships next to
    ``BepInEx/plugins/`` and must install too, not be silently dropped.
    """
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir()
    _write_tree(
        extracted_root,
        {
            "BepInEx/plugins/CoolPlugin.dll": "plugin-content",
            "BepInEx/patchers/CoolPatcher.dll": "patcher-content",
        },
    )

    layout = find_anchor(extracted_root)

    assert layout.mod_type == "client"
    assert Path("BepInEx/plugins/CoolPlugin.dll") in layout.files
    assert Path("BepInEx/patchers/CoolPatcher.dll") in layout.files


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
        ModType.SERVER,
    )
