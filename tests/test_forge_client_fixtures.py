"""Shared fixtures and JSON builders for the Forge HTTP client test suite.

Not a standalone test module (no ``test_*`` functions): it is imported by the
other ``test_forge_client_*.py`` files so fixtures/builders live in one place
without touching ``tests/conftest.py``.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import respx

from sptmod.core.forge.client import DEFAULT_BASE_URL, ForgeClient

# The factory forwards arbitrary keyword arguments to ``ForgeClient``, so the
# parameter list stays open; tests annotate their fixture argument with this.
type ClientFactory = Callable[..., ForgeClient]


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
        self.value += seconds


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
    return FakeClock()


@pytest.fixture
def recording_sleep() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture
def client_factory(
    fake_clock: FakeClock, recording_sleep: RecordingSleep
) -> Iterator[ClientFactory]:
    """Build ``ForgeClient`` instances wired to the fake clock/recording sleep.

    Every client built through the factory is closed on teardown.
    """
    made: list[ForgeClient] = []

    def _make(**kwargs: Any) -> ForgeClient:
        client = ForgeClient(clock=fake_clock, sleep=recording_sleep, **kwargs)
        made.append(client)
        return client

    yield _make
    for client in made:
        client.close()


@pytest.fixture
def respx_router() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as router:
        yield router


# --- JSON builders -----------------------------------------------------


def success_envelope(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def error_envelope(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "code": code, "message": message}


def page_envelope(
    items: list[Any],
    *,
    current_page: int = 1,
    last_page: int = 1,
    per_page: int = 50,
    total: int | None = None,
    next_url: str | None = None,
) -> dict[str, Any]:
    resolved_total = len(items) if total is None else total
    return {
        "success": True,
        "data": items,
        "links": {
            "first": f"{DEFAULT_BASE_URL}/mods?page=1",
            "last": f"{DEFAULT_BASE_URL}/mods?page={last_page}",
            "prev": None,
            "next": next_url,
        },
        "meta": {
            "current_page": current_page,
            "from": 1 if items else None,
            "last_page": last_page,
            "path": f"{DEFAULT_BASE_URL}/mods",
            "per_page": per_page,
            "to": len(items),
            "total": resolved_total,
        },
    }


def owner_json(**overrides: Any) -> dict[str, Any]:
    data = {
        "id": 10,
        "name": "Alice",
        "profile_photo_url": "https://cdn.example/alice.png",
        "cover_photo_url": "https://cdn.example/alice-cover.png",
    }
    data.update(overrides)
    return data


def mod_json_minimal(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 1,
        "hub_id": 100,
        "guid": "com.example.mod",
        "name": "Example Mod",
        "slug": "example-mod",
        "teaser": "A short teaser.",
        "thumbnail": "https://cdn.example/thumb.png",
        "downloads": 1234,
        "favourites_count": 56,
        "owner": owner_json(),
        "additional_authors": [],
        "source_code_links": [{"url": "https://github.com/ex/mod", "label": "GitHub"}],
        "detail_url": "https://forge.sp-tarkov.com/mods/example-mod",
        "fika_compatibility": True,
        "featured": False,
        "contains_ads": False,
        "contains_ai_content": False,
        "shows_profile_binding_notice": False,
        "published_at": "2025-01-01T00:00:00Z",
        "created_at": "2024-12-01T00:00:00Z",
        "updated_at": "2025-06-01T00:00:00Z",
    }
    data.update(overrides)
    return data


def mod_json_full(**overrides: Any) -> dict[str, Any]:
    data = mod_json_minimal(
        description="Full description.",
        custom_ai_disclosure="Uses AI for translation.",
        license={"id": 1, "name": "MIT License", "link": "https://mit.example"},
        category={"id": 5, "title": "Weapons", "slug": "weapons"},
        versions=[
            {
                "id": 900,
                "version": "1.0.0",
                "spt_version_constraint": "^3.8.0",
                "downloads": 10,
                "published_at": "2025-01-02T00:00:00Z",
            }
        ],
    )
    data.update(overrides)
    return data


def version_json(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 900,
        "hub_id": 800,
        "version": "1.0.0",
        "description": "Initial release.",
        "link": "https://github.com/ex/mod/releases/download/v1.0.0/mod.zip",
        "content_length": None,
        "spt_version_constraint": "^3.8.0",
        "downloads": 10,
        "fika_compatibility": "compatible",
        "published_at": "2025-01-02T00:00:00Z",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-02T00:00:00Z",
        "dependencies": [
            {
                "id": 1,
                "mod_id": 2,
                "mod_guid": "com.example.dep",
                "mod_name": "Dep Mod",
                "version_constraint": "^2.0.0",
                "is_optional": False,
            }
        ],
        "virus_total_links": ["https://virustotal.com/gui/file/abc123"],
    }
    data.update(overrides)
    return data
