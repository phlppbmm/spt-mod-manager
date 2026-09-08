"""JSON builders for GitHub release API responses.

Not a test module: ``test_sptrelease_client`` imports these builders so the
shape of a mocked GitHub releases payload (as verified live against
``https://api.github.com/repos/sp-tarkov/build/releases``) lives in one place.
"""

from typing import Any


def asset_json(**overrides: Any) -> dict[str, Any]:
    """Build a GitHub release asset object with default values, overridable by kwargs."""
    data: dict[str, Any] = {
        "name": "SPT-4.1.2-40743.7z",
        "browser_download_url": "https://github.com/sp-tarkov/build/releases/download/v4.1.2/SPT-4.1.2-40743.7z",
        "size": 123456789,
    }
    data.update(overrides)
    return data


def release_json(**overrides: Any) -> dict[str, Any]:
    """Build a GitHub release object with a single ``.7z`` asset, overridable by kwargs.

    Defaults describe a well-formed, non-draft, non-prerelease SPT release
    with a tag, name, and archive asset that the client can parse fully.
    """
    data: dict[str, Any] = {
        "tag_name": "4.1.2",
        "name": "SPT 4.1.2 (40743)",
        "html_url": "https://github.com/sp-tarkov/build/releases/tag/4.1.2",
        "published_at": "2025-01-02T03:04:05Z",
        "draft": False,
        "prerelease": False,
        "assets": [asset_json()],
    }
    data.update(overrides)
    return data


def release_without_asset_json(**overrides: Any) -> dict[str, Any]:
    """Build a well-formed GitHub release object that publishes no assets."""
    return release_json(assets=[], **overrides)
