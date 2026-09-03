"""HTTP client for the read-only Forge API v0 (the only module importing httpx)."""

import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, Literal

import httpx

from sptmod.core.errors import ForgeApiError, ForgeUnavailableError

DEFAULT_BASE_URL: Final = "https://sp-mod.com/api/v0"
BASE_URL_ENV_VAR: Final = "SPTMOD_FORGE_URL"
MAX_PER_PAGE: Final = 50
DEFAULT_RATE_PER_MINUTE: Final = 200
DEFAULT_MAX_RETRIES: Final = 4
DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_CHUNK_SIZE: Final = 65536
RANGE_NOT_SATISFIABLE: Final = 416
BACKOFF_BASE_SECONDS: Final = 1.0
BACKOFF_MAX_SECONDS: Final = 60.0

type FikaCompatibility = Literal["compatible", "incompatible", "unknown"]


@dataclass(frozen=True)
class ForgeUser:
    """A mod owner or additional author."""

    id: int
    name: str
    profile_photo_url: str | None
    cover_photo_url: str | None


@dataclass(frozen=True)
class SourceCodeLink:
    """A source code link attached to a mod."""

    url: str
    label: str


@dataclass(frozen=True)
class ForgeVersionSummary:
    """The reduced version shape delivered by ``include=versions`` on /mods."""

    id: int
    version: str
    spt_version_constraint: str
    downloads: int
    published_at: str | None


@dataclass(frozen=True)
class ForgeLicense:
    """The license shape delivered by ``include=license``."""

    id: int
    name: str
    link: str


@dataclass(frozen=True)
class ForgeCategory:
    """The category shape delivered by ``include=category``."""

    id: int
    title: str
    slug: str


@dataclass(frozen=True)
class ForgeMod:
    """A mod as returned by /mods and /mod/{modId}.

    The detail endpoint adds description and custom_ai_disclosure fields,
    which are None when fetched from the list endpoint.
    """

    id: int
    hub_id: int | None
    guid: str | None
    name: str
    slug: str
    teaser: str | None
    thumbnail: str | None
    downloads: int
    favourites_count: int
    owner: ForgeUser | None
    additional_authors: list[ForgeUser]
    source_code_links: list[SourceCodeLink]
    detail_url: str
    fika_compatibility: bool | None
    featured: bool
    contains_ads: bool
    contains_ai_content: bool
    shows_profile_binding_notice: bool
    published_at: str | None
    created_at: str | None
    updated_at: str | None
    description: str | None = None
    custom_ai_disclosure: str | None = None
    endorsements_count: int = 0
    license: ForgeLicense | None = None
    category: ForgeCategory | None = None
    versions: list[ForgeVersionSummary] = field(default_factory=list)


@dataclass(frozen=True)
class ForgeVersionDependency:
    """An immediate dependency embedded in a version via ``include=dependencies``."""

    id: int
    mod_id: int
    mod_guid: str | None
    mod_name: str
    version_constraint: str
    is_optional: bool


@dataclass(frozen=True)
class ForgeVersion:
    """A mod version from /mod/{modId}/versions.

    Note: content_length may be None for historical versions. link is the
    download URL.
    """

    id: int
    hub_id: int | None
    version: str
    description: str | None
    link: str | None
    content_length: int | None
    spt_version_constraint: str
    downloads: int
    fika_compatibility: FikaCompatibility
    published_at: str | None
    created_at: str | None
    updated_at: str | None
    dependencies: list[ForgeVersionDependency] = field(default_factory=list)
    virus_total_links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PageMeta:
    """The ``meta`` block of a paginated response."""

    current_page: int
    last_page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class Page[T]:
    """One page of a paginated list response.

    Attributes:
        items: The items on this page.
        meta: Pagination metadata (current_page, last_page, per_page, total).
        next_url: The URL for the next page, or None if this is the last page.
    """

    items: list[T]
    meta: PageMeta
    next_url: str | None


@dataclass(frozen=True)
class CurrentVersion:
    """The installed version an update entry refers to.

    ``slug`` is only present in ``updates`` entries, not in ``blocked_updates``.
    """

    id: int
    mod_id: int
    guid: str | None
    name: str
    version: str
    slug: str | None = None


@dataclass(frozen=True)
class RecommendedVersion:
    """The version /mods/updates recommends installing."""

    id: int
    version: str
    link: str | None
    content_length: int | None
    fika_compatibility: FikaCompatibility
    spt_versions: list[str]


@dataclass(frozen=True)
class ModUpdate:
    """An available update for an installed mod version."""

    current_version: CurrentVersion
    recommended_version: RecommendedVersion
    update_reason: str


@dataclass(frozen=True)
class LatestVersion:
    """The newest version of a mod whose update is blocked."""

    id: int
    version: str
    spt_versions: list[str]


@dataclass(frozen=True)
class BlockingMod:
    """An installed mod whose dependency constraint blocks an update."""

    mod_id: int
    mod_guid: str | None
    mod_name: str
    current_version: str
    constraint: str
    incompatible_with: str


@dataclass(frozen=True)
class BlockedUpdate:
    """An update that exists but cannot be applied."""

    current_version: CurrentVersion
    latest_version: LatestVersion
    block_reason: str
    blocking_mods: list[BlockingMod] = field(default_factory=list)


@dataclass(frozen=True)
class UpToDateMod:
    """An installed mod version that is already current."""

    id: int
    mod_id: int
    guid: str | None
    name: str
    version: str
    spt_versions: list[str]


@dataclass(frozen=True)
class IncompatibleMod:
    """An installed mod version without a version for the queried SPT release."""

    id: int
    mod_id: int
    guid: str | None
    name: str
    version: str
    reason: str
    latest_compatible_version: str | None


@dataclass(frozen=True)
class UpdatesResult:
    """The parsed response from /mods/updates for installed mod versions.

    Attributes:
        spt_version: The SPT version that was queried.
        updates: Mods with available updates for the queried SPT version.
        blocked_updates: Mods with newer versions available but blocked by
            dependency constraints.
        up_to_date: Mods already at the recommended version.
        incompatible_with_spt: Mods without any version for the queried SPT release.
    """

    spt_version: str
    updates: list[ModUpdate]
    blocked_updates: list[BlockedUpdate]
    up_to_date: list[UpToDateMod]
    incompatible_with_spt: list[IncompatibleMod]


@dataclass(frozen=True)
class CompatibleVersion:
    """The version chosen for a dependency node."""

    id: int
    version: str
    link: str | None
    content_length: int | None
    fika_compatibility: FikaCompatibility


@dataclass(frozen=True)
class DependencyNode:
    """A node in the recursive dependency tree from /mods/dependencies.

    Attributes:
        id: The mod ID.
        guid: The mod GUID, or None if unknown.
        name: The mod name.
        slug: The mod slug.
        latest_compatible_version: The version chosen for this node (the
            latest one satisfying all constraints), or None if unavailable.
        conflict: True if no single version of this mod satisfies all constraints
            across the queried mods (version selection is global). Defaults to True
            if missing (fail-safe).
        dependencies: Direct dependencies of this node (recursive).
    """

    id: int
    guid: str | None
    name: str
    slug: str
    latest_compatible_version: CompatibleVersion | None
    conflict: bool
    dependencies: list["DependencyNode"]


@dataclass(frozen=True)
class DownloadStream:
    """An open streaming response for an arbitrary download URL."""

    status_code: int
    headers: Mapping[str, str]
    chunks: Iterator[bytes]


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _elements(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _mappings(value: object) -> list[Mapping[str, object]]:
    return [_mapping(item) for item in _elements(value)]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _texts(value: object) -> list[str]:
    return [item for item in _elements(value) if isinstance(item, str)]


def _number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object) -> bool:
    return value is True


def _optional_flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _fika(value: object) -> FikaCompatibility:
    if value == "compatible":
        return "compatible"
    if value == "incompatible":
        return "incompatible"
    return "unknown"


def _parse_user(data: Mapping[str, object]) -> ForgeUser:
    return ForgeUser(
        id=_number(data.get("id")),
        name=_text(data.get("name")),
        profile_photo_url=_optional_text(data.get("profile_photo_url")),
        cover_photo_url=_optional_text(data.get("cover_photo_url")),
    )


def _parse_source_code_link(data: Mapping[str, object]) -> SourceCodeLink:
    return SourceCodeLink(url=_text(data.get("url")), label=_text(data.get("label")))


def _parse_version_summary(data: Mapping[str, object]) -> ForgeVersionSummary:
    return ForgeVersionSummary(
        id=_number(data.get("id")),
        version=_text(data.get("version")),
        spt_version_constraint=_text(data.get("spt_version_constraint")),
        downloads=_number(data.get("downloads")),
        published_at=_optional_text(data.get("published_at")),
    )


def _parse_license(data: Mapping[str, object] | None) -> ForgeLicense | None:
    if data is None:
        return None
    return ForgeLicense(
        id=_number(data.get("id")),
        name=_text(data.get("name")),
        link=_text(data.get("link")),
    )


def _parse_category(data: Mapping[str, object] | None) -> ForgeCategory | None:
    if data is None:
        return None
    return ForgeCategory(
        id=_number(data.get("id")),
        # The live API names the field "title"; older payloads used "name".
        title=_text(data.get("title") or data.get("name")),
        slug=_text(data.get("slug")),
    )


def _parse_mod(data: Mapping[str, object]) -> ForgeMod:
    owner = _optional_mapping(data.get("owner"))
    return ForgeMod(
        id=_number(data.get("id")),
        hub_id=_optional_number(data.get("hub_id")),
        guid=_optional_text(data.get("guid")),
        name=_text(data.get("name")),
        slug=_text(data.get("slug")),
        teaser=_optional_text(data.get("teaser")),
        thumbnail=_optional_text(data.get("thumbnail")),
        downloads=_number(data.get("downloads")),
        favourites_count=_number(data.get("favourites_count")),
        endorsements_count=_number(data.get("endorsements_count")),
        owner=None if owner is None else _parse_user(owner),
        additional_authors=[
            _parse_user(item) for item in _mappings(data.get("additional_authors"))
        ],
        source_code_links=[
            _parse_source_code_link(item)
            for item in _mappings(data.get("source_code_links"))
        ],
        detail_url=_text(data.get("detail_url")),
        fika_compatibility=_optional_flag(data.get("fika_compatibility")),
        featured=_flag(data.get("featured")),
        contains_ads=_flag(data.get("contains_ads")),
        contains_ai_content=_flag(data.get("contains_ai_content")),
        shows_profile_binding_notice=_flag(data.get("shows_profile_binding_notice")),
        published_at=_optional_text(data.get("published_at")),
        created_at=_optional_text(data.get("created_at")),
        updated_at=_optional_text(data.get("updated_at")),
        description=_optional_text(data.get("description")),
        custom_ai_disclosure=_optional_text(data.get("custom_ai_disclosure")),
        license=_parse_license(_optional_mapping(data.get("license"))),
        category=_parse_category(_optional_mapping(data.get("category"))),
        versions=[
            _parse_version_summary(item) for item in _mappings(data.get("versions"))
        ],
    )


def _parse_version_dependency(data: Mapping[str, object]) -> ForgeVersionDependency:
    return ForgeVersionDependency(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        mod_guid=_optional_text(data.get("mod_guid")),
        mod_name=_text(data.get("mod_name")),
        version_constraint=_text(data.get("version_constraint")),
        is_optional=_flag(data.get("is_optional")),
    )


def _parse_version(data: Mapping[str, object]) -> ForgeVersion:
    return ForgeVersion(
        id=_number(data.get("id")),
        hub_id=_optional_number(data.get("hub_id")),
        version=_text(data.get("version")),
        description=_optional_text(data.get("description")),
        link=_optional_text(data.get("link")),
        content_length=_optional_number(data.get("content_length")),
        spt_version_constraint=_text(data.get("spt_version_constraint")),
        downloads=_number(data.get("downloads")),
        fika_compatibility=_fika(data.get("fika_compatibility")),
        published_at=_optional_text(data.get("published_at")),
        created_at=_optional_text(data.get("created_at")),
        updated_at=_optional_text(data.get("updated_at")),
        dependencies=[
            _parse_version_dependency(item)
            for item in _mappings(data.get("dependencies"))
        ],
        virus_total_links=_texts(data.get("virus_total_links")),
    )


def _parse_page[T](
    payload: Mapping[str, object], parse: Callable[[Mapping[str, object]], T]
) -> Page[T]:
    meta = _mapping(payload.get("meta"))
    links = _mapping(payload.get("links"))
    return Page(
        items=[parse(item) for item in _mappings(payload.get("data"))],
        meta=PageMeta(
            current_page=_number(meta.get("current_page")),
            last_page=_number(meta.get("last_page")),
            per_page=_number(meta.get("per_page")),
            total=_number(meta.get("total")),
        ),
        next_url=_optional_text(links.get("next")),
    )


def _parse_current_version(data: Mapping[str, object]) -> CurrentVersion:
    return CurrentVersion(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        guid=_optional_text(data.get("guid")),
        name=_text(data.get("name")),
        version=_text(data.get("version")),
        slug=_optional_text(data.get("slug")),
    )


def _parse_recommended_version(data: Mapping[str, object]) -> RecommendedVersion:
    return RecommendedVersion(
        id=_number(data.get("id")),
        version=_text(data.get("version")),
        link=_optional_text(data.get("link")),
        content_length=_optional_number(data.get("content_length")),
        fika_compatibility=_fika(data.get("fika_compatibility")),
        spt_versions=_texts(data.get("spt_versions")),
    )


def _parse_update(data: Mapping[str, object]) -> ModUpdate:
    return ModUpdate(
        current_version=_parse_current_version(_mapping(data.get("current_version"))),
        recommended_version=_parse_recommended_version(
            _mapping(data.get("recommended_version"))
        ),
        update_reason=_text(data.get("update_reason")),
    )


def _parse_latest_version(data: Mapping[str, object]) -> LatestVersion:
    return LatestVersion(
        id=_number(data.get("id")),
        version=_text(data.get("version")),
        spt_versions=_texts(data.get("spt_versions")),
    )


def _parse_blocking_mod(data: Mapping[str, object]) -> BlockingMod:
    return BlockingMod(
        mod_id=_number(data.get("mod_id")),
        mod_guid=_optional_text(data.get("mod_guid")),
        mod_name=_text(data.get("mod_name")),
        current_version=_text(data.get("current_version")),
        constraint=_text(data.get("constraint")),
        incompatible_with=_text(data.get("incompatible_with")),
    )


def _parse_blocked_update(data: Mapping[str, object]) -> BlockedUpdate:
    return BlockedUpdate(
        current_version=_parse_current_version(_mapping(data.get("current_version"))),
        latest_version=_parse_latest_version(_mapping(data.get("latest_version"))),
        block_reason=_text(data.get("block_reason")),
        blocking_mods=[
            _parse_blocking_mod(item) for item in _mappings(data.get("blocking_mods"))
        ],
    )


def _parse_up_to_date(data: Mapping[str, object]) -> UpToDateMod:
    return UpToDateMod(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        guid=_optional_text(data.get("guid")),
        name=_text(data.get("name")),
        version=_text(data.get("version")),
        spt_versions=_texts(data.get("spt_versions")),
    )


def _parse_incompatible(data: Mapping[str, object]) -> IncompatibleMod:
    return IncompatibleMod(
        id=_number(data.get("id")),
        mod_id=_number(data.get("mod_id")),
        guid=_optional_text(data.get("guid")),
        name=_text(data.get("name")),
        version=_text(data.get("version")),
        reason=_text(data.get("reason")),
        latest_compatible_version=_optional_text(data.get("latest_compatible_version")),
    )


def _parse_updates(data: Mapping[str, object]) -> UpdatesResult:
    return UpdatesResult(
        spt_version=_text(data.get("spt_version")),
        updates=[_parse_update(item) for item in _mappings(data.get("updates"))],
        blocked_updates=[
            _parse_blocked_update(item)
            for item in _mappings(data.get("blocked_updates"))
        ],
        up_to_date=[
            _parse_up_to_date(item) for item in _mappings(data.get("up_to_date"))
        ],
        incompatible_with_spt=[
            _parse_incompatible(item)
            for item in _mappings(data.get("incompatible_with_spt"))
        ],
    )


def _parse_compatible_version(
    data: Mapping[str, object] | None,
) -> CompatibleVersion | None:
    if data is None:
        return None
    return CompatibleVersion(
        id=_number(data.get("id")),
        version=_text(data.get("version")),
        link=_optional_text(data.get("link")),
        content_length=_optional_number(data.get("content_length")),
        fika_compatibility=_fika(data.get("fika_compatibility")),
    )


def _parse_dependency_node(data: Mapping[str, object]) -> DependencyNode:
    """Parse a dependency tree node from the API response.

    The conflict field treats anything not JSON false as True (fail-safe:
    missing or null conflict becomes conflict=True, which is safer than
    silently assuming no conflict).
    """
    return DependencyNode(
        id=_number(data.get("id")),
        guid=_optional_text(data.get("guid")),
        name=_text(data.get("name")),
        slug=_text(data.get("slug")),
        latest_compatible_version=_parse_compatible_version(
            _optional_mapping(data.get("latest_compatible_version"))
        ),
        # Anything not JSON false counts as a conflict (fail-safe).
        conflict=data.get("conflict") is not False,
        dependencies=[
            _parse_dependency_node(item) for item in _mappings(data.get("dependencies"))
        ],
    )


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
            base_url = os.environ.get(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL
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
    ) -> Mapping[str, object]:
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
            payload = _mapping(response.json())
        except ValueError as error:
            raise ForgeUnavailableError(
                f"{url} answered with a non-JSON body: {error}"
            ) from error
        if not _flag(payload.get("success")):
            raise ForgeApiError(
                code=_text(payload.get("code")) or "UNKNOWN",
                message=_text(payload.get("message")),
                status=response.status_code,
            )
        return payload

    def _iter_pages[T](
        self,
        url: str,
        params: dict[str, str],
        parse: Callable[[Mapping[str, object]], T],
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
            page = _parse_page(self._fetch(next_url, next_params), parse)
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
        return _parse_page(self._fetch("/mods", params), _parse_mod)

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
        return self._iter_pages("/mods", params, _parse_mod)

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
        return _parse_mod(_mapping(payload.get("data")))

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
        return _parse_page(payload, _parse_version)

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
        return self._iter_pages(f"/mod/{mod_id}/versions", params, _parse_version)

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
        return _parse_updates(_mapping(payload.get("data")))

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
            pair: [_parse_dependency_node(node) for node in _mappings(nodes)]
            for pair, nodes in _mapping(payload.get("data")).items()
        }

    @contextmanager
    def stream(
        self,
        url: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[DownloadStream]:
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
