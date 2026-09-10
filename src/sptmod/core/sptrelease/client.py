"""HTTP client for the list of published SPT releases (one of three modules importing httpx)."""

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

import httpx

from sptmod.core.errors import SptReleaseUnavailableError
from sptmod.core.models import Json

DEFAULT_RELEASES_URLS: Final = (
    "https://api.github.com/repos/SP-Tushonka/build/releases",
    "https://api.github.com/repos/sp-tarkov/build/releases",
)
RELEASES_PER_PAGE: Final = 100
DEFAULT_TIMEOUT: Final = 30.0
UNKNOWN_DATE: Final = datetime.min.replace(tzinfo=UTC)
ARCHIVE_SUFFIX: Final = ".7z"

_TAG_PATTERN: Final = re.compile(r"^v?(\d+\.\d+\.\d+)$")
_NAME_PATTERN: Final = re.compile(r"^SPT (\d+\.\d+\.\d+)(?: \((\d+)\))?$")


class SptReleaseEnv(StrEnum):
    """Names of the environment variables the release client reads."""

    RELEASES_URL = "SPTMOD_SPT_RELEASE_URL"


def spt_releases_urls() -> tuple[str, ...]:
    """Return the release list URLs: the single one the environment names, or the default sources."""
    named = os.environ.get(SptReleaseEnv.RELEASES_URL)
    return (named,) if named else DEFAULT_RELEASES_URLS


@dataclass(frozen=True)
class SptRelease:
    """One published SPT release.

    Attributes:
        version: The release version, for example ``4.1.2``.
        client_build: The Escape from Tarkov build the release needs, empty
            when the release does not name one.
        published_at: When the release was published; :data:`UNKNOWN_DATE`
            when the source sends no readable date.
        url: The address of the release page.
        download_url: The address of the release archive, empty when the
            release publishes none.
        archive_name: The file name of the release archive, empty when the
            release publishes none.
        content_length: The size of the release archive in bytes, None when
            the release publishes none.
    """

    version: str
    client_build: str
    published_at: datetime
    url: str
    download_url: str
    archive_name: str
    content_length: int | None


def _mapping(value: Json) -> Mapping[str, Json]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Json) -> str:
    return value if isinstance(value, str) else ""


def _optional_number(value: Json) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_published(value: Json) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value))
    except ValueError:
        return UNKNOWN_DATE
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_archive(assets: Json) -> tuple[str, str, int | None]:
    for asset in assets if isinstance(assets, list) else []:
        entry = _mapping(asset)
        name = _text(entry.get("name"))
        if name.lower().endswith(ARCHIVE_SUFFIX):
            return _text(entry.get("browser_download_url")), name, _optional_number(entry.get("size"))
    return "", "", None


def _parse_release(data: Mapping[str, Json]) -> SptRelease | None:
    """Parse one release entry, or return None when it carries no usable version."""
    if data.get("draft") is True or data.get("prerelease") is True:
        return None
    tag = _TAG_PATTERN.match(_text(data.get("tag_name")))
    name = _NAME_PATTERN.match(_text(data.get("name")))
    if tag is None or name is None:
        return None
    download_url, archive_name, content_length = _parse_archive(data.get("assets"))
    return SptRelease(
        version=tag.group(1),
        client_build=name.group(2) or "",
        published_at=_parse_published(data.get("published_at")),
        url=_text(data.get("html_url")),
        download_url=download_url,
        archive_name=archive_name,
        content_length=content_length,
    )


class SptReleaseClient:
    """Read-only client for the published SPT releases.

    The sources need no authentication and each is read in one request;
    releases beyond the first page are not fetched because the page holds
    every SPT release a source ever published. By default SPT's current and
    its former release source are read and merged.

    An explicit ``base_url`` or the environment variable
    ``SPTMOD_SPT_RELEASE_URL``, read at construction time, replaces
    :data:`DEFAULT_RELEASES_URLS` with that single URL, so a mirror can be
    pointed at without code changes.
    """

    def __init__(self, base_url: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Initialize an SptReleaseClient.

        Args:
            base_url: The single release list URL to read; defaults to
                ``SPTMOD_SPT_RELEASE_URL`` when that variable is set,
                otherwise to the official release sources.
            timeout: Request timeout in seconds (default: 30.0).
        """
        self.base_urls: tuple[str, ...] = (base_url,) if base_url else spt_releases_urls()
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/vnd.github+json"},
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool.

        After calling this, the client should not be reused.
        """
        self._http.close()

    def __enter__(self) -> "SptReleaseClient":
        """Context manager entry; returns self."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Context manager exit; closes the HTTP pool."""
        self.close()

    def releases(self) -> list[SptRelease]:
        """Fetch the published SPT releases of every configured source, newest first.

        Drafts, pre-releases and entries without a readable version are left
        out; a release without a published archive is kept, with empty
        download fields. A version published by more than one source is kept
        once, with the entry of the source read first.

        Returns:
            The parsed releases, ordered by publication date, newest first.

        Raises:
            SptReleaseUnavailableError: If a source is unreachable, answers
                with an error status, or sends a body that is not a list of
                releases.
        """
        merged: dict[str, SptRelease] = {}
        for url in self.base_urls:
            for release in self._fetch(url):
                merged.setdefault(release.version, release)
        return sorted(merged.values(), key=lambda release: release.published_at, reverse=True)

    def _fetch(self, base_url: str) -> Sequence[SptRelease]:
        try:
            response = self._http.get(base_url, params={"per_page": str(RELEASES_PER_PAGE)})
        except httpx.HTTPError as error:
            raise SptReleaseUnavailableError(
                "The SPT release list cannot be reached. Check your internet connection and try again."
            ) from error
        if not response.is_success:
            raise SptReleaseUnavailableError(
                f"The SPT release list is not available right now (HTTP {response.status_code}). Try again later."
            )
        try:
            payload: Json = response.json()
        except ValueError as error:
            raise SptReleaseUnavailableError("The SPT release list arrived unreadable.") from error
        if not isinstance(payload, list):
            raise SptReleaseUnavailableError("The SPT release list arrived in an unexpected shape.")
        return [release for item in payload if (release := _parse_release(_mapping(item))) is not None]
