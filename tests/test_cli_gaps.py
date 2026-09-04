"""CLI coverage gaps identified by the group-4 audit (see project scratchpad).

Each test below pins one command or branch of ``cli.py`` that was reachable
through the public CLI but had no test anywhere in the suite: ``version``,
``snapshot``, ``install``'s two ``BadParameter`` guards and its Forge-path
decline/success prints, ``update``'s skipped/nothing-to-update/blocked/
incompatible/up-to-date rows and its notice branch, ``disable``/``enable``'s
notice branch, ``export``'s invalid-format guard, ``browse``'s
``available``/default-filter branches, ``status``'s non-empty/nothing-changed
branches, the nullable-``content_length`` size column, and ``init``'s
interactive per-mod adopt loop.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from sptmod.cli import app, main
from sptmod.core import game_dir
from sptmod.core.game_dir import detect_layout
from sptmod.core.repo import Repo
from test_forge_client_fixtures import (
    mod_json_full,
    owner_json,
    page_envelope,
    respx_router,
    success_envelope,
    version_json,
)

__all__ = ["respx_router"]

runner = CliRunner()

SPT_VERSION = "4.1.0"
SPT_CONSTRAINT = "^4.0.0"


@pytest.fixture(autouse=True)
def not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])


@pytest.fixture
def isolated_default_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``default_db_path()`` at an isolated location for CLI tests.

    The CLI never passes ``db_path`` explicitly, so it resolves through
    ``forge.catalog.default_db_path()``, which must not touch the developer's
    real config directory.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    return config_dir / "sptmod" / "forge-catalog.db"


def _clean_repo(path: Path) -> Repo:
    repo = Repo.init(detect_layout(path))
    repo.commit(
        f"chore: clean SPT {SPT_VERSION}",
        trailers={"Spt-Version": SPT_VERSION},
        paths=None,
    )
    return repo


def _mod_json(mod_id: int, name: str, version: str) -> dict[str, object]:
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name="Alice"),
        versions=[
            {
                "id": mod_id * 100,
                "version": version,
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )


def _seed_forge_mod(
    respx_router: respx.MockRouter,
    *,
    mod_id: int,
    name: str,
    version: str,
    version_id: int,
    link: str | None,
    content_length: int | None,
) -> None:
    """Mock the sync page, the version list and an empty dependency tree.

    Enough for ``sync`` to cache ``name``, and for ``plan_mod_install`` /
    ``install_mod`` to resolve it end to end without any real dependencies.
    The synced summary and the fetched version list share ``version_id``, so
    ``_with_link`` (matched by that ID) actually fills in the link.
    """
    mod = mod_json_full(
        id=mod_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        owner=owner_json(id=mod_id * 10, name="Alice"),
        versions=[
            {
                "id": version_id,
                "version": version,
                "spt_version_constraint": SPT_CONSTRAINT,
                "downloads": 1,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    page = page_envelope([mod], total=1)
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page))

    versions_page = page_envelope(
        [
            version_json(
                id=version_id,
                version=version,
                link=link,
                content_length=content_length,
                spt_version_constraint=SPT_CONSTRAINT,
            )
        ],
        total=1,
    )
    respx_router.get(f"/mod/{mod_id}/versions").mock(
        return_value=httpx.Response(200, json=versions_page)
    )

    def _empty_dependencies(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    respx_router.get("/mods/dependencies").mock(side_effect=_empty_dependencies)


def _current_version(
    *, mod_id: int, name: str, version: str, version_id: int
) -> dict[str, object]:
    return {
        "id": version_id,
        "mod_id": mod_id,
        "guid": None,
        "name": name,
        "version": version,
        "slug": name.lower().replace(" ", "-"),
    }


def _updates_payload(
    kind: str, *, mod_id: int, name: str, version: str
) -> dict[str, object]:
    """A ``/mods/updates`` envelope with exactly one entry, in the list ``kind``."""
    payload: dict[str, object] = {
        "spt_version": SPT_VERSION,
        "updates": [],
        "blocked_updates": [],
        "up_to_date": [],
        "incompatible_with_spt": [],
    }
    if kind == "blocked":
        payload["blocked_updates"] = [
            {
                "current_version": _current_version(
                    mod_id=mod_id, name=name, version=version, version_id=1
                ),
                "latest_version": {
                    "id": 2,
                    "version": "2.0.0",
                    "spt_versions": [SPT_VERSION],
                },
                "block_reason": "Conflicting mod dependency.",
                "blocking_mods": [
                    {
                        "mod_id": 999,
                        "mod_guid": None,
                        "mod_name": "Other Mod",
                        "current_version": "1.0.0",
                        "constraint": "<2.0.0",
                        "incompatible_with": "2.0.0",
                    }
                ],
            }
        ]
    elif kind == "incompatible":
        payload["incompatible_with_spt"] = [
            {
                "id": 3,
                "mod_id": mod_id,
                "guid": None,
                "name": name,
                "version": version,
                "reason": "No version for this SPT release.",
                "latest_compatible_version": "1.0.0",
            }
        ]
    else:
        payload["up_to_date"] = [
            {
                "id": 4,
                "mod_id": mod_id,
                "guid": None,
                "name": name,
                "version": version,
                "spt_versions": [SPT_VERSION],
            }
        ]
    return payload


# --- version --------------------------------------------------------------


def test_version_prints_the_version_string() -> None:
    """If ``version``'s body is `pass`, this fails: nothing is printed."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "sptmod 0.1.0" in result.stdout


# --- main(): the console-script entry point ----------------------------------


def test_main_runs_the_app_with_the_real_process_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercises ``main()`` itself (not ``app`` via ``CliRunner``): if its
    body is `pass`, this fails because ``app()`` (and its 'sptmod 0.1.0'
    output) never runs at all, so no ``SystemExit`` is raised either."""
    monkeypatch.setattr(sys, "argv", ["sptmod", "version"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    assert "sptmod 0.1.0" in capsys.readouterr().out


# --- snapshot ---------------------------------------------------------------


def test_snapshot_commits_the_modified_profile_and_reports_it(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``snapshot``'s print line is removed, this fails: no ``snapshot:``
    line appears even though the commit itself still happens."""
    repo = _clean_repo(fake_game_dir)
    commits_before = len(repo.log())
    monkeypatch.chdir(fake_game_dir)
    profile = fake_game_dir / "SPT" / "user" / "profiles" / "abc.json"
    profile.write_text(
        json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["snapshot", "--message", "after a raid"])

    assert result.exit_code == 0
    assert "snapshot: after a raid" in result.stdout
    assert len(repo.log()) == commits_before + 1


# --- install: BadParameter guards -------------------------------------------


def test_install_rejects_both_a_mod_argument_and_file(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive
) -> None:
    """If the ``MOD``/``--file`` exclusivity check is removed, this fails:
    exit code stays 0 instead of the BadParameter usage error."""
    _clean_repo(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "SomeMod", "--file", str(archive)])

    assert result.exit_code != 0
    assert "pass either MOD or --file, not both" in result.output


def test_install_rejects_neither_a_mod_argument_nor_file(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the missing-target check is removed, this fails: exit code stays 0
    instead of the BadParameter usage error."""
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install"])

    assert result.exit_code != 0
    assert "pass a mod ID or name, or --file ARCHIVE" in result.output


# --- install: Forge path -----------------------------------------------------


def test_install_forge_path_declining_the_plan_cancels_without_a_commit(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
) -> None:
    """If the plan-confirmation prompt is removed, this fails: the mod gets
    installed (one extra commit) even though the simulated user answered
    'n'."""
    repo = _clean_repo(fake_game_dir)
    commits_before = len(repo.log())
    _seed_forge_mod(
        respx_router,
        mod_id=501,
        name="Target Mod",
        version="2.0.0",
        version_id=5010,
        link="https://dl.example/target-mod-2.0.0.zip",
        content_length=1000,
    )
    monkeypatch.chdir(fake_game_dir)
    runner.invoke(app, ["sync", "--full"])

    result = runner.invoke(app, ["install", "Target Mod"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.stdout
    assert len(repo.log()) == commits_before


def test_install_forge_path_success_prints_each_installed_mod(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
    make_archive,
) -> None:
    """If the ``for installed in result.installed: _print_install(...)`` loop
    is removed, this fails: the CLI's own install output never names the
    mod even though the commit (checked via the RPC-covered path) succeeds."""
    _clean_repo(fake_game_dir)
    archive = make_archive(
        {
            "SPT/user/mods/Target Mod/package.json": json.dumps(
                {"name": "Target Mod", "version": "2.0.0", "author": "Acme"}
            )
        },
        "zip",
    )
    body = archive.read_bytes()
    link = "https://dl.example/target-mod-2.0.0-success.zip"
    _seed_forge_mod(
        respx_router,
        mod_id=502,
        name="Target Mod",
        version="2.0.0",
        version_id=5020,
        link=link,
        content_length=len(body),
    )
    respx_router.get(link).mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Length": str(len(body))}
        )
    )
    monkeypatch.chdir(fake_game_dir)
    runner.invoke(app, ["sync", "--full"])

    result = runner.invoke(app, ["install", "Target Mod", "--yes"])

    assert result.exit_code == 0
    assert "installed Target Mod 2.0.0" in result.stdout


def test_install_plan_with_no_reported_size_renders_without_crashing(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
) -> None:
    """Pins ``_size(None)``: if the ``None`` guard is removed, dividing
    ``None`` by ``_BYTES_PER_MB`` raises a ``TypeError`` before the plan table
    (and the cancel prompt after it) is ever reached, so this fails."""
    _clean_repo(fake_game_dir)
    _seed_forge_mod(
        respx_router,
        mod_id=503,
        name="Sizeless Mod",
        version="1.0.0",
        version_id=5030,
        link="https://dl.example/sizeless-mod-1.0.0.zip",
        content_length=None,
    )
    monkeypatch.chdir(fake_game_dir)
    runner.invoke(app, ["sync", "--full"])

    result = runner.invoke(app, ["install", "Sizeless Mod"], input="n\n")

    assert result.exit_code == 0
    assert "Sizeless Mod" in result.stdout
    assert "cancelled" in result.stdout
    assert "None" not in result.stdout


# --- update: skipped / nothing to update ------------------------------------


def test_update_reports_a_mod_forge_cannot_identify_as_skipped_and_stops(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    """If the ``for skipped in report.skipped`` loop, or the empty-report
    'nothing to update' print, is removed, this fails: neither line appears
    even though the mod carries no Forge ID and Forge lists nothing to
    apply."""
    repo = _clean_repo(fake_game_dir)
    install_commit(
        repo, "Ghost Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": ""}
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["update"], input="")

    assert result.exit_code == 0
    assert "not identified on Forge: Ghost Mod" in result.stdout
    assert "nothing to update" in result.stdout


# --- update: blocked / incompatible / up-to-date rows -----------------------


@pytest.mark.parametrize(
    ("kind", "expected_row_text"),
    [
        ("blocked", "blocked"),
        ("incompatible", "incompatible"),
        ("up_to_date", "up to date"),
    ],
    ids=["blocked-row", "incompatible-row", "up-to-date-row"],
)
def test_update_check_renders_the_blocked_incompatible_and_uptodate_rows(
    kind: str,
    expected_row_text: str,
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    install_commit,
) -> None:
    """If ``_updates_table``'s loop for this list is removed, this fails: the
    row's state text never appears even though Forge reported an entry."""
    repo = _clean_repo(fake_game_dir)
    install_commit(
        repo, "Some Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "77"}
    )
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(
            200,
            json=success_envelope(
                _updates_payload(kind, mod_id=77, name="Some Mod", version="1.0.0")
            ),
        )
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["update", "--check"])

    assert result.exit_code == 0
    assert expected_row_text in result.stdout


def test_update_reports_a_notice_when_forge_no_longer_offers_the_update(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    install_commit,
) -> None:
    """Pins the TOCTOU no-op path: ``update`` queries Forge once to display
    the plan and confirm, then ``update_mod`` re-queries before downloading.
    If ``_print_update``'s notice branch is removed, this fails: the second
    (up-to-date) answer's ``notice`` is silently dropped instead of printed."""
    repo = _clean_repo(fake_game_dir)
    install_commit(
        repo, "CoolMod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "10"}
    )
    available = {
        "spt_version": SPT_VERSION,
        "updates": [
            {
                "current_version": _current_version(
                    mod_id=10, name="CoolMod", version="1.0.0", version_id=100
                ),
                "recommended_version": {
                    "id": 200,
                    "version": "2.0.0",
                    "link": "https://dl.example/coolmod-2.0.0.zip",
                    "content_length": 1,
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
    up_to_date = _updates_payload(
        "up_to_date", mod_id=10, name="CoolMod", version="1.0.0"
    )
    calls = {"n": 0}

    def _side_effect(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        data = available if calls["n"] == 1 else up_to_date
        return httpx.Response(200, json=success_envelope(data))

    respx_router.get("/mods/updates").mock(side_effect=_side_effect)
    commits_before = len(repo.log())
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["update"], input="y\n")

    assert result.exit_code == 0
    assert "notice: CoolMod 1.0.0 is up to date" in result.stdout
    assert len(repo.log()) == commits_before


# --- disable / enable: notice branch -----------------------------------------


def test_disabling_an_already_disabled_mod_prints_a_notice(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    """If ``_print_toggle``'s notice branch is removed, this fails: the
    second disable prints the normal 'disabled ...' line (with an empty
    commit hash) instead of the notice."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    first = runner.invoke(app, ["disable", "CoolMod"])
    assert first.exit_code == 0

    result = runner.invoke(app, ["disable", "CoolMod"])

    assert result.exit_code == 0
    assert "notice: CoolMod is already disabled, nothing to disable" in result.stdout


def test_enabling_an_already_active_mod_prints_a_notice(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    """If ``_print_toggle``'s notice branch is removed, this fails: enabling
    a never-disabled mod prints the normal 'enabled ...' line instead of the
    notice."""
    repo = _clean_repo(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["enable", "CoolMod"])

    assert result.exit_code == 0
    assert (
        "notice: CoolMod is installed, not disabled, nothing to enable"
        in result.stdout
    )


# --- export: invalid format --------------------------------------------------


def test_export_rejects_an_unknown_format(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``_export_format``'s raise is removed, this fails: exit code stays
    0 instead of the BadParameter usage error."""
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["export", "--format", "yaml"])

    assert result.exit_code != 0
    assert "--format must be one of text, json" in result.output


# --- browse: available / default filter --------------------------------------


def test_browse_available_filter_excludes_the_installed_mod(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
    install_commit,
) -> None:
    """If ``_browse_filter``'s ``available`` branch is removed, ``available``
    falls through to the default ``all`` filter, so this fails: the installed
    mod (which ``available`` must exclude) still appears."""
    repo = _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200,
            json=page_envelope(
                [
                    _mod_json(1, "Installed Mod", "1.0.0"),
                    _mod_json(2, "Free Mod", "1.0.0"),
                ],
                total=2,
            ),
        )
    )
    monkeypatch.chdir(fake_game_dir)
    runner.invoke(app, ["sync", "--full"])
    install_commit(
        repo, "Installed Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"}
    )

    result = runner.invoke(app, ["browse", "--available"])

    assert result.exit_code == 0
    assert "Free Mod" in result.stdout
    assert "Installed Mod" not in result.stdout


def test_browse_with_no_filter_flag_lists_every_cached_mod(
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    isolated_default_db_path: Path,
) -> None:
    """If ``_browse_filter``'s default-``all`` fallthrough is removed (e.g.
    it returns ``None``), this fails: ``browse`` with no flags crashes or
    lists nothing instead of showing the cached mod."""
    _clean_repo(fake_game_dir)
    respx_router.get("/mods").mock(
        return_value=httpx.Response(
            200, json=page_envelope([_mod_json(1, "Cached Mod", "1.0.0")], total=1)
        )
    )
    monkeypatch.chdir(fake_game_dir)
    runner.invoke(app, ["sync", "--full"])

    result = runner.invoke(app, ["browse"])

    assert result.exit_code == 0
    assert "Cached Mod" in result.stdout


# --- status: non-empty and nothing-changed branches --------------------------


@pytest.mark.parametrize(
    ("write_profile", "expected"),
    [
        (True, "modified profiles"),
        (False, "nothing changed"),
    ],
    ids=["dirty-profile", "clean-tree"],
)
def test_status_reports_dirty_profiles_or_nothing_changed(
    write_profile: bool,
    expected: str,
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``_print_paths``'s early return is inverted (always returns), the
    dirty-profile case fails: the modified-profiles headline never appears.
    If the ``not any(...)`` guard in ``_print_status`` is removed, the
    clean-tree case fails: no summary line appears instead of 'nothing
    changed'."""
    _clean_repo(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    if write_profile:
        profile = fake_game_dir / "SPT" / "user" / "profiles" / "abc.json"
        profile.write_text(
            json.dumps({"Inventory": {"items": [1]}, "TradersInfo": {}, "Quests": []}),
            encoding="utf-8",
        )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert expected in result.stdout


# --- check: warnings branch ---------------------------------------------------


def test_check_reports_a_warning_for_an_unparseable_db_file(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit
) -> None:
    """If the ``for warning in result.warnings`` loop is removed, this fails:
    no warning line appears even though the mod's db file is not valid JSON."""
    repo = _clean_repo(fake_game_dir)
    install_commit(
        repo,
        "CoolMod",
        "1.0.0",
        mod_type="server",
        files={
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.0.0", "author": "Acme"}
            ),
            "SPT/user/mods/CoolMod/db/items/broken.json": "not valid json {",
        },
    )
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0
    assert "warning:" in result.stdout


# --- init: interactive per-mod adopt loop ------------------------------------


def _write_pre_existing_mod(fake_game_dir: Path, name: str = "CoolMod") -> Path:
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / name
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "author": "Acme"}),
        encoding="utf-8",
    )
    return mod_dir


def test_init_prompts_per_pre_existing_mod_and_adopts_on_yes(
    fake_game_dir: Path,
) -> None:
    """If the per-mod adopt loop is removed, this fails: neither the prompt's
    'adopted' confirmation nor a commit for the mod appears."""
    _write_pre_existing_mod(fake_game_dir)

    result = runner.invoke(app, ["init", str(fake_game_dir)], input="y\n")

    assert result.exit_code == 0
    assert "adopted CoolMod 1.0.0" in result.stdout
    assert len(Repo(fake_game_dir).log()) == 2


def test_init_prompts_per_pre_existing_mod_and_leaves_it_untracked_on_no(
    fake_game_dir: Path,
) -> None:
    """If the per-mod adopt loop's decline branch prints nothing (or adopts
    anyway), this fails: neither the 'left untracked' hint appears nor does
    the repository stay at a single commit."""
    _write_pre_existing_mod(fake_game_dir)

    result = runner.invoke(app, ["init", str(fake_game_dir)], input="n\n")

    assert result.exit_code == 0
    assert "left untracked, run 'sptmod adopt SPT/user/mods/CoolMod' later" in (
        result.stdout
    )
    assert len(Repo(fake_game_dir).log()) == 1


def test_init_yes_flag_adopts_pre_existing_mods_without_further_prompting(
    fake_game_dir: Path,
) -> None:
    """If the ``if yes: return`` early exit is removed, this fails: with an
    empty stdin the interactive per-mod loop hits EOF and aborts instead of
    exiting 0 after the ``adopted`` lines already printed by
    ``init_game_dir`` itself."""
    _write_pre_existing_mod(fake_game_dir)

    result = runner.invoke(app, ["init", str(fake_game_dir), "--yes"], input="")

    assert result.exit_code == 0
    assert "adopted CoolMod 1.0.0" in result.stdout
    assert len(Repo(fake_game_dir).log()) == 2
