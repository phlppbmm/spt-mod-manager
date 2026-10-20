"""Executable bits of the files ``install_from_archive`` and ``update_mod`` write.

Mods ship native libraries and scripts. Zip archives restore no mode on
extraction, so a mod file arrives without an executable bit; the install must
make ELF binaries and shebang scripts executable by their content, and an
update must leave a replaced file executable when it was before. Every case
also checks the working tree is clean afterwards, so the commit recorded the
mode that is on disk.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from conftest import forge_trailers
from fixture_types import ClientFactory, InstallCommit, MakeArchive
from forge_json import current_version_json, success_envelope
from sptmod.core import services
from sptmod.core.repo import Repo

SPT_VERSION = "4.1.0"
MOD_DIR = "SPT/user/mods/NativeMod"
PACKAGE = f"{MOD_DIR}/package.json"
ELF_HEADER = b"\x7fELF\x02\x01\x01\x00"


def _package(version: str) -> str:
    """Return the package.json of NativeMod at ``version``."""
    return json.dumps({"name": "NativeMod", "version": version, "author": "Acme"})


def _is_executable(path: Path) -> bool:
    """Return whether everyone who may read ``path`` may also execute it."""
    return path.stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize(
    ("name", "content", "executable"),
    [
        pytest.param(f"{MOD_DIR}/bin/native.so", ELF_HEADER + b"library", True, id="elf-shared-library"),
        pytest.param(f"{MOD_DIR}/bin/helper", ELF_HEADER + b"binary", True, id="elf-binary"),
        pytest.param(f"{MOD_DIR}/run.sh", "#!/bin/sh\necho run\n", True, id="shebang-script"),
        pytest.param(f"{MOD_DIR}/config.json", "{}", False, id="plain-data"),
    ],
)
def test_install_makes_mod_programs_executable_by_their_content(
    clean_repo: Repo, make_archive: MakeArchive, name: str, content: str | bytes, executable: bool
) -> None:
    """install_from_archive() makes a mod's ELF binaries and shebang scripts executable.

    A zip holding NativeMod's package.json and ``name`` is installed; ``name``
    is executable afterwards exactly when its content is a program, and the
    tree is clean. A mod's native helper must run without the player fixing
    permissions by hand, while its data stays data.
    """
    archive = make_archive({PACKAGE: _package("1.0.0"), name: content}, "zip")

    services.install_from_archive(clean_repo.path, archive)

    assert _is_executable(clean_repo.path / name) is executable
    assert clean_repo.is_clean()


def test_update_keeps_a_replaced_mod_file_executable(
    clean_repo: Repo,
    client_factory: ClientFactory,
    respx_router: respx.MockRouter,
    db_path: Path,
    install_commit: InstallCommit,
    make_archive: MakeArchive,
) -> None:
    """update_mod() leaves a replaced file executable when it was executable.

    NativeMod 1.0.0 is installed with an executable ``start`` committed as
    such; version 2.0.0 replaces it with content that is neither ELF nor a
    shebang script and carries no bit, so only the replaced file's mode can
    tell. Afterwards ``start`` holds the new content, is still executable and
    the tree is clean: an update must not take away what made the mod run.
    """
    start = clean_repo.path / MOD_DIR / "start"
    start.parent.mkdir(parents=True)
    start.touch(mode=0o755)
    install_commit(
        clean_repo,
        "NativeMod",
        "1.0.0",
        mod_type="server",
        files={PACKAGE: _package("1.0.0"), f"{MOD_DIR}/start": "start 1.0.0"},
        **forge_trailers("10", "100"),
    )
    body = make_archive({PACKAGE: _package("2.0.0"), f"{MOD_DIR}/start": "start 2.0.0"}, "zip").read_bytes()
    link = "https://dl.example/nativemod-2.0.0.zip"
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(
            200,
            json=success_envelope(
                {
                    "spt_version": SPT_VERSION,
                    "updates": [
                        {
                            "current_version": current_version_json(
                                version_id=100, mod_id=10, name="NativeMod", version="1.0.0", slug="nativemod"
                            ),
                            "recommended_version": {
                                "id": 200,
                                "version": "2.0.0",
                                "link": link,
                                "content_length": len(body),
                                "fika_compatibility": "unknown",
                                "spt_versions": [SPT_VERSION],
                            },
                            "update_reason": "newer version available",
                        }
                    ],
                    "blocked_updates": [],
                    "up_to_date": [],
                    "incompatible_with_spt": [],
                }
            ),
        )
    )
    respx_router.get(link).mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )

    services.update_mod(clean_repo.path, "NativeMod", db_path=db_path, client=client_factory())

    assert start.read_text(encoding="utf-8") == "start 2.0.0"
    assert _is_executable(start)
    assert clean_repo.is_clean()
