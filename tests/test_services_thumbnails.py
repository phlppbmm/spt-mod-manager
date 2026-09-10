"""Tests for ``sptmod.core.services.get_thumbnails`` and the image cache it fills.

Setup: a catalog cache below ``tmp_path`` is filled from a respx-mocked Forge
page, and the pictures those catalog rows point at are served by a mocked
image host that answers only a request carrying the Forge origin as
``Referer``.

Assertion: the service answers every requested Forge id with the picture of
its catalog row as a data URL, with None where the catalog knows no picture,
keeps the pictures across calls and loses them again when the catalog cache is
cleared.

Reason: the image URL must come from the catalog and never from the caller,
and clearing the catalog has to take the images with it -- neither is visible
anywhere else.
"""

import base64
from pathlib import Path

import pytest
import respx

from fixture_types import JsonDict
from forge_json import mod_json_minimal, page_envelope
from sptmod.core import services
from sptmod.core.forge.client import DEFAULT_BASE_URL

IMAGE_URL = "https://files.example.test/mods/47/thumb.png"
IMAGE_HOST = "files.example.test"
REFERER = "https://sp-mod.com/"
PNG_BYTES = b"\x89PNG\r\n\x1a\nthe pixels of mod 47"
UNUSED_GAME_DIR = Path("/unused")


def serve_catalog(router: respx.MockRouter, catalog_db: Path, mods: list[JsonDict]) -> None:
    """Fill the catalog cache at ``catalog_db`` with ``mods`` from the mocked Forge."""
    router.get(f"{DEFAULT_BASE_URL}/mods").respond(200, json=page_envelope(mods))
    services.sync_catalog(UNUSED_GAME_DIR, db_path=catalog_db)


def serve_image(router: respx.MockRouter) -> respx.Route:
    """Serve the picture at ``IMAGE_URL`` to a request naming the Forge origin.

    A request without that header is refused exactly as the real image host
    refuses it, so a picture that arrives proves the header was sent. Returns
    the route that serves it.
    """
    route = router.get(IMAGE_URL, headers={"Referer": REFERER})
    route.respond(200, content=PNG_BYTES, headers={"Content-Type": "image/png"})
    router.get(IMAGE_URL).respond(403)
    return route


def decoded(data_url: str | None) -> tuple[str, bytes]:
    """Split a ``data:<media type>;base64,<payload>`` URL into its two parts."""
    assert data_url is not None
    head, _, encoded = data_url.partition(",")
    assert head.startswith("data:")
    assert head.endswith(";base64")
    return head.removeprefix("data:").removesuffix(";base64"), base64.b64decode(encoded)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Path of the catalog cache one test fills, inside ``tmp_path``."""
    return tmp_path / "forge-catalog.db"


@pytest.fixture
def image_cache(tmp_path: Path) -> Path:
    """An empty directory the fetched pictures of one test are kept in."""
    directory = tmp_path / "thumbnails"
    directory.mkdir()
    return directory


# --- the image the catalog row points at ----------------------------------


def test_get_thumbnails_returns_the_image_of_the_catalog_row_as_a_data_url(
    db: Path, image_cache: Path
) -> None:
    """The picture a mod's catalog row names is fetched with the header its
    host insists on and handed back as a data URL -- a view can render that
    directly, which is the whole reason the bytes travel through here.
    """
    with respx.mock(assert_all_called=False) as router:
        serve_catalog(router, db, [mod_json_minimal(id=47, thumbnail=IMAGE_URL)])
        serve_image(router)

        loaded = services.get_thumbnails([47], db_path=db, cache_dir=image_cache)

    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)


@pytest.mark.parametrize(
    "thumbnail",
    [
        pytest.param(None, id="row_without_an_image"),
        pytest.param("", id="row_with_an_empty_image_url"),
    ],
)
def test_get_thumbnails_answers_none_for_a_mod_the_catalog_holds_no_image_for(
    thumbnail: str | None, db: Path, image_cache: Path
) -> None:
    """A row without a usable image URL answers None and costs no request at
    all: the caller shows its placeholder, and an empty URL must not turn into
    a fetch of the image host's root.
    """
    with respx.mock(assert_all_called=False) as router:
        serve_catalog(router, db, [mod_json_minimal(id=47, thumbnail=thumbnail)])
        image_host = router.route(host=IMAGE_HOST).respond(500)

        loaded = services.get_thumbnails([47], db_path=db, cache_dir=image_cache)

        assert image_host.call_count == 0
    assert loaded == {47: None}


def test_get_thumbnails_answers_none_for_an_id_the_catalog_does_not_know(
    db: Path, image_cache: Path
) -> None:
    """An id no synced row belongs to answers None instead of raising: the
    caller asks for whatever its list holds, which may be ahead of the cache.
    """
    with respx.mock(assert_all_called=False) as router:
        serve_catalog(router, db, [mod_json_minimal(id=47, thumbnail=IMAGE_URL)])
        image_host = router.route(host=IMAGE_HOST).respond(500)

        loaded = services.get_thumbnails([9001], db_path=db, cache_dir=image_cache)

        assert image_host.call_count == 0
    assert loaded == {9001: None}


def test_get_thumbnails_answers_every_requested_id_in_one_call(
    db: Path, image_cache: Path
) -> None:
    """Mods with and without a picture are asked for together, and every id
    comes back keyed by itself -- a caller renders a whole list from this one
    answer, so a dropped key would leave a row unanswered.
    """
    mods = [
        mod_json_minimal(id=47, thumbnail=IMAGE_URL),
        mod_json_minimal(id=48, name="Second Mod", slug="second-mod", thumbnail=None),
    ]
    with respx.mock(assert_all_called=False) as router:
        serve_catalog(router, db, mods)
        serve_image(router)

        loaded = services.get_thumbnails([47, 48, 9001], db_path=db, cache_dir=image_cache)

    assert set(loaded) == {47, 48, 9001}
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert loaded[48] is None
    assert loaded[9001] is None


# --- the cache the pictures live in ---------------------------------------


def test_a_second_call_without_a_cache_directory_reuses_the_kept_image(
    db: Path,
) -> None:
    """Without an explicit directory the pictures land in the workspace's own
    image cache and are read back from there, so a caller that never passes a
    directory still fetches each picture only once.
    """
    with respx.mock(assert_all_called=False) as router:
        serve_catalog(router, db, [mod_json_minimal(id=47, thumbnail=IMAGE_URL)])
        route = serve_image(router)

        first = services.get_thumbnails([47], db_path=db)
        second = services.get_thumbnails([47], db_path=db)

        assert route.call_count == 1
    assert decoded(second[47]) == ("image/png", PNG_BYTES)
    assert second == first


def test_clear_catalog_cache_takes_the_kept_images_with_it(db: Path) -> None:
    """After clearing the catalog, the picture of a freshly synced row is
    fetched again: the images belong to the cache that was thrown away, so
    leaving them behind would keep stale pictures alive forever.
    """
    with respx.mock(assert_all_called=False) as router:
        serve_catalog(router, db, [mod_json_minimal(id=47, thumbnail=IMAGE_URL)])
        route = serve_image(router)
        services.get_thumbnails([47], db_path=db)

        services.clear_catalog_cache(db_path=db)

        serve_catalog(router, db, [mod_json_minimal(id=47, thumbnail=IMAGE_URL)])
        loaded = services.get_thumbnails([47], db_path=db)

        assert route.call_count == 2
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
