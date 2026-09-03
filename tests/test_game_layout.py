"""Tests for SPT 4.x layout support (spt-mod-manager-fyx).

Covers ``GameLayout``/``detect_layout`` (SPT_Runtime vs SPT, both-present
warning, the flat-3.x rejection, precise missing-directory naming), the SPT
version chain (``read_core_json_version``, ``read_pe_file_version`` against a
synthetic PE built in-test, ``store_spt_version``/``read_stored_version``,
``resolve_spt_version`` fallback order), ``Repo.config_get``/``config_set``,
the ``set_spt_version`` service and API endpoint, the ``sptmod init
--spt-version`` CLI pin, and an end-to-end pass over the ``SPT_Runtime``
layout (init whitelist, archive-prefix translation on install, status,
list_mods, check_profiles).
"""

import json
import struct
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sptmod.cli import app as cli_app
from sptmod.core import game_dir, services
from sptmod.core.errors import GitError, NotAGameDirError
from sptmod.core.game_dir import (
    SPT_VERSION_CONFIG_KEY,
    GameLayout,
    detect_layout,
    read_core_json_version,
    read_pe_file_version,
    read_stored_version,
    resolve_spt_version,
    store_spt_version,
)
from sptmod.core.repo import Repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


# ============================================================================
# Synthetic PE builder (stdlib only): a minimal PE32+ image with one
# VS_VERSION_INFO resource carrying a StringFileInfo/String entry, built to
# the documented Microsoft VS_VERSIONINFO binary layout (independent of
# read_pe_file_version's own parsing implementation) and cross-checked with
# `pefile` during development.
# ============================================================================


def _utf16z(s: str) -> bytes:
    return (s + "\0").encode("utf-16-le")


def _pad4(data: bytes) -> bytes:
    return data + b"\0" * ((-len(data)) % 4)


def _version_block(
    key: str, value_word_count: int, value: bytes, children: bytes = b""
) -> bytes:
    key_bytes = _utf16z(key)
    head = struct.pack("<HHH", 0, value_word_count, 1 if value else 0) + key_bytes
    body = _pad4(head) + _pad4(value) if value else _pad4(head)
    total = body + children
    return struct.pack("<HHH", len(total), value_word_count, 1) + key_bytes + total[
        len(head) :
    ]


def _build_version_resource(version: str, key_name: str) -> bytes:
    fixed_file_info = struct.pack(
        "<13I", 0xFEEF04BD, 0x10000, 0, 0, 0, 0, 0x3F, 0, 4, 1, 0, 0, 0
    )
    value_str = _utf16z(version)
    string_entry = _version_block(key_name, len(version) + 1, value_str)
    string_table = _version_block("040904B0", 0, b"", string_entry)
    string_file_info = _version_block("StringFileInfo", 0, b"", string_table)
    return _version_block(
        "VS_VERSION_INFO", len(fixed_file_info) // 2, fixed_file_info, string_file_info
    )


def _build_synthetic_exe(version: str, key_name: str = "FileVersion") -> bytes:
    """Build a minimal PE32+ image whose VS_VERSION_INFO carries ``key_name``."""
    version_data = _build_version_resource(version, key_name)

    level1_size, level2_size, level3_size, data_entry_size = 24, 24, 24, 16
    header_total = level1_size + level2_size + level3_size + data_entry_size
    padded_version = _pad4(version_data)

    section_alignment, file_alignment, headers_size = 0x1000, 0x200, 0x400
    rsrc_rva, rsrc_raw_offset = section_alignment, headers_size
    data_rva = rsrc_rva + header_total

    level1 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack(
        "<II", 16, 0x80000000 | level1_size
    )
    level2 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack(
        "<II", 1, 0x80000000 | (level1_size + level2_size)
    )
    level3 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack(
        "<II", 1033, level1_size + level2_size + level3_size
    )
    data_entry = struct.pack("<IIII", data_rva, len(version_data), 0, 0)
    rsrc_section = level1 + level2 + level3 + data_entry + padded_version
    rsrc_raw_size = ((len(rsrc_section) + file_alignment - 1) // file_alignment) * (
        file_alignment
    )
    rsrc_padded = rsrc_section + b"\0" * (rsrc_raw_size - len(rsrc_section))
    image_size = rsrc_rva + (
        (len(rsrc_section) + section_alignment - 1) // section_alignment
    ) * section_alignment

    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 60, 64)

    coff = struct.pack("<IHHIIIHH", 0x00004550, 0x8664, 1, 0, 0, 0, 0xF0, 0x0022)

    optional = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B, 14, 0, 0x200, 0, 0, 0, 0x1000, 0x140000000,
        section_alignment, file_alignment, 6, 0, 1, 0, 6, 0, 0,
        image_size, headers_size, 0, 3, 0,
        0x100000, 0x1000, 0x100000, 0x1000, 0, 16,
    )
    data_dirs = bytearray(16 * 8)
    struct.pack_into("<II", data_dirs, 2 * 8, rsrc_rva, len(rsrc_section))
    optional += bytes(data_dirs)

    section_header = struct.pack(
        "<8sIIIIIIHHI",
        b".rsrc\0\0\0",
        len(rsrc_section),
        rsrc_rva,
        rsrc_raw_size,
        rsrc_raw_offset,
        0, 0, 0, 0,
        0x40000040,
    )

    header_blob = bytes(dos) + coff + optional + section_header
    header_blob += b"\0" * (headers_size - len(header_blob))
    return header_blob + rsrc_padded


def _write_server_exe(root: Path, runtime_dir_name: str, version: str) -> Path:
    exe_path = root / runtime_dir_name / "SPT.Server.exe"
    exe_path.write_bytes(_build_synthetic_exe(version))
    return exe_path


class TestSyntheticExeBuilderSanityCheck:
    """The builder itself must actually produce a version FileVersion reads."""

    def test_builder_produces_a_pe_read_pe_file_version_can_parse(
        self, tmp_path: Path
    ) -> None:
        exe = tmp_path / "SPT.Server.exe"
        exe.write_bytes(_build_synthetic_exe("4.1.2"))

        assert read_pe_file_version(exe) == "4.1.2"


# ============================================================================
# detect_layout
# ============================================================================


def test_detect_layout_accepts_runtime_only(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT_Runtime"
    assert layout.warning == ""


def test_detect_layout_accepts_spt_only(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT"
    assert layout.warning == ""


def test_detect_layout_prefers_runtime_over_spt_and_warns(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)
    (root / "SPT").mkdir(parents=True)
    (root / "BepInEx").mkdir(parents=True)

    layout = detect_layout(root)

    assert layout.runtime_dir_name == "SPT_Runtime"
    assert "SPT_Runtime" in layout.warning
    assert "SPT" in layout.warning


def test_detect_layout_raises_when_neither_runtime_dir_exists_and_names_both(
    tmp_path: Path,
) -> None:
    root = tmp_path / "game"
    (root / "BepInEx").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    message = str(exc_info.value)
    assert "SPT_Runtime" in message
    assert "SPT" in message


def test_detect_layout_raises_when_bepinex_missing_and_names_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "game"
    (root / "SPT_Runtime").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    assert "BepInEx" in str(exc_info.value)


def test_detect_layout_rejects_a_flat_spt_3x_installation(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "BepInEx").mkdir(parents=True)
    (root / "SPT_Data" / "Server").mkdir(parents=True)
    (root / "user" / "mods").mkdir(parents=True)

    with pytest.raises(NotAGameDirError) as exc_info:
        detect_layout(root)

    message = str(exc_info.value)
    assert "3.x" in message
    assert "SPT_Runtime" in message
    assert "SPT" in message


# ============================================================================
# GameLayout accessors
# ============================================================================


@pytest.mark.parametrize("runtime_dir_name", ["SPT_Runtime", "SPT"])
def test_game_layout_accessors_are_relative_to_the_runtime_dir(
    runtime_dir_name: str,
) -> None:
    layout = GameLayout(root=Path("/game"), runtime_dir_name=runtime_dir_name)

    assert layout.runtime_dir == Path(runtime_dir_name)
    assert layout.mods_dir == Path(runtime_dir_name) / "user" / "mods"
    assert layout.profiles_dir == Path(runtime_dir_name) / "user" / "profiles"
    assert (
        layout.profile_backups_dir
        == Path(runtime_dir_name) / "user" / "profiles" / "backups"
    )
    assert (
        layout.core_config_file
        == Path(runtime_dir_name) / "SPT_Data" / "configs" / "core.json"
    )
    assert layout.server_exe == Path(runtime_dir_name) / "SPT.Server.exe"


# ============================================================================
# read_core_json_version
# ============================================================================


def _layout(tmp_path: Path, runtime_dir_name: str = "SPT_Runtime") -> GameLayout:
    (tmp_path / "game" / runtime_dir_name / "SPT_Data" / "configs").mkdir(
        parents=True, exist_ok=True
    )
    return GameLayout(root=tmp_path / "game", runtime_dir_name=runtime_dir_name)


def _write_core_json(layout: GameLayout, content: str) -> None:
    (layout.root / layout.core_config_file).write_text(content, encoding="utf-8")


def test_read_core_json_version_returns_the_field_when_present(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "4.1.0"}))

    assert read_core_json_version(layout) == "4.1.0"


def test_read_core_json_version_none_for_a_4x_style_config_without_the_key(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _write_core_json(
        layout, json.dumps({"projectName": "SPT", "compatibleTarkovVersion": "1.0"})
    )

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_when_file_missing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_when_json_malformed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write_core_json(layout, "{not valid json")

    assert read_core_json_version(layout) is None


def test_read_core_json_version_none_for_an_empty_string_value(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _write_core_json(layout, json.dumps({"sptVersion": "   "}))

    assert read_core_json_version(layout) is None


# ============================================================================
# read_pe_file_version
# ============================================================================


def test_read_pe_file_version_reads_file_version(tmp_path: Path) -> None:
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(_build_synthetic_exe("4.1.2", "FileVersion"))

    assert read_pe_file_version(exe) == "4.1.2"


def test_read_pe_file_version_falls_back_to_product_version(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(_build_synthetic_exe("4.0.9", "ProductVersion"))

    assert read_pe_file_version(exe) == "4.0.9"


def test_read_pe_file_version_none_for_garbage_bytes(tmp_path: Path) -> None:
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(b"not a PE file at all, just some junk bytes" * 4)

    assert read_pe_file_version(exe) is None


def test_read_pe_file_version_none_for_a_missing_file(tmp_path: Path) -> None:
    assert read_pe_file_version(tmp_path / "does-not-exist.exe") is None


# ============================================================================
# Repo.config_get / config_set
# ============================================================================


def test_config_get_returns_none_for_an_unset_key(initialized_repo: Repo) -> None:
    assert initialized_repo.config_get("sptmod.unset-key") is None


def test_config_set_then_get_roundtrips_the_value(initialized_repo: Repo) -> None:
    initialized_repo.config_set("sptmod.testkey", "hello-value")

    assert initialized_repo.config_get("sptmod.testkey") == "hello-value"


def test_config_set_overwrites_an_existing_value(initialized_repo: Repo) -> None:
    initialized_repo.config_set("sptmod.testkey", "first")
    initialized_repo.config_set("sptmod.testkey", "second")

    assert initialized_repo.config_get("sptmod.testkey") == "second"


# ============================================================================
# store_spt_version / read_stored_version
# ============================================================================


def test_read_stored_version_none_when_nothing_was_stored(
    initialized_repo: Repo,
) -> None:
    assert read_stored_version(initialized_repo) is None


def test_store_then_read_stored_version_roundtrips(initialized_repo: Repo) -> None:
    store_spt_version(initialized_repo, "4.1.2")

    assert read_stored_version(initialized_repo) == "4.1.2"
    assert initialized_repo.config_get(SPT_VERSION_CONFIG_KEY) == "4.1.2"


# ============================================================================
# resolve_spt_version chain: core.json -> PE -> stored -> None
# ============================================================================


def _build_layout_and_repo(
    tmp_path: Path,
    *,
    core_version: str | None = None,
    exe_version: str | None = None,
) -> tuple[GameLayout, Repo]:
    layout = _layout(tmp_path)
    core_config: dict[str, str] = {"projectName": "SPT"}
    if core_version is not None:
        core_config["sptVersion"] = core_version
    _write_core_json(layout, json.dumps(core_config))
    if exe_version is not None:
        _write_server_exe(layout.root, layout.runtime_dir_name, exe_version)
    repo = Repo.init(layout)
    return layout, repo


def test_resolve_spt_version_prefers_core_json_over_pe_and_stored(
    tmp_path: Path,
) -> None:
    layout, repo = _build_layout_and_repo(
        tmp_path, core_version="9.9.9", exe_version="1.1.1"
    )
    store_spt_version(repo, "2.2.2")

    assert resolve_spt_version(layout, repo) == "9.9.9"


def test_resolve_spt_version_falls_back_to_pe_when_core_json_has_no_version(
    tmp_path: Path,
) -> None:
    layout, repo = _build_layout_and_repo(tmp_path, exe_version="1.1.1")
    store_spt_version(repo, "2.2.2")

    assert resolve_spt_version(layout, repo) == "1.1.1"


def test_resolve_spt_version_falls_back_to_stored_when_core_json_and_pe_are_absent(
    tmp_path: Path,
) -> None:
    layout, repo = _build_layout_and_repo(tmp_path)
    store_spt_version(repo, "2.2.2")

    assert resolve_spt_version(layout, repo) == "2.2.2"


def test_resolve_spt_version_none_when_the_whole_chain_is_empty(
    tmp_path: Path,
) -> None:
    layout, repo = _build_layout_and_repo(tmp_path)

    assert resolve_spt_version(layout, repo) is None


# ============================================================================
# set_spt_version (service)
# ============================================================================


def test_set_spt_version_stores_and_returns_the_stripped_value(
    initialized_repo: Repo,
) -> None:
    result = services.set_spt_version(initialized_repo.path, "  4.1.2  ")

    assert result == "4.1.2"
    assert read_stored_version(initialized_repo) == "4.1.2"


def test_set_spt_version_raises_value_error_for_an_empty_version(
    initialized_repo: Repo,
) -> None:
    with pytest.raises(ValueError, match="empty"):
        services.set_spt_version(initialized_repo.path, "")


def test_set_spt_version_raises_not_a_game_dir_error_for_a_non_game_dir(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "not-a-game-dir"
    empty.mkdir()

    with pytest.raises(NotAGameDirError):
        services.set_spt_version(empty, "4.1.2")


def test_set_spt_version_raises_git_error_when_not_yet_a_repository(
    fake_game_dir: Path,
) -> None:
    with pytest.raises(GitError):
        services.set_spt_version(fake_game_dir, "4.1.2")


# ============================================================================
# set_spt_version over the rpc adapter
# ============================================================================


def test_rpc_set_spt_version_stores_and_returns_the_stripped_value(
    fake_game_dir: Path,
) -> None:
    services.init_game_dir(fake_game_dir)

    result = runner.invoke(
        cli_app,
        ["rpc", "set_spt_version"],
        input=json.dumps({"path": str(fake_game_dir), "version": "  4.1.9  "}),
    )

    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert result.exit_code == 0
    assert lines == [{"event": "result", "data": {"spt_version": "4.1.9"}}]
    assert read_stored_version(Repo(fake_game_dir)) == "4.1.9"


# ============================================================================
# CLI contract pin: `sptmod init --spt-version` / interactive prompt
# ============================================================================


def test_cli_init_with_spt_version_option_stores_it_and_skips_the_prompt(
    fake_game_dir_runtime: Path,
) -> None:
    result = runner.invoke(
        cli_app,
        ["init", str(fake_game_dir_runtime), "--spt-version", "4.1.2"],
    )

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout


def test_cli_init_without_spt_version_prompts_and_stores_the_answer(
    fake_game_dir_runtime: Path,
) -> None:
    result = runner.invoke(
        cli_app, ["init", str(fake_game_dir_runtime)], input="4.1.2\n"
    )

    assert result.exit_code == 0
    assert "initialized SPT 4.1.2" in result.stdout


def test_cli_init_reports_a_warning_when_both_runtime_dirs_are_present(
    fake_game_dir_runtime: Path,
) -> None:
    (fake_game_dir_runtime / "SPT").mkdir()
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = runner.invoke(cli_app, ["init", str(fake_game_dir_runtime)])

    assert result.exit_code == 0
    assert "SPT_Runtime" in result.stdout
    assert "SPT" in result.stdout


# ============================================================================
# End-to-end on the SPT_Runtime layout
# ============================================================================


def test_init_on_runtime_layout_tracks_only_the_whitelist_and_runtime_profile(
    fake_game_dir_runtime: Path,
) -> None:
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = services.init_game_dir(fake_game_dir_runtime)

    assert result.spt_version == "4.1.2"
    committed = {
        f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)
    }
    assert committed == {
        Path(".gitignore"),
        Path("SPT_Runtime/user/profiles/abc.json"),
    }


def test_init_on_runtime_layout_ignored_dummies_are_absent_from_the_commit(
    fake_game_dir_runtime: Path,
) -> None:
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")

    result = services.init_game_dir(fake_game_dir_runtime)

    committed = {
        f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)
    }
    ignored = {
        Path("SPT_Runtime/user/logs/x.log"),
        Path("EscapeFromTarkov_Data/foo"),
        Path("SPT_Runtime/user/profiles/backups/old.json"),
        Path("SPT_Runtime/SPT_Data/configs/core.json"),
    }
    assert committed.isdisjoint(ignored)


def test_install_from_archive_translates_the_spt_prefix_to_the_runtime_dir(
    fake_game_dir_runtime: Path, make_archive
) -> None:
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    services.init_game_dir(fake_game_dir_runtime)
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "me"}
            )
        },
        "zip",
    )

    result = services.install_from_archive(fake_game_dir_runtime, archive)

    assert result.files == [Path("SPT_Runtime/user/mods/CoolMod/package.json")]
    assert (
        fake_game_dir_runtime / "SPT_Runtime/user/mods/CoolMod/package.json"
    ).exists()
    assert not (fake_game_dir_runtime / "SPT/user/mods/CoolMod/package.json").exists()


def test_list_mods_and_status_work_on_the_runtime_layout(
    fake_game_dir_runtime: Path, make_archive
) -> None:
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    services.init_game_dir(fake_game_dir_runtime)
    archive = make_archive(
        {
            "SPT_Runtime/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "me"}
            )
        },
        "zip",
    )
    services.install_from_archive(fake_game_dir_runtime, archive)

    assert [mod.name for mod in services.list_mods(fake_game_dir_runtime)] == [
        "CoolMod"
    ]

    profile_path = fake_game_dir_runtime / "SPT_Runtime/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    report = services.status(fake_game_dir_runtime)

    assert report.modified_profiles == [
        Path("SPT_Runtime/user/profiles/abc.json")
    ]


def test_check_profiles_finds_residue_on_the_runtime_layout(
    fake_game_dir_runtime: Path, install_commit
) -> None:
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "4.1.2")
    repo = Repo.init(detect_layout(fake_game_dir_runtime))
    repo.commit(
        "chore: clean SPT 4.1.2", trailers={"Spt-Version": "4.1.2"}, paths=None
    )
    item_id = "5c0d1b8b86f7744bb2683cf1"
    install_commit(
        repo,
        "ServerMod",
        mod_type="server",
        files={
            "SPT_Runtime/user/mods/ServerMod/db/templates/items.json": json.dumps(
                {item_id: {"_id": item_id, "_name": "item"}}
            )
        },
    )
    profile_path = fake_game_dir_runtime / "SPT_Runtime/user/profiles/abc.json"
    profile_path.write_text(
        json.dumps(
            {
                "characters": {
                    "pmc": {
                        "Inventory": {
                            "items": [{"_id": "i1", "tpl": item_id}]
                        },
                        "TradersInfo": {},
                        "Quests": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    repo.commit(
        "chore: profile snapshot",
        paths=[Path("SPT_Runtime/user/profiles/abc.json")],
    )

    result = services.check_profiles(fake_game_dir_runtime)

    assert set(result.residues.keys()) == {"ServerMod"}
