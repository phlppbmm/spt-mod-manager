"""In-process ``sptmod rpc <name>`` tests for the four Fika functions.

One round trip per function plus the two error envelopes the app reacts to
(``FIKA_HOST_UNREACHABLE``, ``INVALID_METADATA``) and ``launch_profile``
accepting ``fika_path``. The behaviour of the service functions themselves is
covered in ``tests/test_services_fika.py``, ``tests/test_services_fika_install.py``
and ``tests/test_launch_fika.py``; this file pins the RPC contract.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from conftest import LaunchableGameDir, invoke_rpc, kill_launchable_processes
from forge_json import mod_json_full, owner_json, page_envelope, version_json
from sptmod.core import game_dir
from sptmod.core.forge.catalog import Catalog
from sptmod.core.forge.client import ForgeClient
from sptmod.core.game_dir import GameProcess
from sptmod.core.repo import Repo
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH, PROFILES_PATH

pytestmark = pytest.mark.usefixtures("not_running")

HOST = "https://192.0.2.10:6969"
HOST_PROFILE_ID = "aaa111hostprofile"
SPT_CONSTRAINT = "^4.0.0"


def _no_game_processes() -> list[GameProcess]:
    """Stand in for ``game_dir.game_processes`` when nothing runs."""
    return []


@pytest.fixture
def game(tmp_path: Path) -> Path:
    """A game directory the Fika settings are stored for."""
    directory = tmp_path / "game"
    directory.mkdir()
    return directory


def _host_answers(router: respx.MockRouter, *, profiles: list[dict[str, Any]]) -> None:
    """Let the host answer the ping, the profile list and the sign-in."""
    router.get(PING_PATH).respond(200, json={"Response": "Pong!"})
    router.get(PROFILES_PATH).respond(200, json={"Response": profiles})
    router.put(LOGIN_PATH).respond(200, json={"Response": True})


def _mini_profile() -> dict[str, Any]:
    """The one profile the mocked host offers."""
    return {
        "profileId": HOST_PROFILE_ID,
        "username": "HostAccount",
        "nickname": "HostNick",
        "currlvl": 42,
        "side": "Usec",
        "hasPassword": False,
    }


def _fika_half(*, mod_id: int, name: str, slug: str, version: str, version_id: int) -> dict[str, Any]:
    """The ``/mods`` entry of one half of Fika."""
    return mod_json_full(
        id=mod_id,
        name=name,
        slug=slug,
        owner=owner_json(id=mod_id * 10, name="Lacyway"),
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


def _seed_fika_catalog(
    client_factory: Callable[..., ForgeClient], respx_router: respx.MockRouter, db_path: Path
) -> None:
    """Cache both halves of Fika and answer the Forge calls the plan makes."""
    halves = [
        _fika_half(mod_id=2357, name="Project Fika - Server", slug="project-fika-server", version="2.4.0",
                   version_id=23570),
        _fika_half(mod_id=2326, name="Project Fika", slug="project-fika", version="2.4.2", version_id=23260),
    ]
    respx_router.get("/mods").mock(return_value=httpx.Response(200, json=page_envelope(halves, total=2)))
    with Catalog(db_path) as catalog:
        catalog.sync(client_factory(), full=True)
    for half in halves:
        summary = half["versions"][0]
        page = page_envelope(
            [
                version_json(
                    id=summary["id"],
                    version=summary["version"],
                    link=f"https://dl.example/{half['slug']}.zip",
                    content_length=1000,
                    spt_version_constraint=SPT_CONSTRAINT,
                )
            ],
            total=1,
        )
        respx_router.get(f"/mod/{half['id']}/versions").mock(return_value=httpx.Response(200, json=page))

    def _no_dependencies(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["mods"]
        return httpx.Response(200, json={"success": True, "data": {pair: []}})

    respx_router.get("/mods/dependencies").mock(side_effect=_no_dependencies)


# --- one round trip per function --------------------------------------------


def test_set_fika_settings_rpc_returns_the_stored_role_and_its_url(game: Path, fika_store: Path) -> None:
    """If the request fields were not passed on, this fails: the response
    would describe a directory that plays alone instead of the client and the
    URL its address leads to."""
    exit_code, lines = invoke_rpc(
        "set_fika_settings",
        {"path": str(game), "role": "client", "host_address": "192.0.2.10", "fika_path": str(fika_store)},
    )

    assert exit_code == 0
    assert lines == [
        {
            "event": "result",
            "data": {"role": "client", "host_address": "192.0.2.10", "base_url": HOST},
        }
    ]


def test_get_fika_settings_rpc_reports_what_was_stored_before(game: Path, fika_store: Path) -> None:
    """If the read were not routed to the same store, this fails: the settings
    written a moment ago would come back as a directory that plays alone."""
    invoke_rpc(
        "set_fika_settings",
        {"path": str(game), "role": "host", "host_address": "pc.local:7000", "fika_path": str(fika_store)},
    )

    exit_code, lines = invoke_rpc("get_fika_settings", {"path": str(game), "fika_path": str(fika_store)})

    assert exit_code == 0
    assert lines[0]["data"] == {
        "role": "host",
        "host_address": "pc.local:7000",
        "base_url": "https://pc.local:7000",
    }


def test_list_host_profiles_rpc_returns_the_profiles_of_the_stored_host(game: Path, fika_store: Path) -> None:
    """If the list were not serialised as the response model, this fails: the
    profiles would be missing or their fields would not carry the names the
    app reads them under."""
    invoke_rpc(
        "set_fika_settings",
        {"path": str(game), "role": "client", "host_address": "192.0.2.10", "fika_path": str(fika_store)},
    )

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        router.get(PROFILES_PATH).respond(200, json={"Response": [_mini_profile()]})

        exit_code, lines = invoke_rpc(
            "list_host_profiles", {"path": str(game), "fika_path": str(fika_store)}
        )

    assert exit_code == 0
    assert lines[0]["data"] == [
        {
            "profile_id": HOST_PROFILE_ID,
            "username": "HostAccount",
            "nickname": "HostNick",
            "level": 42,
            "side": "Usec",
            "has_password": False,
        }
    ]


def test_plan_fika_install_rpc_returns_both_halves_and_their_plan(
    clean_repo: Repo,
    client_factory: Callable[..., ForgeClient],
    respx_router: respx.MockRouter,
    db_path: Path,
) -> None:
    """If the plan were not serialised, this fails: the ids the app installs
    by, or the entries it shows for confirmation, would be missing from the
    response."""
    _seed_fika_catalog(client_factory, respx_router, db_path)

    exit_code, lines = invoke_rpc(
        "plan_fika_install", {"path": str(clean_repo.path), "db_path": str(db_path)}
    )

    assert exit_code == 0
    data = lines[0]["data"]
    assert data["server_mod_id"] == 2357
    assert data["client_mod_id"] == 2326
    assert [(entry["name"], entry["version"]) for entry in data["plan"]["entries"]] == [
        ("Project Fika - Server", "2.4.0"),
        ("Project Fika", "2.4.2"),
    ]


# --- error envelopes --------------------------------------------------------


def test_list_host_profiles_rpc_maps_an_unreachable_host_to_its_own_code(
    game: Path, fika_store: Path
) -> None:
    """If the mapping were missing, this fails: the code would default to
    SPTMOD_ERROR and the app could not offer its retry button for exactly
    this failure."""
    exit_code, lines = invoke_rpc("list_host_profiles", {"path": str(game), "fika_path": str(fika_store)})

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "FIKA_HOST_UNREACHABLE"


def test_set_fika_settings_rpc_maps_an_unusable_address_to_invalid_metadata(
    game: Path, fika_store: Path
) -> None:
    """If the address were accepted at the boundary, this fails: an address no
    server is reached at would be stored and reported as a success instead of
    the error the settings form shows next to the field."""
    exit_code, lines = invoke_rpc(
        "set_fika_settings",
        {"path": str(game), "role": "client", "host_address": "ftp://pc.local", "fika_path": str(fika_store)},
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "INVALID_METADATA"


def test_set_fika_settings_rpc_refuses_a_role_that_does_not_exist(game: Path, fika_store: Path) -> None:
    """If the role were a free-form string, this fails: an unknown role would
    be stored and read back as ``none`` later, silently losing the choice
    instead of being refused as a bad request."""
    exit_code, lines = invoke_rpc(
        "set_fika_settings",
        {"path": str(game), "role": "chief", "host_address": "", "fika_path": str(fika_store)},
    )

    assert exit_code == 2
    assert lines[-1]["data"]["code"] == "VALIDATION_ERROR"


# --- launch_profile accepting fika_path -------------------------------------


def test_launch_profile_rpc_accepts_fika_path_and_plays_on_the_stored_host(
    fake_launchable_dir: LaunchableGameDir,
    fika_store: Path,
    mod_set_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``fika_path`` were not a request field, this fails: the request is
    rejected as invalid and the launch would read the developer's real Fika
    settings instead of the ones the app hands in."""
    monkeypatch.setattr(game_dir, "game_processes", _no_game_processes)
    d = fake_launchable_dir
    invoke_rpc(
        "set_fika_settings",
        {"path": str(d.root), "role": "client", "host_address": "192.0.2.10", "fika_path": str(fika_store)},
    )

    with respx.mock(base_url=HOST, assert_all_called=False) as router:
        _host_answers(router, profiles=[_mini_profile()])

        exit_code, lines = invoke_rpc(
            "launch_profile",
            {
                "path": str(d.root),
                "profile_id": HOST_PROFILE_ID,
                "store_path": str(mod_set_store),
                "fika_path": str(fika_store),
            },
        )

    assert exit_code == 0
    assert lines[-1]["data"]["nickname"] == "HostNick"
    assert lines[-1]["data"]["server_started"] is False
    assert not Path(f"{d.server_binary}.dump.json").exists()

    kill_launchable_processes(d.root)
