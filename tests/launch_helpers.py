"""Helpers shared by the launch test modules.

Holds the two respx routes every launch test sets up, the progress capture and
the poll-until helper, so ``test_launch.py`` and its siblings each import them
instead of carrying their own copy.
"""

import time
from collections.abc import Callable

import respx

from sptmod.core.models import Progress
from sptmod.core.sptserver import LOGIN_PATH, PING_PATH


def capture_progress() -> tuple[list[tuple[str, int, str]], Progress]:
    """Capture all progress callback invocations into a list for inspection.

    Returns a tuple of (events, callback). The callback appends (step, percent,
    message) tuples; events is readable to verify the order and values of each
    reported step.
    """
    events: list[tuple[str, int, str]] = []

    def _capture(step: str, percent: int, message: str) -> None:
        events.append((step, percent, message))

    return events, _capture


def pong_route(router: respx.MockRouter) -> respx.Route:
    """Mock the SPT server to respond successfully to ping requests."""
    return router.get(PING_PATH).respond(200, json={"Response": "Pong!"})


def login_ok(router: respx.MockRouter) -> respx.Route:
    """Set up respx to return a successful login response.

    Routes PUT requests to LOGIN_PATH to reply with HTTP 200 and
    {"Response": true}, simulating a profile that the server accepts.
    """
    return router.put(LOGIN_PATH).respond(200, json={"Response": True})


def wait_until(predicate: Callable[[], bool], timeout: float = 4.0) -> None:
    """Repeatedly call predicate until it returns true or timeout expires.

    Sleeps 20ms between polls; raises AssertionError if the deadline passes
    before predicate becomes true. Timeout defaults to 4 seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was never met")
