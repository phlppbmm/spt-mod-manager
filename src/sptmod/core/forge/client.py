"""HTTP client for the read-only Forge API v0 (the only module importing httpx)."""

import os
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import StrEnum
from typing import Final

import httpx

from sptmod.core.errors import ForgeApiError, ForgeUnavailableError
from sptmod.core.forge.models import (
    BlockedUpdate,
    BlockingMod,
    CompatibleVersion,
    CurrentVersion,
    DependencyNode,
    DownloadStream,
    FikaCompatibility,
    ForgeCategory,
    ForgeLicense,
    ForgeMod,
    ForgeUser,
    ForgeVersion,
    ForgeVersionDependency,
    ForgeVersionSummary,
    IncompatibleMod,
    LatestVersion,
    ModUpdate,
    Page,
    PageMeta,
    RecommendedVersion,
    SourceCodeLink,
    UpdatesResult,
    UpToDateMod,
)
from sptmod.core.forge.parse import (
    flag,
    mapping,
    mappings,
    parse_dependency_node,
    parse_mod,
    parse_page,
    parse_updates,
    parse_version,
    text,
)
from sptmod.core.models import Json

# The dataclasses live in forge.models and the coercion in forge.parse, but
# forge.client stays the single import site for the whole Forge API surface.
__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_MAX_SECONDS",
    "DEFAULT_BASE_URL",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RATE_PER_MINUTE",
    "DEFAULT_TIMEOUT",
    "MAX_PER_PAGE",
    "RANGE_NOT_SATISFIABLE",
    "BlockedUpdate",
    "BlockingMod",
    "CompatibleVersion",
    "CurrentVersion",
    "DependencyNode",
    "DownloadStream",
    "FikaCompatibility",
    "ForgeCategory",
    "ForgeClient",
    "ForgeEnv",
    "ForgeLicense",
    "ForgeMod",
    "ForgeUser",
    "ForgeVersion",
    "ForgeVersionDependency",
    "ForgeVersionSummary",
    "IncompatibleMod",
    "LatestVersion",
    "ModUpdate",
    "Page",
    "PageMeta",
    "RecommendedVersion",
    "SourceCodeLink",
    "UpToDateMod",
    "UpdatesResult",
    "forge_base_url",
]

DEFAULT_BASE_URL: Final = "https://sp-mod.com/api/v0"
MAX_PER_PAGE: Final = 50
DEFAULT_RATE_PER_MINUTE: Final = 200
DEFAULT_MAX_RETRIES: Final = 4
DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_CHUNK_SIZE: Final = 65536
RANGE_NOT_SATISFIABLE: Final = 416
BACKOFF_BASE_SECONDS: Final = 1.0
BACKOFF_MAX_SECONDS: Final = 60.0


class ForgeEnv(StrEnum):
    """Names of the environment variables the Forge client reads."""

    BASE_URL = "SPTMOD_FORGE_URL"


def forge_base_url() -> str:
    """Return the Forge base URL from the environment, or the default when unset or empty."""
    return os.environ.get(ForgeEnv.BASE_URL) or DEFAULT_BASE_URL


def _serialize_mods(mods: Sequence[tuple[str, str]]) -> str:
    return ",".join(f"{identifier}:{version}" for identifier, version in mods)


def _build_params(
    *,
    query: str | None = None,
    filters: Mapping[str, str] | None = None,
    sort: str | None = None,
    include: Sequence[str] | None = None,
    fields: Sequence[str] | None = None,
    page: int | None = None,
    per_page: int | None = None,
) -> dict[str, str]:
    params: dict[str, str] = {}
    if query is not None:
        params["query"] = query
    for name, value in (filters or {}).items():
        params[f"filter[{name}]"] = value
    if sort is not None:
        params["sort"] = sort
    if include:
        params["include"] = ",".join(include)
    if fields:
        params["fields"] = ",".join(fields)
    if page is not None:
        params["page"] = str(page)
    if per_page is not None:
        params["per_page"] = str(min(per_page, MAX_PER_PAGE))
    return params


def _retry_after_seconds(value: str | None) -> float:
    """Parse the Retry-After header value (seconds form only).

    Only delay-seconds form (numeric) is honoured. HTTP-date form yields 0.0
    and the caller falls back to exponential backoff. Returns 0.0 for missing
    or malformed values.
    """
    # Only the delay-seconds form is honoured; an HTTP-date Retry-After
    # yields 0.0 and the caller falls back to its exponential backoff.
    if value is None:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 0.0


class ForgeClient:
    """Read-only client for the Forge API v0.

    The API requires no authentication and is open/read-only. All requests
    (except ``/ping``) are rate-limited with a client-side token bucket and
    retried with exponential backoff on 429 or 5xx responses. An optional
    ``Retry-After`` header is honoured if present.

    Without an explicit ``base_url`` the environment variable
    ``SPTMOD_FORGE_URL`` is read at construction time and overrides
    :data:`DEFAULT_BASE_URL`, so a test or staging Forge can be pointed at
    without code changes.

    Warning:
        A sparse fieldset (``fields=``) must always list ``id``: fields the
        server omits are filled with defaults, so a missing ``id`` silently
        becomes ``0``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        rate_per_minute: int = DEFAULT_RATE_PER_MINUTE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize a ForgeClient.

        Args:
            base_url: The API base URL; defaults to ``SPTMOD_FORGE_URL`` when
                that variable is set, otherwise to Forge production.
            rate_per_minute: Throttle rate in requests per minute (must be > 0).
                Defaults to 200.
            max_retries: Maximum number of retries on 429 or 5xx (default: 4).
            timeout: Request timeout in seconds (default: 30.0).
            clock: Callable returning the current monotonic time
                (default: time.monotonic). Injected for testing.
            sleep: Callable to sleep for a given duration in seconds
                (default: time.sleep). Injected for testing.

        Raises:
            ValueError: If ``rate_per_minute`` is not greater than 0.
        """
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be greater than 0")
        if base_url is None:
            base_url = forge_base_url()
        self.base_url = base_url.rstrip("/")
        self.rate_per_minute = rate_per_minute
        self.max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(rate_per_minute)
        self._refilled_at = clock()
        self._http = httpx.Client(
            base_url=self.base_url, timeout=timeout, follow_redirects=True
        )

    def _take_token(self) -> None:
        """Token bucket refill and consume logic.

        Tokens refill at rate_per_minute/60 per second, capped at rate_per_minute.
        If a token is not immediately available, sleep to reach the next token.
        """
        per_second = self.rate_per_minute / 60.0
        now = self._clock()
        self._tokens = min(
            float(self.rate_per_minute),
            self._tokens + (now - self._refilled_at) * per_second,
        )
        self._refilled_at = now
        if self._tokens < 1.0:
            self._sleep((1.0 - self._tokens) / per_second)
            self._tokens = 1.0
        self._tokens -= 1.0

    def _send(self, url: str, params: Mapping[str, str] | None) -> httpx.Response:
        """Send a GET request with token bucket throttling and retry on 429/5xx.

        Retries up to max_retries times with exponential backoff. Network errors
        and 429/5xx responses trigger a retry. A Retry-After header (delay-seconds
        form only) takes precedence over exponential backoff if provided.

        Args:
            url: Relative or absolute URL.
            params: Query parameters to append.

        Returns:
            An httpx.Response with status < 500 and != 429.

        Raises:
            ForgeUnavailableError: If all retries are exhausted.
        """
        reason = f"{url} is unreachable"
        for attempt in range(self.max_retries + 1):
            self._take_token()
            retry_after = 0.0
            try:
                response = self._http.get(url, params=params)
            except httpx.HTTPError as error:
                reason = f"{url} failed: {error}"
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                reason = f"{url} answered with HTTP {response.status_code}"
                # Only delay-seconds form of Retry-After is honoured;
                # HTTP-date form yields 0.0 and falls back to exponential backoff.
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if attempt < self.max_retries:
                backoff = min(BACKOFF_BASE_SECONDS * 2**attempt, BACKOFF_MAX_SECONDS)
                self._sleep(max(backoff, retry_after))
        raise ForgeUnavailableError(reason)

    def _fetch(
        self, url: str, params: Mapping[str, str] | None = None
    ) -> Mapping[str, Json]:
        """Fetch and parse a response, unwrapping the success envelope.

        Maps success=false responses to ForgeApiError with code, message, and
        HTTP status. Falls back to code='UNKNOWN' if the API omits it.

        Args:
            url: Relative or absolute URL.
            params: Query parameters to append.

        Returns:
            The unwrapped payload (the top-level dict).

        Raises:
            ForgeUnavailableError: On non-JSON response body.
            ForgeApiError: If the response envelope has success=false.
        """
        response = self._send(url, params)
        try:
            payload = mapping(response.json())
        except ValueError as error:
            raise ForgeUnavailableError(
                f"{url} answered with a non-JSON body: {error}"
            ) from error
        if not flag(payload.get("success")):
            raise ForgeApiError(
                code=text(payload.get("code")) or "UNKNOWN",
                message=text(payload.get("message")),
                status=response.status_code,
            )
        return payload

    def _iter_pages[T](
        self,
        url: str,
        params: dict[str, str],
        parse: Callable[[Mapping[str, Json]], T],
    ) -> Iterator[T]:
        """Yield items from all pages of a paginated resource.

        Pagination stops when current_page >= last_page or when a repeat URL is
        encountered (API inconsistency guard). If the response lacks a meta block,
        iteration stops after the first page. The next_url chain is followed from
        the response envelope.

        Args:
            url: Starting URL.
            params: Query parameters for the first page only.
            parse: Parser function to convert raw dicts to typed objects.

        Yields:
            Parsed items from every page.
        """
        next_url: str | None = url
        next_params: dict[str, str] | None = params
        fetched: set[str] = set()
        while next_url is not None and next_url not in fetched:
            fetched.add(next_url)
            page = parse_page(self._fetch(next_url, next_params), parse)
            yield from page.items
            if page.meta.current_page >= page.meta.last_page:
                return
            next_url = page.next_url
            next_params = None

    def close(self) -> None:
        """Close the underlying HTTP connection pool.

        After calling this, the client should not be reused.
        """
        self._http.close()

    def __enter__(self) -> "ForgeClient":
        """Context manager entry; returns self."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Context manager exit; closes the HTTP pool."""
        self.close()

    def ping(self) -> bool:
        """Check if the API is reachable on /ping.

        This request bypasses the token bucket throttle. Returns False on network
        errors or non-success HTTP status; True only if the response is 2xx.

        Returns:
            True if the server answered with a 2xx status, False otherwise.
        """
        try:
            response = self._http.get("/ping")
        except httpx.HTTPError:
            return False
        return response.is_success

    def mods(
        self,
        *,
        query: str | None = None,
        filters: Mapping[str, str] | None = None,
        sort: str | None = None,
        include: Sequence[str] | None = None,
        fields: Sequence[str] | None = None,
        page: int = 1,
        per_page: int = MAX_PER_PAGE,
    ) -> Page[ForgeMod]:
        """Fetch one page of the mod catalog.

        Args:
            query: Meilisearch full-text query (searches name/slug/description).
            filters: Filter parameters as {key: value}; sent as filter[key]=value.
            sort: Comma-separated sort fields; prefix with '-' for DESC.
            include: Optional inclusions ('versions', 'license', 'category',
                'source_code_links').
            fields: Sparse fieldset (comma-separated); must include 'id'.
            page: Page number (1-indexed).
            per_page: Items per page; clamped to MAX_PER_PAGE (50).

        Returns:
            A Page object with items, meta (current_page, last_page, etc.),
            and next_url.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error response (e.g. VALIDATION_FAILED).
        """
        params = _build_params(
            query=query,
            filters=filters,
            sort=sort,
            include=include,
            fields=fields,
            page=page,
            per_page=per_page,
        )
        return parse_page(self._fetch("/mods", params), parse_mod)

    def iter_mods(
        self,
        *,
        query: str | None = None,
        filters: Mapping[str, str] | None = None,
        sort: str | None = None,
        include: Sequence[str] | None = None,
        fields: Sequence[str] | None = None,
        per_page: int = MAX_PER_PAGE,
    ) -> Iterator[ForgeMod]:
        """Yield every mod from the catalog, automatically following pagination.

        Args:
            query: Meilisearch full-text query.
            filters: Filter parameters as {key: value}.
            sort: Comma-separated sort fields.
            include: Optional inclusions ('versions', 'license', 'category',
                'source_code_links').
            fields: Sparse fieldset (must include 'id').
            per_page: Items per page; clamped to MAX_PER_PAGE.

        Yields:
            ForgeMod objects from every page until exhausted.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error response.
        """
        params = _build_params(
            query=query,
            filters=filters,
            sort=sort,
            include=include,
            fields=fields,
            page=1,
            per_page=per_page,
        )
        return self._iter_pages("/mods", params, parse_mod)

    def mod(
        self,
        mod_id: int,
        *,
        include: Sequence[str] | None = None,
        fields: Sequence[str] | None = None,
    ) -> ForgeMod:
        """Fetch the detail view of a single mod.

        Args:
            mod_id: The numeric mod ID.
            include: Optional inclusions ('versions', 'license', 'category',
                'source_code_links'); detail view adds 'description' and
                'custom_ai_disclosure' vs. list view.
            fields: Sparse fieldset (must include 'id').

        Returns:
            A ForgeMod object with all available fields.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error (e.g., NOT_FOUND for invalid ID).
        """
        params = _build_params(include=include, fields=fields)
        payload = self._fetch(f"/mod/{mod_id}", params)
        return parse_mod(mapping(payload.get("data")))

    def mod_versions(
        self,
        mod_id: int,
        *,
        filters: Mapping[str, str] | None = None,
        sort: str | None = None,
        include: Sequence[str] | None = None,
        fields: Sequence[str] | None = None,
        page: int = 1,
        per_page: int = MAX_PER_PAGE,
    ) -> Page[ForgeVersion]:
        """Fetch one page of versions for a mod.

        Args:
            mod_id: The numeric mod ID.
            filters: Filter parameters as {key: value}.
            sort: Comma-separated sort fields.
            include: Optional inclusions ('dependencies', 'virus_total_links').
            fields: Sparse fieldset (must include 'id').
            page: Page number (1-indexed).
            per_page: Items per page; clamped to MAX_PER_PAGE.

        Returns:
            A Page object with ForgeVersion items, meta, and next_url.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error response.
        """
        params = _build_params(
            filters=filters,
            sort=sort,
            include=include,
            fields=fields,
            page=page,
            per_page=per_page,
        )
        payload = self._fetch(f"/mod/{mod_id}/versions", params)
        return parse_page(payload, parse_version)

    def iter_mod_versions(
        self,
        mod_id: int,
        *,
        filters: Mapping[str, str] | None = None,
        sort: str | None = None,
        include: Sequence[str] | None = None,
        fields: Sequence[str] | None = None,
        per_page: int = MAX_PER_PAGE,
    ) -> Iterator[ForgeVersion]:
        """Yield every version of a mod, automatically following pagination.

        Args:
            mod_id: The numeric mod ID.
            filters: Filter parameters as {key: value}.
            sort: Comma-separated sort fields.
            include: Optional inclusions ('dependencies', 'virus_total_links').
            fields: Sparse fieldset (must include 'id').
            per_page: Items per page; clamped to MAX_PER_PAGE.

        Yields:
            ForgeVersion objects from every page until exhausted.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error response.
        """
        params = _build_params(
            filters=filters,
            sort=sort,
            include=include,
            fields=fields,
            page=1,
            per_page=per_page,
        )
        return self._iter_pages(f"/mod/{mod_id}/versions", params, parse_version)

    def updates(
        self, mods: Sequence[tuple[str, str]], spt_version: str
    ) -> UpdatesResult:
        """Check for available updates for installed mods.

        Args:
            mods: List of (identifier, version) tuples; identifier is a numeric
                mod ID (str) or GUID.
            spt_version: The SPT version to check updates for (exact match required,
                e.g., '3.11.5').

        Returns:
            An UpdatesResult with updates, blocked_updates, up_to_date, and
            incompatible_with_spt lists, plus the queried spt_version.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error (e.g., VALIDATION_FAILED for unknown version).
        """
        params = {"mods": _serialize_mods(mods), "spt_version": spt_version}
        payload = self._fetch("/mods/updates", params)
        return parse_updates(mapping(payload.get("data")))

    def dependencies(
        self, mods: Sequence[tuple[str, str]], spt_version: str
    ) -> dict[str, list[DependencyNode]]:
        """Resolve the dependency tree for installed mods.

        Args:
            mods: List of (identifier, version) tuples; identifier is a numeric
                mod ID (str) or GUID.
            spt_version: The SPT version to resolve dependencies for (exact match
                required, e.g., '3.11.5').

        Returns:
            A dict keyed by each queried 'identifier:version' pair (verbatim),
            whose value is the list of that mod's direct dependencies (empty
            when it has none); each node carries its own dependencies
            recursively. Unresolvable pairs are omitted entirely, which is
            distinct from an empty list. The conflict field is True when no
            single version satisfies all constraints across queried mods.

        Raises:
            ForgeUnavailableError: On network error or 5xx responses.
            ForgeApiError: On API error (e.g., VALIDATION_FAILED for unknown version).
        """
        params = {"mods": _serialize_mods(mods), "spt_version": spt_version}
        payload = self._fetch("/mods/dependencies", params)
        return {
            pair: [parse_dependency_node(node) for node in mappings(nodes)]
            for pair, nodes in mapping(payload.get("data")).items()
        }

    @contextmanager
    def stream(
        self,
        url: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        headers: Mapping[str, str] | None = None,
    ) -> Generator[DownloadStream]:
        """Stream an arbitrary download URL without envelope handling or throttling.

        This method bypasses the token bucket and API envelope envelope parsing.
        Raises on any HTTP error (non-2xx status or network issues). The response
        is guaranteed to be closed on all code paths (normal iteration, exception,
        or early exit) because it uses a context manager. Download links answer
        with a 307 to a CDN URL, which the client follows.

        Args:
            url: Absolute or relative URL to download from.
            chunk_size: Bytes per chunk (default: 65536).
            headers: Request headers to send, for example a ``Range`` header
                continuing an interrupted download.

        Yields:
            A DownloadStream with the response status, its headers and an
            iterator of byte chunks.

        Raises:
            ForgeUnavailableError: On network error or HTTP error status. A 416
                answer to a ``Range`` request is passed on unraised so the
                caller can drop its unusable partial file and start over.
        """
        try:
            with self._http.stream("GET", url, headers=headers) as response:
                rejected_range = (
                    response.status_code == RANGE_NOT_SATISFIABLE
                    and headers is not None
                    and "Range" in headers
                )
                if not rejected_range:
                    response.raise_for_status()
                yield DownloadStream(
                    status_code=response.status_code,
                    headers=response.headers,
                    chunks=response.iter_bytes(chunk_size),
                )
        except httpx.HTTPError as error:
            raise ForgeUnavailableError(
                f"{url} cannot be downloaded: {error}"
            ) from error
