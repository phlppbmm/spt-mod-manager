"""Tests for ``sptmod.core.launch``: everything ``launch_profile`` refuses
before the game is started -- a path that is no game directory, a game that is
already running, an unreadable profile, missing programs, unusable launcher
settings, a sign-in the server rejects and a server that never answers -- plus
``tokenize_launch_settings``.

No test starts a real SPT server or game; the stub binaries never bind a
port, and every ping/login answer is simulated through respx.
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
import respx

from fixtures_launch import LaunchableGameDir
from launch_helpers import capture_progress, login_ok, pong_route
from sptmod.core import game_dir
from sptmod.core.errors import (
    GameRunningError,
    LaunchError,
    LauncherSettingsError,
    LoginFailedError,
    NotAGameDirError,
    ServerPortBusyError,
)
from sptmod.core.game_dir import GameProcess, detect_layout
from sptmod.core.launch import (
    LAUNCH_STATE_FILE_NAME,
    launch_profile,
    tokenize_launch_settings,
)
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH

pytestmark = pytest.mark.timeout(5)


def _free_route(router: respx.MockRouter) -> respx.Route:
    """Mock the SPT server port as busy (refusing connections)."""
    return router.get(PING_PATH).mock(side_effect=httpx.ConnectError("refused"))


# --------------------------------------------------------------------------
# tokenize_launch_settings
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", []),
        ("   ", []),
        ("plain", ["plain"]),
        ("A=1 B=2", ["A=1", "B=2"]),
        ("  A=1   B=2  ", ["A=1", "B=2"]),
        ('-Arg="x y"', ["-Arg=x y"]),
        ('X="a b" -flag', ["X=a b", "-flag"]),
        ('A="incomplete', ["A=incomplete"]),
    ],
)
def test_tokenize_launch_settings(text: str, expected: list[str]) -> None:
    """Tokenization of launch settings splits on whitespace and handles quoted values.

    Parameterized cases cover empty input, unquoted tokens, quoted tokens with
    embedded spaces, and unclosed quotes (which end at the string end).
    """
    assert tokenize_launch_settings(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param("A='a b'", ["A=a b"], id="single-quoted-value-with-a-space"),
        pytest.param("'a b' 'c d'", ["a b", "c d"], id="two-single-quoted-tokens"),
        pytest.param("A='incomplete", ["A=incomplete"], id="unterminated-single-quote"),
        pytest.param('A=\\"x', ['A="x'], id="escaped-double-quote-outside-quotes"),
        pytest.param("A=\\'x", ["A='x"], id="escaped-single-quote-outside-quotes"),
        pytest.param('A="a\\"b"', ['A=a"b'], id="escaped-double-quote-inside-a-quoted-run"),
        pytest.param("A='a\\'b'", ["A=a'b"], id="escaped-single-quote-inside-a-quoted-run"),
        pytest.param('A=""', ["A="], id="empty-double-quoted-value"),
        pytest.param("A=''", ["A="], id="empty-single-quoted-value"),
        pytest.param('""', [""], id="an-empty-quoted-token-on-its-own"),
        pytest.param('A="x"B', ["A=xB"], id="text-directly-behind-a-quoted-run"),
        pytest.param("A=C:\\path", ["A=C:\\path"], id="backslash-that-is-not-before-a-quote"),
        pytest.param('A=\'a "b" c\'', ['A=a "b" c'], id="double-quotes-inside-a-single-quoted-run"),
    ],
)
def test_tokenize_launch_settings_quoting(text: str, expected: list[str]) -> None:
    """Tokenization matches the quoting rules of the official launcher.

    Both quote characters open a run that ends at the same character, a
    backslash keeps the following quote as text, an unterminated run reaches
    the end of the settings and an empty run still yields a token. A backslash
    that stands before anything else -- a Windows path, which these settings
    are full of -- stays as it is.
    """
    assert tokenize_launch_settings(text) == expected


# --------------------------------------------------------------------------
# launch_profile: refusal before any HTTP call
# --------------------------------------------------------------------------


def test_launch_profile_raises_not_a_game_dir_for_a_bad_path(tmp_path: Path) -> None:
    """launch_profile raises NotAGameDirError when the game directory does not exist.

    Calling launch_profile with a path that has no valid SPT/SPT_Runtime layout
    raises NotAGameDirError before any other checks.
    """
    with pytest.raises(NotAGameDirError):
        launch_profile(tmp_path / "does-not-exist", "any-profile")


def test_launch_profile_refuses_a_running_client(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_profile raises GameRunningError before proceeding when a client is running.

    Process scan returns a running EscapeFromTarkov.exe; launch_profile exits with
    GameRunningError naming the process, refusing to start server or perform login.
    """
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=1, name="EscapeFromTarkov.exe", kind="client", started_at=1.0)],
    )

    with pytest.raises(GameRunningError, match="EscapeFromTarkov.exe"):
        launch_profile(fake_launchable_dir.root, fake_launchable_dir.profile_id)


def test_launch_profile_reuses_an_already_running_server(
    fake_launchable_dir: LaunchableGameDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launch_profile reuses an existing server process without spawning a new one.

    Process scan finds an already-running SPT.Server.Linux (pid 555, started at 42.0);
    launch_profile records server_started=False with that pid in the result, and
    server_binary.dump.json is never created (proof the binary was not spawned).
    """
    # A server started by someone else (or a previous launch) is running, but
    # sptmod has no process record of it -- the scan is the only signal.
    monkeypatch.setattr(
        game_dir,
        "game_processes",
        lambda: [GameProcess(pid=555, name="SPT.Server.Linux", kind="server", started_at=42.0)],
    )
    d = fake_launchable_dir

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        result = launch_profile(d.root, d.profile_id)

    assert result.server_started is False
    assert result.server_pid == 555
    # The stub server binary must never have been spawned.
    assert not Path(f"{d.server_binary}.dump.json").exists()


def test_launch_profile_raises_server_port_busy_for_a_foreign_listener(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises ServerPortBusyError when a foreign process holds the server port.

    Port 6969 answers but responds with an unexpected message, indicating
    another program owns the address.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).respond(200, json={"Response": "not-pong"})

        with pytest.raises(ServerPortBusyError, match="6969"):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_profile: profile file handling
# --------------------------------------------------------------------------


def test_launch_profile_raises_launch_error_for_a_missing_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when the profile JSON file does not exist.

    Calling launch_profile(root, "no-such-profile") when the file is missing raises
    LaunchError with the profile name in the message, preventing any server startup.
    """
    with pytest.raises(LaunchError, match="no-such-profile"):
        launch_profile(fake_launchable_dir.root, "no-such-profile")


@pytest.mark.parametrize(
    "profile_id",
    ["", ".", "..", "../escape", "a/b", "a\\b", "/etc/passwd"],
)
def test_launch_profile_rejects_a_profile_id_that_is_not_a_bare_file_stem(
    fake_launchable_dir: LaunchableGameDir, profile_id: str
) -> None:
    """launch_profile rejects profile IDs containing path separators or dots.

    Parameterized cases include empty string, dot, parent-dir reference, slash
    paths, backslash paths, and absolute paths.
    """
    with pytest.raises(LaunchError):
        launch_profile(fake_launchable_dir.root, profile_id)


def test_launch_profile_raises_launch_error_for_an_unreadable_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when profile JSON is malformed."""
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text("not json", encoding="utf-8")

    with pytest.raises(LaunchError):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_when_username_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when profile.info lacks a username key.

    Profile JSON contains info.id but no info.username field; launch_profile raises
    LaunchError naming the profile (via match), stopping before server startup.
    """
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text(json.dumps({"info": {"id": d.profile_id}}), encoding="utf-8")

    with pytest.raises(LaunchError, match=d.profile_id):
        launch_profile(d.root, d.profile_id)


def test_launch_profile_falls_back_to_the_account_name_when_nickname_is_absent(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile uses username as nickname when profile.info has no nickname.

    Profile JSON has info.username ("test_user") but no info.nickname; launch_profile
    completes successfully and LaunchResult.nickname equals the username value.
    """
    d = fake_launchable_dir
    layout = detect_layout(d.root)
    profile_file = d.root / layout.profiles_dir / f"{d.profile_id}.json"
    profile_file.write_text(
        json.dumps({"info": {"id": d.profile_id, "username": d.username}}),
        encoding="utf-8",
    )

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)
        result = launch_profile(d.root, d.profile_id)

    assert result.nickname == d.username


# --------------------------------------------------------------------------
# launch_profile: missing binaries
# --------------------------------------------------------------------------


def test_launch_profile_raises_launch_error_for_a_missing_server_binary(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when the server executable is missing.

    After profile validation, before spawning the server, launch_profile checks
    that SPT.Server.Linux exists; missing binary raises LaunchError naming it.
    """
    d = fake_launchable_dir
    d.server_binary.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_route(router)

        with pytest.raises(LaunchError, match="SPT.Server.Linux"):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launch_error_for_a_missing_client_executable(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when game executable is missing, no state file written.

    The server and client processes may still start; before recording them,
    launch_profile checks that the game binary exists and fails if not.
    """
    d = fake_launchable_dir
    d.client_exe.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LaunchError, match="EscapeFromTarkov.exe"):
            launch_profile(d.root, d.profile_id)

    assert not (d.root / LAUNCH_STATE_FILE_NAME).exists()


# --------------------------------------------------------------------------
# launch_profile: LauncherSettings.json handling
# --------------------------------------------------------------------------


def test_launch_profile_raises_launcher_settings_error_when_the_file_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LauncherSettingsError when LauncherSettings.json is missing.

    After server login succeeds, launch_profile reads LauncherSettings.json to get
    client wrapper settings; missing file raises LauncherSettingsError.
    """
    d = fake_launchable_dir
    d.launcher_settings_file.unlink()

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launcher_settings_error_for_invalid_json(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LauncherSettingsError when LauncherSettings.json is malformed.

    Settings file exists but contains "not json"; json.loads() fails and
    launch_profile raises LauncherSettingsError instead of letting the parse
    exception escape.
    """
    d = fake_launchable_dir
    d.launcher_settings_file.write_text("not json", encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_raises_launcher_settings_error_when_linux_settings_is_missing(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LauncherSettingsError when LinuxSettings is absent.

    Settings JSON parses but lacks the LinuxSettings section; launch_profile
    requires it and raises LauncherSettingsError instead of continuing.
    """
    d = fake_launchable_dir
    d.launcher_settings_file.write_text(json.dumps({"Servers": []}), encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


@pytest.mark.parametrize("umu_path", [None, ""])
def test_launch_profile_raises_launcher_settings_error_when_umu_path_is_unset(
    fake_launchable_dir: LaunchableGameDir, umu_path: str | None
) -> None:
    """launch_profile raises LauncherSettingsError when UmuPath is null or empty string.

    Parameterized cases test both None and empty string.
    """
    d = fake_launchable_dir
    settings = json.loads(d.launcher_settings_file.read_text(encoding="utf-8"))
    settings["LinuxSettings"]["UmuPath"] = umu_path
    d.launcher_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        login_ok(router)

        with pytest.raises(LauncherSettingsError):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_profile: login failure
# --------------------------------------------------------------------------


def test_launch_profile_raises_login_failed_when_the_server_rejects_the_profile(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LoginFailedError when server responds false to login.

    Server ping succeeds, but PUT LOGIN_PATH returns {"Response": false}; launch_profile
    raises LoginFailedError naming the profile's nickname in the message.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        router.put(LOGIN_PATH).respond(200, json={"Response": False})

        with pytest.raises(LoginFailedError, match=d.nickname):
            launch_profile(d.root, d.profile_id)


def test_launch_profile_propagates_launch_error_on_a_login_transport_failure(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when login HTTP request times out.

    Server responds to ping successfully; login PUT request raises httpx.ReadTimeout.
    launch_profile catches the transport error and raises LaunchError, not the raw
    httpx exception.
    """
    d = fake_launchable_dir
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        pong_route(router)
        router.put(LOGIN_PATH).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(LaunchError):
            launch_profile(d.root, d.profile_id)


# --------------------------------------------------------------------------
# launch_profile: readiness poll
# --------------------------------------------------------------------------


def test_launch_profile_polls_until_ready_with_two_free_answers_first(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile polls server until pong appears, emitting monotonic progress.

    First 3 pings (1 initial + 2 polls) fail with ConnectError ("refused"); 4th
    returns pong. Progress callback receives "waiting" step events with percent
    values that form a strictly increasing sequence. Server is spawned and
    result.server_started is True.
    """
    d = fake_launchable_dir
    calls = {"n": 0}

    def _ping(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:  # 1 initial check + 2 poll iterations, all "free"
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "Pong!"})

    events, progress = capture_progress()
    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)
        login_ok(router)

        result = launch_profile(d.root, d.profile_id, progress=progress, poll_seconds=0.0, clock=lambda: 0.0)

    assert result.server_started is True
    waiting_percents = [percent for step, percent, _ in events if step == "waiting"]
    assert waiting_percents == sorted(waiting_percents)


def test_launch_profile_keeps_polling_through_a_mid_poll_timeout_then_succeeds(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile tolerates timeout errors mid-poll and continues polling.

    A timeout (something answered but did not finish in time) is retried unlike
    a foreign response, which stops immediately. First request: refused. Second:
    timeout. Third: pong succeeds.
    """
    # A "timeout" answer (something took the connection but did not answer in
    # time) must not raise ServerPortBusyError like "foreign" does; the poll
    # loop keeps going and still reaches readiness.
    d = fake_launchable_dir
    calls = {"n": 0}

    def _ping(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")  # initial check: "free"
        if calls["n"] == 2:
            raise httpx.ReadTimeout("slow")  # mid-poll: "timeout"
        return httpx.Response(200, json={"Response": "Pong!"})

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)
        login_ok(router)

        result = launch_profile(d.root, d.profile_id, poll_seconds=0.0, clock=lambda: 0.0)

    assert result.server_started is True


def test_launch_profile_raises_launch_error_when_the_server_never_answers(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when polling exceeds the ready_timeout.

    Every ping raises ConnectError; injected clock jumps from 0.0 to 1000.0 on
    the first poll, exceeding ready_timeout=5.0. launch_profile raises LaunchError
    with "5 seconds" in the message.
    """
    d = fake_launchable_dir
    clock_values = iter([0.0, 1000.0])

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        _free_route(router)

        with pytest.raises(LaunchError, match="5 seconds"):
            launch_profile(
                d.root,
                d.profile_id,
                ready_timeout=5.0,
                poll_seconds=0.0,
                clock=lambda: next(clock_values, 1000.0),
            )


def test_launch_profile_raises_server_port_busy_when_a_foreign_answer_appears_mid_poll(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises ServerPortBusyError when port answers non-SPT mid-poll.

    Initial ping fails with ConnectError. On next poll attempt, port answers with
    HTTP 200 but {"Response": "someone-else"}, indicating a foreign service owns
    the port. launch_profile raises ServerPortBusyError immediately.
    """
    d = fake_launchable_dir
    calls = {"n": 0}

    def _ping(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"Response": "someone-else"})

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)

        with pytest.raises(ServerPortBusyError):
            launch_profile(d.root, d.profile_id, poll_seconds=0.0)


def test_launch_profile_raises_launch_error_when_the_spawned_server_dies(
    fake_launchable_dir: LaunchableGameDir,
) -> None:
    """launch_profile raises LaunchError when spawned server process exits early.

    Server binary is replaced with a script that exits with code 1 immediately.
    Ping mock sleeps 0.1s before responding, guaranteeing server has exited before
    first poll checks process_alive. launch_profile raises LaunchError with
    "stopped" in message.
    """
    d = fake_launchable_dir
    # Replace the stub with one that exits immediately instead of sleeping.
    d.server_binary.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n", encoding="utf-8")
    os.chmod(d.server_binary, 0o755)

    def _ping(_request: httpx.Request) -> httpx.Response:
        # A tiny real delay guarantees the trivial script above has already
        # exited by the time the first poll iteration checks process_alive.
        time.sleep(0.1)
        raise httpx.ConnectError("refused")

    with respx.mock(base_url=d.base_url, assert_all_called=False) as router:
        router.get(PING_PATH).mock(side_effect=_ping)

        with pytest.raises(LaunchError, match="stopped"):
            launch_profile(d.root, d.profile_id, poll_seconds=0.0)
