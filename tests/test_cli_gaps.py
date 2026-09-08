"""What ``sptmod`` prints and does on the command paths the other CLI modules leave out.

Covers the console-script entry point, ``snapshot``'s report of what it secured,
``install``'s refusal of contradictory targets and its Forge plan/confirm/install output,
``update``'s skipped, blocked, incompatible, up-to-date and already-current answers,
``disable``/``enable`` on a mod that is already in the requested state, ``export``'s
format validation, ``browse``'s filters, ``status``'s two summaries, ``check``'s warnings
and ``init``'s per-mod adopt question.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from conftest import clean_repo_at
from fixture_types import InstallCommit, MakeArchive, TrailerInstallCommit
from forge_json import (
    current_version_json,
    mod_json_full,
    owner_json,
    page_envelope,
    success_envelope,
    version_json,
)
from sptmod import __version__
from sptmod.cli import app, main
from sptmod.core.repo import Repo

runner = CliRunner()

SPT_VERSION = "4.1.0"
SPT_CONSTRAINT = "^4.0.0"


def _mod_json(mod_id: int, name: str, version: str) -> dict[str, object]:
    """Build a Forge mod JSON envelope for a given ID, name and version."""
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
    respx_router.get(f"/mod/{mod_id}/versions").mock(return_value=httpx.Response(200, json=versions_page))

    def _empty_dependencies(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    respx_router.get("/mods/dependencies").mock(side_effect=_empty_dependencies)


def _updates_payload(kind: str, *, mod_id: int, name: str, version: str) -> dict[str, object]:
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
                "current_version": current_version_json(mod_id=mod_id, name=name, version=version, version_id=1),
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


# --- main(): the console-script entry point ----------------------------------


def test_main_runs_the_app_with_the_real_process_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The installed ``sptmod`` command reads its arguments from the process itself.

    Sets the process argument list to ``sptmod version`` and calls the console-script
    entry point directly instead of going through ``CliRunner``; asserts it exits 0 and
    prints the version banner. Reason: this is the entry point a user's shell invokes, so
    it has to work without a test harness handing it an argument list.
    """
    monkeypatch.setattr(sys, "argv", ["sptmod", "version"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    assert f"sptmod {__version__}" in capsys.readouterr().out


# --- snapshot ---------------------------------------------------------------


def test_snapshot_commits_the_modified_profile_and_reports_it(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``snapshot`` secures a changed player profile and names the snapshot it wrote.

    Writes a profile file into a clean game directory and runs ``snapshot --message``;
    asserts exit 0, a ``snapshot: after a raid`` line on stdout and exactly one new commit.
    Reason: the user needs the confirmation to know their raid result is safe.
    """
    repo = clean_repo_at(fake_game_dir)
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


def test_snapshot_reports_the_secured_game_files_and_mod_settings(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """``snapshot`` names both classes it secured and prints no snapshot line without one.

    A game file an SPT update rewrote and a setting an active mod rewrote are the only
    changes, so the CLI must report both counts and stay silent about the profile snapshot,
    which never happened. Without the counts the user has no idea the 1130 changed game
    files were taken care of.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(
        repo,
        "ClientMod",
        files={
            "BepInEx/plugins/ClientMod/ClientMod.dll": "v1",
            "BepInEx/plugins/ClientMod/config.json": '{"v": 1}',
        },
    )
    spt_dll = fake_game_dir / "BepInEx" / "plugins" / "spt" / "spt-common.dll"
    spt_dll.parent.mkdir(parents=True)
    spt_dll.write_bytes(b"4.1.0-build")
    repo.commit("chore: seed SPT client files", paths=[Path("BepInEx/plugins/spt/spt-common.dll")])
    spt_dll.write_bytes(b"4.0.0-build")
    (fake_game_dir / "BepInEx" / "plugins" / "ClientMod" / "config.json").write_text('{"v": 2}', encoding="utf-8")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["snapshot"])

    assert result.exit_code == 0
    assert "secured 1 updated game files" in result.stdout
    assert "secured 1 changed mod settings" in result.stdout
    assert "snapshot:" not in result.stdout


# --- install: BadParameter guards -------------------------------------------


def test_install_rejects_both_a_mod_argument_and_file(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, make_archive: MakeArchive
) -> None:
    """``install`` refuses a mod name and an archive at the same time.

    Runs ``install SomeMod --file <archive>``; asserts a non-zero exit and a message
    telling the user to pass one or the other. Reason: the two targets mean different
    installs, so guessing one silently would install something the user did not ask for.
    """
    clean_repo_at(fake_game_dir)
    archive = make_archive({"BepInEx/plugins/CoolPlugin.dll": b"dll-bytes"}, "zip")
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["install", "SomeMod", "--file", str(archive)])

    assert result.exit_code != 0
    assert "pass either MOD or --file, not both" in result.output


def test_install_rejects_neither_a_mod_argument_nor_file(fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``install`` with no target at all says what it needs.

    Runs ``install`` without a mod name and without ``--file``; asserts a non-zero exit
    and a message naming both ways to say what to install. Reason: an install with nothing
    to install must end in usage help, not in a silent success.
    """
    clean_repo_at(fake_game_dir)
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
    """Declining the install plan installs nothing.

    Seeds a Forge mod into the catalog, runs ``install "Target Mod"`` and answers "n" at
    the plan confirmation; asserts exit 0, a "cancelled" line and an unchanged commit
    history. Reason: the plan is shown so the user can say no, and saying no must leave
    the installation untouched.
    """
    repo = clean_repo_at(fake_game_dir)
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
    make_archive: MakeArchive,
) -> None:
    """A confirmed Forge install names every mod it installed with its version.

    Seeds a Forge mod whose download link serves a real archive and runs
    ``install "Target Mod" --yes``; asserts exit 0 and an ``installed Target Mod 2.0.0``
    line. Reason: the install report is the only place the user learns which version
    actually landed.
    """
    clean_repo_at(fake_game_dir)
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
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
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
    """A Forge version without a reported download size still gets a readable plan.

    Seeds a mod whose version carries no content length, runs ``install "Sizeless Mod"``
    and declines; asserts exit 0, the mod named in the plan, the cancellation line, and no
    literal "None" anywhere in the output. Reason: the Forge omits the size for some
    versions, and the plan must stay readable rather than showing "None" or failing.
    """
    clean_repo_at(fake_game_dir)
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
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_commit: TrailerInstallCommit,
) -> None:
    """``update`` names the mods it cannot look up and then reports nothing to update.

    Installs a mod carrying no Forge ID and runs ``update``; asserts exit 0, a line naming
    the mod as not identified on Forge, and the "nothing to update" summary. Reason: a mod
    installed from an archive can never be updated, and the user has to be told which one
    was passed over rather than silently ignored.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "Ghost Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": ""})
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
    install_commit: TrailerInstallCommit,
) -> None:
    """``update --check`` shows a row for every state the Forge reports back.

    Installs a Forge-identified mod, mocks the update endpoint to return it in each of the
    blocked, incompatible and up-to-date lists in turn, and runs ``update --check``;
    asserts the state text appears in the table each time. Reason: a mod missing from the
    table would read as "no answer", so every state the Forge knows must be shown.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "Some Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "77"})
    respx_router.get("/mods/updates").mock(
        return_value=httpx.Response(
            200,
            json=success_envelope(_updates_payload(kind, mod_id=77, name="Some Mod", version="1.0.0")),
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
    install_commit: TrailerInstallCommit,
) -> None:
    """An update that the Forge withdraws between plan and download is reported, not
    silently skipped.

    Mocks the update endpoint to offer version 2.0.0 on the first call, which the user
    confirms, and to report the mod as up to date on the second call the download makes;
    asserts exit 0, a notice saying the mod is up to date and an unchanged commit history.
    Reason: the user confirmed an update that then did not happen, so the outcome has to
    be stated instead of leaving a silent success behind.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "10"})
    available = {
        "spt_version": SPT_VERSION,
        "updates": [
            {
                "current_version": current_version_json(mod_id=10, name="CoolMod", version="1.0.0", version_id=100),
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
    up_to_date = _updates_payload("up_to_date", mod_id=10, name="CoolMod", version="1.0.0")
    calls = {"n": 0}

    def _side_effect(_request: httpx.Request) -> httpx.Response:
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


@pytest.mark.parametrize(
    ("setup_command", "command", "expected_notice"),
    [
        ("disable", "disable", "notice: CoolMod is already disabled, nothing to disable"),
        (None, "enable", "notice: CoolMod is installed, not disabled, nothing to enable"),
    ],
    ids=["disable-already-disabled", "enable-not-disabled"],
)
def test_toggling_a_mod_into_the_state_it_is_already_in_prints_a_notice(
    setup_command: str | None,
    command: str,
    expected_notice: str,
    fake_game_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_commit: InstallCommit,
) -> None:
    """Disabling a disabled mod or enabling an active one says so instead of pretending
    to have changed something.

    Installs "CoolMod", optionally disables it first, then runs the toggle command that
    would move it into the state it already has; asserts exit 0 and a notice naming the
    current state. Reason: reporting a change that never happened would make the user
    believe a stale mod state was fixed.
    """
    repo = clean_repo_at(fake_game_dir)
    install_commit(repo, "CoolMod")
    monkeypatch.chdir(fake_game_dir)
    if setup_command is not None:
        assert runner.invoke(app, [setup_command, "CoolMod"]).exit_code == 0

    result = runner.invoke(app, [command, "CoolMod"])

    assert result.exit_code == 0
    assert expected_notice in result.stdout


# --- export: invalid format --------------------------------------------------


def test_export_rejects_an_unknown_format(fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``export`` rejects a format it cannot write and lists the ones it can.

    Runs ``export --format yaml``; asserts a non-zero exit and a message naming text and
    json. Reason: writing nothing, or writing the default format under a name the user did
    not ask for, would both be worse than a usage error.
    """
    clean_repo_at(fake_game_dir)
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
    install_commit: TrailerInstallCommit,
) -> None:
    """``browse --available`` hides the mods that are already installed.

    Syncs two catalog mods, installs one of them, and runs ``browse --available``; asserts
    exit 0, the free mod listed and the installed one absent. Reason: the flag exists to
    show what is still worth installing.
    """
    repo = clean_repo_at(fake_game_dir)
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
    install_commit(repo, "Installed Mod", "1.0.0", mod_type="server", **{"Mod-Forge-Id": "1"})

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
    """``browse`` without a filter flag lists everything the catalog holds.

    Syncs one mod into the catalog and runs ``browse`` with no flags; asserts exit 0 and
    the cached mod in the output. Reason: the bare command is the entry point into the
    catalog, so it must default to showing all of it.
    """
    clean_repo_at(fake_game_dir)
    respx_router.get("/mods").mock(
        return_value=httpx.Response(200, json=page_envelope([_mod_json(1, "Cached Mod", "1.0.0")], total=1))
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
    """``status`` either lists what changed or says that nothing did.

    Runs ``status`` once on a game directory with a rewritten player profile and once on
    an untouched one; asserts exit 0 and, respectively, the modified-profiles headline and
    the "nothing changed" summary. Reason: a status command that prints nothing at all
    leaves the user unsure whether it even ran.
    """
    clean_repo_at(fake_game_dir)
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
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, install_commit: InstallCommit
) -> None:
    """``check`` warns about a mod database file it cannot parse.

    Installs a server mod whose ``db`` folder holds a file that is not valid JSON and runs
    ``check``; asserts exit 0 and a ``warning:`` line. Reason: an unreadable database file
    breaks the mod at runtime, and the check is where the user is supposed to find out.
    """
    repo = clean_repo_at(fake_game_dir)
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
    """Create a pre-existing mod folder with a package.json file."""
    mod_dir = fake_game_dir / "SPT" / "user" / "mods" / name
    mod_dir.mkdir(parents=True)
    (mod_dir / "package.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "author": "Acme"}),
        encoding="utf-8",
    )
    return mod_dir


@pytest.mark.parametrize(
    ("extra_args", "stdin", "expected_text", "expected_commits"),
    [
        ([], "y\n", "adopted CoolMod 1.0.0", 2),
        ([], "n\n", "left untracked, run 'sptmod adopt SPT/user/mods/CoolMod' later", 1),
        (["--yes"], "", "adopted CoolMod 1.0.0", 2),
    ],
    ids=["prompt-yes-adopts", "prompt-no-leaves-untracked", "yes-flag-adopts-without-prompt"],
)
def test_init_asks_about_every_pre_existing_mod_and_follows_the_answer(
    extra_args: list[str],
    stdin: str,
    expected_text: str,
    expected_commits: int,
    fake_game_dir: Path,
) -> None:
    """``init`` offers to take an already-present mod under management and obeys the answer.

    Puts a mod folder into an untracked game directory and runs ``init`` answering "y",
    answering "n", and passing ``--yes`` with no input at all; asserts exit 0 each time,
    the matching confirmation or hint on stdout, and a second commit only where the mod
    was adopted. Reason: a user who already installed mods by hand decides per mod what
    sptmod takes over, and ``--yes`` answers for them without needing a terminal.
    """
    _write_pre_existing_mod(fake_game_dir)

    result = runner.invoke(app, ["init", str(fake_game_dir), *extra_args], input=stdin)

    assert result.exit_code == 0
    assert expected_text in result.stdout
    assert len(Repo(fake_game_dir).log()) == expected_commits
