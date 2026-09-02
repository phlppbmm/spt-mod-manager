"""stream(): plain streaming GET for arbitrary (external) download URLs."""

import httpx
import respx

from test_forge_client_fixtures import client_factory, fake_clock, recording_sleep

__all__ = ["client_factory", "fake_clock", "recording_sleep"]

DOWNLOAD_URL = "https://cdn.example.com/releases/mod-1.0.0.zip"


def test_stream_delivers_chunks_and_exposes_headers(client_factory) -> None:
    body = b"0123456789" * 50  # 500 bytes
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={
                    "Content-Disposition": "attachment; filename=mod-1.0.0.zip",
                    "Content-Length": "500",
                },
            )
        )
        client = client_factory()

        with client.stream(DOWNLOAD_URL, chunk_size=64) as download:
            chunks = list(download.chunks)

        assert b"".join(chunks) == body
        assert len(chunks) > 1
        assert (
            download.headers["Content-Disposition"]
            == "attachment; filename=mod-1.0.0.zip"
        )
        assert download.headers["Content-Length"] == "500"


def test_stream_of_empty_body_yields_no_chunks(client_factory) -> None:
    with respx.mock() as router:
        router.get(DOWNLOAD_URL).mock(
            return_value=httpx.Response(
                200, content=b"", headers={"Content-Length": "0"}
            )
        )
        client = client_factory()

        with client.stream(DOWNLOAD_URL) as download:
            chunks = list(download.chunks)

        assert chunks == []
