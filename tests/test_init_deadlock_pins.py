"""Pins for the init-deadlock fix (spt-mod-manager, coordinator round 2/3).

(1) init_game_dir on an unresolvable-version dir leaves no .git residue and
    fails before git init runs. (2) init on a zero-commit half-init repo
    continues instead of refusing. (3) init on a repo with commits still
    refuses. (4) CLI init on an undetectable-version layout, no
    --spt-version, no input -> clear error, no .git residue. (5) an explicit
    --spt-version wins over a core.json/PE value that would detect
    differently. (6) the PE reader returns None quickly for a header
    claiming a giant section size, no multi-GB read.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sptmod.cli import app as cli_app
from sptmod.core import game_dir, services
from sptmod.core.errors import GitError, SptVersionUnknownError
from sptmod.core.game_dir import read_pe_file_version
from sptmod.core.repo import Repo
from test_game_layout import _build_synthetic_exe, _write_server_exe

runner = CliRunner()


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


# --- (1) unresolvable version: no .git residue, checked before git init ----


def test_init_leaves_no_git_residue_when_version_cannot_be_resolved(
    fake_game_dir_runtime: Path,
) -> None:
    with pytest.raises(SptVersionUnknownError):
        services.init_game_dir(fake_game_dir_runtime)

    assert not (fake_game_dir_runtime / ".git").exists()


# --- (2) half-init (zero commits) continues instead of refusing -----------


def test_init_on_a_zero_commit_half_init_repo_continues(
    fake_game_dir_runtime: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"], cwd=fake_game_dir_runtime, check=True
    )

    result = services.init_game_dir(fake_game_dir_runtime, spt_version="4.1.2")

    assert result.commit != ""
    assert Repo(fake_game_dir_runtime).log() != []


# --- (3) a repo with commits still refuses ---------------------------------


def test_init_on_a_repo_with_commits_still_refuses(
    fake_game_dir_runtime: Path,
) -> None:
    services.init_game_dir(fake_game_dir_runtime, spt_version="4.1.2")

    with pytest.raises(GitError):
        services.init_game_dir(fake_game_dir_runtime, spt_version="4.1.2")


# --- (4) CLI init, undetectable version, no --spt-version, no input -------


def test_cli_init_undetectable_version_no_option_no_input_errors_cleanly(
    fake_game_dir_runtime: Path,
) -> None:
    result = runner.invoke(cli_app, ["init", str(fake_game_dir_runtime)], input="")

    assert result.exit_code != 0
    assert not (fake_game_dir_runtime / ".git").exists()


# --- (5) explicit --spt-version wins over a detectable core.json/PE value -


def test_explicit_spt_version_overrides_a_detectable_core_json_version(
    fake_game_dir_runtime: Path,
) -> None:
    core_config_path = (
        fake_game_dir_runtime / "SPT_Runtime" / "SPT_Data" / "configs" / "core.json"
    )
    core_config_path.write_text(
        json.dumps({"sptVersion": "1.0.0"}), encoding="utf-8"
    )

    result = services.init_game_dir(fake_game_dir_runtime, spt_version="9.9.9")

    assert result.spt_version == "9.9.9"
    commit = Repo(fake_game_dir_runtime).log()[0]
    assert commit.trailers.get("Spt-Version") == "9.9.9"


def test_explicit_spt_version_overrides_a_detectable_pe_version(
    fake_game_dir_runtime: Path,
) -> None:
    _write_server_exe(fake_game_dir_runtime, "SPT_Runtime", "1.0.0")

    result = services.init_game_dir(fake_game_dir_runtime, spt_version="9.9.9")

    assert result.spt_version == "9.9.9"


# --- (6) PE reader: giant claimed section size returns None quickly -------


def test_read_pe_file_version_none_quickly_for_a_giant_claimed_section_size(
    tmp_path: Path,
) -> None:
    real = bytearray(_build_synthetic_exe("4.1.2"))
    # Section header layout (see _SECTION_HEADER_SIZE / _read_sections):
    # name(8) + virtual_size(4) + virtual_address(4) + raw_size(4) + ...
    # The section header sits right after DOS+COFF+OptionalHeader (0x400).
    section_header_offset = 0x400
    raw_size_offset = section_header_offset + 8 + 4 + 4  # skip name, vsize, vaddr
    struct_giant = (0xFFFFFFFF).to_bytes(4, "little")
    real[raw_size_offset : raw_size_offset + 4] = struct_giant
    # Truncate the file right after the section header: the declared raw_size
    # claims ~4GB but no such data exists on disk.
    truncated = bytes(real[: section_header_offset + 40])
    exe = tmp_path / "SPT.Server.exe"
    exe.write_bytes(truncated)

    start = time.monotonic()
    result = read_pe_file_version(exe)
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 2.0


# --- spt core plugin/patcher files: excluded from pre_existing_mods, -------
# --- tracked in the baseline commit, never listed as manual in status ------


def _build_spt_core_and_real_mod_fixture(root: Path) -> None:
    core_plugin_dir = root / "BepInEx" / "plugins" / "spt"
    core_plugin_dir.mkdir(parents=True)
    (core_plugin_dir / "spt-core.dll").write_bytes(b"spt-core-binary")

    patchers_dir = root / "BepInEx" / "patchers"
    patchers_dir.mkdir(parents=True)
    (patchers_dir / "spt-prepatch.dll").write_bytes(b"spt-prepatch-binary")

    real_mod_dir = root / "SPT_Runtime" / "user" / "mods" / "RealMod"
    real_mod_dir.mkdir(parents=True)
    (real_mod_dir / "package.json").write_text(
        json.dumps({"name": "RealMod", "version": "1.0.0", "author": "me"}),
        encoding="utf-8",
    )


def test_init_excludes_spt_core_files_from_pre_existing_mods_but_tracks_them(
    fake_game_dir_runtime: Path,
) -> None:
    _build_spt_core_and_real_mod_fixture(fake_game_dir_runtime)

    result = services.init_game_dir(fake_game_dir_runtime, spt_version="4.1.2")

    assert result.pre_existing_mods == [Path("SPT_Runtime/user/mods/RealMod")]

    committed = {f.path for f in Repo(fake_game_dir_runtime).show_files(result.commit)}
    assert Path("BepInEx/plugins/spt/spt-core.dll") in committed
    assert Path("BepInEx/patchers/spt-prepatch.dll") in committed


def test_init_yes_adopts_only_the_real_mod_and_status_has_no_manual_entries(
    fake_game_dir_runtime: Path,
) -> None:
    _build_spt_core_and_real_mod_fixture(fake_game_dir_runtime)

    result = services.init_game_dir(
        fake_game_dir_runtime, adopt=True, spt_version="4.1.2"
    )

    assert [mod.name for mod in result.adopted] == ["RealMod"]

    report = services.status(fake_game_dir_runtime)
    assert report.manual_mods == []
