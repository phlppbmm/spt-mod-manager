"""Out-of-process tests for ``sptmod rpc <name>``: the real console script,
launched as a subprocess, produces the same NDJSON contract the in-process
``CliRunner`` tests pin. Skipped outright when the ``sptmod`` binary is not
on ``PATH`` (it is, under ``uv run``, once ``uv sync`` has installed the
project).

Never touches the real Forge API or the developer's catalog cache: every
request that accepts ``db_path`` gets one pointed into ``tmp_path``.
"""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo

SPTMOD_BINARY = shutil.which("sptmod")

pytestmark = pytest.mark.skipif(
    SPTMOD_BINARY is None,
    reason="sptmod console script not on PATH; run under 'uv run pytest'",
)


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(detect_layout(path))
    repo.commit(
        "chore: clean SPT 4.1.0", trailers={"Spt-Version": "4.1.0"}, paths=None
    )
    return repo


def _run_rpc(name: str, request: dict[str, object]) -> subprocess.CompletedProcess[str]:
    assert SPTMOD_BINARY is not None
    return subprocess.run(
        [SPTMOD_BINARY, "rpc", name],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def test_happy_path_prints_exactly_one_stdout_line_and_empty_stderr(
    tmp_path: Path,
) -> None:
    process = _run_rpc("list_directories", {"path": str(tmp_path)})

    assert process.returncode == 0
    assert process.stderr == ""
    lines = [line for line in process.stdout.splitlines() if line]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["event"] == "result"
    assert doc["data"]["path"] == str(tmp_path.resolve())


def test_install_from_archive_streams_progress_then_a_result_all_valid_json(
    fake_game_dir: Path, tmp_path: Path
) -> None:
    _clean_repo(fake_game_dir)
    archive = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "SPT/user/mods/CoolMod/package.json",
            json.dumps({"name": "CoolMod", "version": "1.2.3", "author": "Acme"}),
        )
    db_path = tmp_path / "unused-catalog.db"

    process = _run_rpc(
        "install_from_archive",
        {
            "path": str(fake_game_dir),
            "archive": str(archive),
            "db_path": str(db_path),
        },
    )

    assert process.returncode == 0
    lines = [line for line in process.stdout.splitlines() if line]
    parsed = [json.loads(line) for line in lines]
    events = [doc["event"] for doc in parsed]
    assert events[:-1] == ["progress"] * (len(events) - 1)
    assert events[-1] == "result"
    assert parsed[-1]["data"]["mod"]["name"] == "CoolMod"


def test_core_error_exits_one_with_a_mapped_error_envelope(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    process = _run_rpc("status", {"path": str(missing)})

    assert process.returncode == 1
    lines = [line for line in process.stdout.splitlines() if line]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc == {
        "event": "error",
        "data": {
            "error": f"{missing} does not exist",
            "code": "NOT_A_GAME_DIR",
            "details": {
                "conflicts": [],
                "residues": [],
                "conflicting_paths": [],
                "installed": [],
                "name": "",
                "candidates": [],
                "dependency": "",
                "reason": "",
                "api_code": "",
                "api_status": 0,
            },
        },
    }


def test_malformed_stdin_exits_two() -> None:
    assert SPTMOD_BINARY is not None
    process = subprocess.run(
        [SPTMOD_BINARY, "rpc", "status"],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert process.returncode == 2
    lines = [line for line in process.stdout.splitlines() if line]
    doc = json.loads(lines[0])
    assert doc["data"]["code"] == "VALIDATION_ERROR"


def test_unknown_function_name_exits_two() -> None:
    process = _run_rpc("totally_made_up_function", {})

    assert process.returncode == 2
    lines = [line for line in process.stdout.splitlines() if line]
    doc = json.loads(lines[0])
    assert doc["data"]["code"] == "VALIDATION_ERROR"
    assert "totally_made_up_function" in doc["data"]["error"]
