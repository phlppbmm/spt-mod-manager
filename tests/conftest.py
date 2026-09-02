"""Shared fixtures for the sptmod test suite."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from sptmod.core.repo import Repo


@pytest.fixture(autouse=True)
def isolate_git_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Isolate every test from the developer machine's git configuration.

    Points HOME and the global/system git config files at locations that do
    not exist, so a machine-global ``user.name``/``user.email`` (or any other
    global git setting) can never leak into a test. Repo operations must work
    purely from the local, per-repository identity set up by ``Repo.init``.
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_home / "does-not-exist-gitconfig"))
    monkeypatch.setenv(
        "GIT_CONFIG_SYSTEM", str(fake_home / "does-not-exist-gitconfig-system")
    )
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_game_dir(tmp_path: Path) -> Path:
    """Build a minimal, realistic SPT game directory.

    Layout:
      SPT/user/mods/                        (empty, trackable)
      SPT/user/profiles/abc.json            (minimal profile, trackable)
      SPT/user/profiles/backups/old.json    (dummy, must stay ignored)
      SPT/user/logs/x.log                   (dummy, must stay ignored)
      SPT/SPT_Data/configs/core.json        (sptVersion, read directly, untracked)
      BepInEx/plugins/                      (empty, trackable)
      BepInEx/config/                       (empty, trackable)
      EscapeFromTarkov_Data/foo              (dummy, must stay ignored)

    Reused by every task building on top of the git layer.
    """
    root = tmp_path / "game"
    (root / "SPT" / "user" / "mods").mkdir(parents=True)
    (root / "SPT" / "user" / "profiles" / "backups").mkdir(parents=True)
    (root / "SPT" / "user" / "logs").mkdir(parents=True)
    (root / "SPT" / "SPT_Data" / "configs").mkdir(parents=True)
    (root / "BepInEx" / "plugins").mkdir(parents=True)
    (root / "BepInEx" / "config").mkdir(parents=True)
    (root / "EscapeFromTarkov_Data").mkdir(parents=True)

    profile = {
        "Inventory": {"items": []},
        "TradersInfo": {},
        "Quests": [],
    }
    (root / "SPT" / "user" / "profiles" / "abc.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )

    core_config = {"sptVersion": "4.1.0"}
    (root / "SPT" / "SPT_Data" / "configs" / "core.json").write_text(
        json.dumps(core_config), encoding="utf-8"
    )

    (root / "SPT" / "user" / "logs" / "x.log").write_text(
        "dummy log line\n", encoding="utf-8"
    )
    (root / "EscapeFromTarkov_Data" / "foo").write_text(
        "dummy game data\n", encoding="utf-8"
    )
    (root / "SPT" / "user" / "profiles" / "backups" / "old.json").write_text(
        "{}", encoding="utf-8"
    )

    return root


@pytest.fixture
def initialized_repo(fake_game_dir: Path) -> Repo:
    """A ``Repo`` freshly initialized on top of :func:`fake_game_dir`."""
    return Repo.init(fake_game_dir)


@pytest.fixture
def committed_repo(initialized_repo: Repo) -> Iterator[tuple[Repo, str]]:
    """A repo with one commit tracking a single mod file.

    Yields ``(repo, first_commit_hash)``. ``SPT/user/mods/TestMod/mod.dll`` is
    the tracked file added by the initial commit.
    """
    mod_dir = initialized_repo.path / "SPT" / "user" / "mods" / "TestMod"
    mod_dir.mkdir(parents=True)
    (mod_dir / "mod.dll").write_bytes(b"binary-mod-content-v1")

    commit_hash = initialized_repo.commit(
        "mod: TestMod 1.0.0",
        trailers={
            "Mod": "TestMod",
            "Mod-Version": "1.0.0",
            "Mod-Type": "client",
            "Mod-Author": "tester",
            "Mod-SPT": "~4.1.0",
            "Mod-Source": "https://example.invalid/TestMod-1.0.0.7z",
            "Mod-Forge-Id": "1",
            "Mod-Forge-Version-Id": "2",
            "Mod-Guid": "",
            "Mod-State": "installed",
        },
    )
    yield initialized_repo, commit_hash


@pytest.fixture
def make_archive(tmp_path: Path):
    """Factory building a real archive file from ``{relpath: content}`` entries.

    Returns a callable ``make_archive(files, fmt) -> Path`` where ``fmt`` is
    ``"zip"`` (stdlib ``zipfile``) or ``"7z"`` (``py7zr``). ``content`` may be
    ``str`` (UTF-8 encoded) or ``bytes``. Member names are written exactly as
    given -- including unsafe ones for the zip backend -- so the same factory
    covers both roundtrip and path-traversal tests.

    ``fmt="rar"`` is intentionally unsupported: building a real ``.rar``
    archive needs the proprietary ``rar`` command line tool, which is not
    available in this environment. Tests exercising rar handling must skip
    via ``rar_backend_available()`` instead of faking a rar as a zip.
    """
    import itertools

    counter = itertools.count()

    def _make(files: dict[str, str | bytes], fmt: str) -> Path:
        archive_path = tmp_path / f"archive-{next(counter)}.{fmt}"
        if fmt == "zip":
            import zipfile

            with zipfile.ZipFile(archive_path, "w") as zf:
                for relpath, content in files.items():
                    data = (
                        content.encode("utf-8") if isinstance(content, str) else content
                    )
                    zf.writestr(relpath, data)
        elif fmt == "7z":
            import py7zr

            staging = tmp_path / f"stage-{next(counter)}"
            staging.mkdir()
            with py7zr.SevenZipFile(archive_path, "w") as zf:
                for member_index, (relpath, content) in enumerate(files.items()):
                    data = (
                        content.encode("utf-8") if isinstance(content, str) else content
                    )
                    # The staged file lives under a flat, safe name -- only
                    # the arcname passed to py7zr carries the (possibly
                    # unsafe, e.g. "../evil.txt") relpath. This keeps a
                    # traversal-member archive from writing outside the
                    # staging dir while it is still being *built*, which
                    # would otherwise make extraction tests unable to tell
                    # apart a file written by extract() from one written by
                    # this fixture.
                    src = staging / f"member-{member_index}"
                    src.write_bytes(data)
                    zf.write(src, arcname=relpath)
        elif fmt == "rar":
            raise NotImplementedError(
                "creating .rar archives needs the 'rar' CLI tool, which is "
                "not available in this environment; gate rar tests on "
                "rar_backend_available() instead of calling make_archive "
                "with fmt='rar'"
            )
        else:
            raise ValueError(f"unsupported archive format: {fmt!r}")
        return archive_path

    return _make
