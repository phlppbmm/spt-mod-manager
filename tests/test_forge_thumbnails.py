"""Tests for the thumbnail cache in ``sptmod.core.forge.thumbnails``.

Setup: the pictures are served by a respx-mocked host that behaves like the
Forge's image host -- it hands a picture out only to a request carrying the
Forge origin as ``Referer`` and refuses every other one -- and each cache
lives in its own directory below ``tmp_path``.

Assertion: ``load`` returns a picture as a data URL, keeps it for the next
call, fetches again what moved or became unusable, refuses what is too large,
drops the least recently used images once the cache outgrew its limit and
answers None instead of raising whenever an image cannot be had; ``clear``
empties the cache.

Reason: the view showing these pictures cannot fetch them itself, so this is
the only place where the header, the cache on disk and its two size limits
are pinned.
"""

import base64
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx

from fixture_types import ClientFactory
from fixtures_forge import recorded_requests
from sptmod.core.forge import thumbnails
from sptmod.core.forge.thumbnails import MAX_CACHE_BYTES, MAX_IMAGE_BYTES

IMAGE_URL = "https://files.example.test/mods/47/thumb.png"
MOVED_URL = "https://files.example.test/mods/47/thumb-v2.png"
SECOND_URL = "https://files.example.test/mods/48/thumb.png"
DEFAULT_REFERER = "https://sp-mod.com/"
PNG_BYTES = b"\x89PNG\r\n\x1a\nthe pixels of mod 47"
SECOND_BYTES = b"\x89PNG\r\n\x1a\nthe pixels of mod 48"


def hotlink_protected(
    router: respx.MockRouter,
    url: str,
    payload: bytes,
    *,
    referer: str = DEFAULT_REFERER,
    content_type: str | None = "image/png",
) -> respx.Route:
    """Serve ``payload`` at ``url``, but only to a request naming ``referer``.

    Every request without that header is answered with the 403 the real image
    host sends, so a test that gets the picture proves the header was on the
    wire. Returns the route that serves the picture.
    """
    headers = {} if content_type is None else {"Content-Type": content_type}
    route = router.get(url, headers={"Referer": referer})
    route.respond(200, content=payload, headers=headers)
    router.get(url).respond(403)
    return route


def decoded(data_url: str | None) -> tuple[str, bytes]:
    """Split a ``data:<media type>;base64,<payload>`` URL into its two parts."""
    assert data_url is not None
    head, _, encoded = data_url.partition(",")
    assert head.startswith("data:")
    assert head.endswith(";base64")
    return head.removeprefix("data:").removesuffix(";base64"), base64.b64decode(encoded)


def images(directory: Path) -> list[Path]:
    """Return the cached pictures of ``directory``, without their descriptions."""
    return sorted(
        entry
        for entry in directory.iterdir()
        if not entry.name.endswith((".json", ".part"))
    )


def sidecar(image: Path) -> Path:
    """Return the description file stored next to the cached picture ``image``."""
    return image.with_name(f"{image.name}.json")


def seed_image(directory: Path, name: str, size: int, *, age: float) -> Path:
    """Put a picture of ``size`` bytes, last used ``age`` seconds ago, into the cache.

    The file is sparse, so a cache of a hundred megabytes costs a test no disk
    space; only the size the sweep reads matters. A negative ``age`` dates the
    picture into the future, as a restored backup or a clock jump does.
    """
    image = directory / name
    image.touch()
    os.truncate(image, size)
    description = {"url": f"https://files.example.test/{name}", "media_type": "image/png"}
    sidecar(image).write_text(json.dumps(description), encoding="utf-8")
    used = time.time() - age
    os.utime(image, (used, used))
    return image


@pytest.fixture
def image_cache(tmp_path: Path) -> Path:
    """An empty directory the thumbnails of one test are cached in."""
    directory = tmp_path / "thumbnails"
    directory.mkdir()
    return directory


# --- the header the image host insists on ---------------------------------


@pytest.mark.parametrize(
    ("forge_url", "expected_referer"),
    [
        pytest.param(None, "https://sp-mod.com/", id="default_forge"),
        pytest.param(
            "https://mirror.example.test/api/v0",
            "https://mirror.example.test/",
            id="forge_url_override",
        ),
    ],
)
def test_load_sends_the_configured_forge_origin_as_referer(
    forge_url: str | None,
    expected_referer: str,
    client_factory: ClientFactory,
    image_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The host only serves a request whose ``Referer`` is the Forge's origin,
    and that origin follows the configured Forge URL -- so a picture arriving
    here proves both the header and its derivation from ``SPTMOD_FORGE_URL``.
    """
    if forge_url is None:
        monkeypatch.delenv("SPTMOD_FORGE_URL", raising=False)
    else:
        monkeypatch.setenv("SPTMOD_FORGE_URL", forge_url)

    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES, referer=expected_referer)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

    sent = [request.headers["Referer"] for request in recorded_requests(route)]
    assert sent == [expected_referer]
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)


# --- what comes back, and what does not -----------------------------------


@pytest.mark.parametrize(
    ("content_type", "expected_media_type"),
    [
        pytest.param("image/png", "image/png", id="png"),
        pytest.param("image/jpeg", "image/jpeg", id="jpeg"),
        pytest.param("image/gif", "image/gif", id="gif"),
        pytest.param("image/webp", "image/webp", id="webp"),
        pytest.param("image/avif", "image/avif", id="avif"),
        pytest.param("IMAGE/PNG; charset=binary", "image/png", id="shouted_with_parameters"),
        pytest.param("image/svg+xml", None, id="a_drawing_the_view_would_run_as_markup"),
        pytest.param("image/x-icon", None, id="an_image_type_no_view_needs"),
        pytest.param("text/html; charset=utf-8", None, id="an_error_page_instead_of_an_image"),
        pytest.param(None, None, id="no_content_type_at_all"),
    ],
)
def test_load_takes_the_media_type_from_the_answer_and_refuses_a_non_image(
    content_type: str | None,
    expected_media_type: str | None,
    client_factory: ClientFactory,
    image_cache: Path,
) -> None:
    """The media type of the data URL is the one the host declared, reduced to
    its bare form, and only the drawn formats are let through: an error page
    would otherwise be rendered as a picture, and an ``image/svg+xml`` the
    host chose would reach the view as markup it runs.
    """
    with respx.mock(assert_all_called=False) as router:
        hotlink_protected(router, IMAGE_URL, PNG_BYTES, content_type=content_type)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

    fetched = None if loaded[47] is None else decoded(loaded[47])
    expected = None if expected_media_type is None else (expected_media_type, PNG_BYTES)
    assert fetched == expected


@pytest.mark.parametrize(
    ("size", "kept"),
    [
        pytest.param(MAX_IMAGE_BYTES, True, id="exactly_at_the_limit"),
        pytest.param(MAX_IMAGE_BYTES + 1, False, id="one_byte_over_the_limit"),
    ],
)
def test_load_reads_an_image_only_up_to_the_per_image_limit(
    size: int, kept: bool, client_factory: ClientFactory, image_cache: Path
) -> None:
    """A picture at the limit is shown and kept, one byte more is dropped and
    nothing of it is left on disk -- the limit is what stops a single huge
    file from being read into memory and pushed into the view.
    """
    payload = b"p" * size
    with respx.mock(assert_all_called=False) as router:
        hotlink_protected(router, IMAGE_URL, payload)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

    fetched = None if loaded[47] is None else decoded(loaded[47])
    assert fetched == (("image/png", payload) if kept else None)
    assert [image.read_bytes() for image in images(image_cache)] == ([payload] if kept else [])


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(403), id="hotlink_refused"),
        pytest.param(httpx.Response(404), id="image_gone"),
        pytest.param(httpx.Response(500), id="host_broken"),
        pytest.param(httpx.ConnectError("no route to host"), id="network_error"),
    ],
)
def test_load_degrades_to_no_thumbnail_when_the_image_cannot_be_fetched(
    answer: httpx.Response | Exception,
    client_factory: ClientFactory,
    image_cache: Path,
) -> None:
    """Every kind of refusal answers None and leaves the cache untouched, so a
    catalog view keeps working without pictures instead of failing whole.
    """
    with respx.mock(assert_all_called=False) as router:
        router.get(IMAGE_URL).mock(side_effect=[answer])

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

    assert loaded == {47: None}
    assert not list(image_cache.iterdir())


def test_load_answers_every_requested_key_even_when_one_image_is_refused(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """One refused picture must not cost the others their entry: the caller
    keys its rows by the mapping it passed in, so a missing key would leave a
    row without an answer at all.
    """
    with respx.mock(assert_all_called=False) as router:
        hotlink_protected(router, IMAGE_URL, PNG_BYTES)
        router.get(SECOND_URL).respond(403)

        loaded = thumbnails.load(
            {47: IMAGE_URL, 48: SECOND_URL}, client=client_factory(), cache_dir=image_cache
        )

    assert set(loaded) == {47, 48}
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert loaded[48] is None


# --- the cache on disk ----------------------------------------------------


def test_a_second_load_answers_from_disk_without_fetching_again(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """The point of the cache: the same picture is fetched once and read from
    disk afterwards, so opening the catalog twice costs one request.
    """
    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)

        first = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )
        second = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

        assert route.call_count == 1
    assert decoded(second[47]) == ("image/png", PNG_BYTES)
    assert second == first


def test_an_image_that_moved_is_fetched_again_instead_of_the_previous_one(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """A mod whose picture moved to another URL must show the new picture: the
    cache is keyed by the whole URL, so the old entry is a miss, not a hit.
    """
    with respx.mock(assert_all_called=False) as router:
        hotlink_protected(router, IMAGE_URL, PNG_BYTES)
        moved = hotlink_protected(router, MOVED_URL, SECOND_BYTES)

        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache)
        loaded = thumbnails.load(
            {47: MOVED_URL}, client=client_factory(), cache_dir=image_cache
        )

        assert moved.call_count == 1
    assert decoded(loaded[47]) == ("image/png", SECOND_BYTES)


def drop_the_image(directory: Path) -> None:
    """Delete the cached picture and leave its description behind."""
    for image in images(directory):
        image.unlink()


def drop_the_description(directory: Path) -> None:
    """Delete the description and leave the picture behind."""
    for image in images(directory):
        sidecar(image).unlink()


def scramble_the_description(directory: Path) -> None:
    """Replace the description with something that is not even JSON."""
    for image in images(directory):
        sidecar(image).write_text("{ this was cut off", encoding="utf-8")


def describe_with_a_list(directory: Path) -> None:
    """Replace the description with valid JSON that describes nothing."""
    for image in images(directory):
        sidecar(image).write_text("[]", encoding="utf-8")


def describe_without_a_media_type(directory: Path) -> None:
    """Leave a description whose media type is no text."""
    for image in images(directory):
        content = json.dumps({"url": IMAGE_URL, "media_type": 7})
        sidecar(image).write_text(content, encoding="utf-8")


def describe_another_image(directory: Path) -> None:
    """Leave a description naming a picture other than the cached one."""
    for image in images(directory):
        content = json.dumps({"url": MOVED_URL, "media_type": "image/png"})
        sidecar(image).write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(drop_the_image, id="image_gone"),
        pytest.param(drop_the_description, id="description_gone"),
        pytest.param(scramble_the_description, id="description_is_not_json"),
        pytest.param(describe_with_a_list, id="description_is_a_list"),
        pytest.param(describe_without_a_media_type, id="media_type_is_not_text"),
        pytest.param(describe_another_image, id="description_names_another_image"),
    ],
)
def test_an_unusable_cache_entry_is_fetched_again_instead_of_raising(
    damage: Callable[[Path], None],
    client_factory: ClientFactory,
    image_cache: Path,
) -> None:
    """Whatever a killed process or a damaged disk left behind, the entry
    counts as missing and the picture is fetched once more: the cache is
    disposable, so nothing it holds may ever fail the view.
    """
    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)
        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache)
        damage(image_cache)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

        assert route.call_count == 2
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)


def test_a_picture_is_still_shown_when_it_cannot_be_cached_at_all(
    client_factory: ClientFactory, tmp_path: Path
) -> None:
    """A cache directory that does not exist costs the caller its cache, not
    its picture: the image is returned, nothing is created behind the caller's
    back and the next call simply fetches again.
    """
    missing = tmp_path / "not-created"
    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=missing
        )
        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=missing)

        assert route.call_count == 2
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert not missing.exists()


# --- the size of the cache ------------------------------------------------


def test_the_least_recently_used_images_are_dropped_at_the_cache_limit(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """Three big pictures outgrow the limit, so writing a fourth drops the one
    used longest ago -- with its description -- and stops there: the cache
    stays bounded without throwing away what is still in use.
    """
    third = MAX_CACHE_BYTES // 3 + 1
    oldest = seed_image(image_cache, "seeded-oldest", third, age=300)
    middle = seed_image(image_cache, "seeded-middle", third, age=200)
    newest = seed_image(image_cache, "seeded-newest", third, age=100)

    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )
        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache)

        assert route.call_count == 1
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert not oldest.exists()
    assert not sidecar(oldest).exists()
    assert middle.exists()
    assert newest.exists()


def test_a_cache_a_single_entry_grew_past_its_limit_is_emptied(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """One entry larger than the whole budget, dated ahead of everything else,
    is dropped last and still dropped: the sweep ends with an empty cache
    instead of leaving the limit broken forever.
    """
    giant = seed_image(image_cache, "seeded-giant", MAX_CACHE_BYTES * 2, age=-300)

    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )
        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache)

        assert route.call_count == 2
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert not giant.exists()


def test_entries_that_are_no_image_do_not_stop_the_cache_from_working(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """A stray directory and a link pointing nowhere are left alone and cost
    the sweep nothing: the picture is cached and read back from disk, so
    housekeeping never trips over what it did not put there.
    """
    stray = image_cache / "leftover-directory"
    stray.mkdir()
    dangling = image_cache / "leftover-link"
    dangling.symlink_to(image_cache / "gone")

    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )
        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache)

        assert route.call_count == 1
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert stray.is_dir()
    assert dangling.is_symlink()


def test_a_picture_another_process_is_writing_survives_the_sweep(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """A cache far over its limit is swept down, and the half-written file of
    another process -- the oldest entry in the directory, hence the first
    victim if it counted -- keeps its bytes: it belongs to that process, which
    moves it into place when it is done.
    """
    seed_image(image_cache, "seeded-giant", MAX_CACHE_BYTES * 2, age=-300)
    partial = image_cache / "seeded-key.4711-a1b2c3d4.part"
    partial.write_bytes(b"half a picture")
    written = time.time() - 900
    os.utime(partial, (written, written))

    with respx.mock(assert_all_called=False) as router:
        hotlink_protected(router, IMAGE_URL, PNG_BYTES)

        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )

    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)
    assert partial.read_bytes() == b"half a picture"


# --- clearing the cache ---------------------------------------------------


def test_clear_empties_the_cache_so_the_next_call_fetches_again(
    client_factory: ClientFactory, image_cache: Path
) -> None:
    """After clearing, the same picture is fetched a second time and nothing
    of the old entry is left; a stray directory the sweep cannot delete stops
    neither the clearing nor the next fetch.
    """
    stray = image_cache / "leftover-directory"
    stray.mkdir()
    with respx.mock(assert_all_called=False) as router:
        route = hotlink_protected(router, IMAGE_URL, PNG_BYTES)
        thumbnails.load({47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache)

        thumbnails.clear(image_cache)

        assert list(image_cache.iterdir()) == [stray]
        loaded = thumbnails.load(
            {47: IMAGE_URL}, client=client_factory(), cache_dir=image_cache
        )
        assert route.call_count == 2
    assert decoded(loaded[47]) == ("image/png", PNG_BYTES)


def test_clear_of_a_cache_directory_that_was_never_created_creates_nothing(
    tmp_path: Path,
) -> None:
    """Clearing before anything was ever cached must stay quiet and leave the
    directory absent, so the housekeeping of a fresh installation neither
    fails nor litters the cache directory.
    """
    missing = tmp_path / "not-created"

    thumbnails.clear(missing)

    assert not missing.exists()
