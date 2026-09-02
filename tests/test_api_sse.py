"""SSE progress-stream test of the FastAPI adapter.

``TestClient`` cannot consume ``GET /api/events``: the endpoint's stream
never ends, so the ASGI transport TestClient uses to run the app in-process
blocks forever on it. This test instead runs the real application under a
live ``uvicorn.Server`` in a background thread and drives it over real HTTP
with ``httpx``, which is the only way to observe the SSE channel end to end.
"""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from sptmod.core import game_dir, services
from sptmod.server.app import app

_STARTUP_TIMEOUT = 5.0
_SUBSCRIBE_SETTLE = 0.3


@pytest.fixture
def live_server() -> Iterator[str]:
    """Run the real app under a live uvicorn server; yields its base URL."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn server did not start in time"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.timeout(20)
def test_sse_stream_delivers_ordered_progress_events_for_the_operation_id(
    live_server: str,
    fake_game_dir: Path,
    make_archive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_dir, "running_game_processes", lambda: [])
    # Set up the game directory directly through the core: this test's
    # subject is the SSE channel, not the init lifecycle (covered elsewhere).
    services.init_game_dir(fake_game_dir)
    archive = make_archive(
        {
            "SPT/user/mods/CoolMod/package.json": json.dumps(
                {"name": "CoolMod", "version": "1.2.3", "author": "Acme"}
            )
        },
        "zip",
    )

    operation_id = "sse-test-operation"
    events: list[dict] = []
    ready = threading.Event()

    def _collect() -> None:
        with httpx.stream(
            "GET", f"{live_server}/api/events", timeout=15
        ) as response:
            ready.set()
            for raw_line in response.iter_lines():
                line = raw_line.strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[len("data:") :].strip()))
                if len(events) >= 8:
                    return

    collector = threading.Thread(target=_collect, daemon=True)
    collector.start()
    assert ready.wait(timeout=5), "SSE stream never connected"
    # Give the server-side generator time to reach broker.subscribe() after
    # the client already observed the response headers.
    time.sleep(_SUBSCRIBE_SETTLE)

    with httpx.Client() as trigger:
        response = trigger.post(
            f"{live_server}/api/install_from_archive",
            json={
                "path": str(fake_game_dir),
                "archive": str(archive),
                "operation_id": operation_id,
            },
        )
    assert response.status_code == 200
    assert response.json()["operation_id"] == operation_id

    collector.join(timeout=15)

    matching = [event for event in events if event["operation_id"] == operation_id]
    assert [event["step"] for event in matching] == [
        "check",
        "extract",
        "anchor",
        "metadata",
        "conflicts",
        "copy",
        "commit",
        "commit",
    ]
    assert [event["percent"] for event in matching] == [
        0,
        10,
        30,
        45,
        60,
        75,
        90,
        100,
    ]
