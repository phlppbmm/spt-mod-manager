"""Forge client and catalog fixtures for the sptmod test suite.

Registered through ``pytest_plugins`` in :mod:`conftest`.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from respx.models import Call

from fixture_types import ClientFactory
from sptmod.core.forge.catalog import DB_FILE_NAME, Catalog
from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient


class FakeClock:
    """A monotonic-clock stand-in that only advances when told to.

    Matches the ``Callable[[], float]`` signature ``ForgeClient`` expects
    for its ``clock`` constructor argument.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        """Moves the fake clock forward by ``seconds``."""
        self.value += seconds


def recorded_requests(route: respx.Route) -> list[httpx.Request]:
    """Return the requests a respx route recorded, in call order.

    ``respx.CallList`` derives from a bare ``list``, so its entries carry no
    type; copying it into a typed list restores what respx documents.
    """
    calls: list[Call] = list(route.calls)
    return [call.request for call in calls]


class RecordingSleep:
    """A sleep stand-in that records requested durations without blocking.

    Matches the ``Callable[[float], None]`` signature ``ForgeClient``
    expects for its ``sleep`` constructor argument. Never calls the real
    ``time.sleep``.
    """

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def fake_clock() -> FakeClock:
    """Fixture providing a FakeClock instance initialized to 1000.0.

    The clock returns a constant monotonic value (1000.0) until advance(seconds)
    is called; this allows tests to control time precisely for throttle and retry
    logic without blocking.
    """
    return FakeClock()


@pytest.fixture
def recording_sleep() -> RecordingSleep:
    """A mock sleep function that records requested sleep durations without blocking."""
    return RecordingSleep()


@pytest.fixture
def client_factory(fake_clock: FakeClock, recording_sleep: RecordingSleep) -> Iterator[ClientFactory]:
    """Build ``ForgeClient`` instances wired to the fake clock/recording sleep.

    Every client built through the factory is closed on teardown.
    """
    made: list[ForgeClient] = []

    def _make(**kwargs: Any) -> ForgeClient:
        """Build a ForgeClient with injected clock and sleep test doubles.

        The client is wired to use fake_clock (controlled time) and recording_sleep
        (non-blocking duration recording); all instances are automatically closed
        during fixture teardown.
        """
        client = ForgeClient(clock=fake_clock, sleep=recording_sleep, **kwargs)
        made.append(client)
        return client

    yield _make
    for client in made:
        client.close()


@pytest.fixture
def respx_router() -> Iterator[respx.MockRouter]:
    """A respx HTTP mocking router set to the Forge's default base URL."""
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path of the catalog SQLite file for one test, inside ``tmp_path``.

    Never the real platformdirs config dir: catalog tests must not touch the
    developer's actual config directory.
    """
    return tmp_path / "forge-catalog.db"


@pytest.fixture
def catalog(db_path: Path) -> Iterator[Catalog]:
    """A ``Catalog`` opened at the test's isolated ``db_path``."""
    with Catalog(db_path) as cat:
        yield cat


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Empty temporary directory used as the download cache root."""
    directory = tmp_path / "cache"
    directory.mkdir()
    return directory


@pytest.fixture
def isolated_default_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``default_db_path()`` at an isolated location for CLI tests.

    The CLI never passes ``db_path`` explicitly, so it resolves through
    ``forge.catalog.default_db_path()``, which must not touch the developer's
    real config directory.
    """
    config_dir = tmp_path / "config"
    def user_config_path(*_args: object, **_kwargs: object) -> Path:
        return config_dir

    monkeypatch.setattr(
        "sptmod.core.forge.catalog.platformdirs.user_config_path", user_config_path
    )
    return config_dir / DB_FILE_NAME
