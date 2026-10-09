"""SSRF protection and safe fetching (SPEC §21)."""

from __future__ import annotations

import httpx
import pytest

from agent.errors import SecurityError
from agent.net.fetch import SafeFetcher
from agent.security.ssrf import (
    SSRFError,
    is_blocked_ip,
    validate_public_url,
)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_blocked_ips(ip: str) -> None:
    assert is_blocked_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    assert not is_blocked_ip(ip)


def test_literal_private_ip_rejected() -> None:
    with pytest.raises(SSRFError):
        validate_public_url("http://127.0.0.1/admin")


def test_metadata_endpoint_rejected() -> None:
    with pytest.raises(SSRFError):
        validate_public_url("http://169.254.169.254/latest/meta-data/")


def test_non_http_scheme_rejected() -> None:
    with pytest.raises(SSRFError):
        validate_public_url("file:///etc/passwd")


def test_url_without_host_rejected() -> None:
    with pytest.raises(SSRFError):
        validate_public_url("http:///path")


def test_localhost_hostname_rejected() -> None:
    with pytest.raises(SSRFError):
        validate_public_url("http://localhost:8080/")


def test_allow_private_bypass() -> None:
    validated = validate_public_url("http://127.0.0.1:9000/", allow_private=True)
    assert validated.host == "127.0.0.1"
    assert validated.port == 9000


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_blocks_private_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    fetcher = SafeFetcher(client=_client(handler))
    with pytest.raises(SSRFError):
        await fetcher.fetch("http://example.com/redirect")


async def test_fetch_follows_public_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "http://example.com/final"})
        return httpx.Response(200, text="done", headers={"content-type": "text/plain"})

    fetcher = SafeFetcher(client=_client(handler))
    result = await fetcher.fetch("http://example.com/start")
    # Note: example.com resolves publicly; 169.254.169.254 must not be reachable.
    assert result.status_code == 200


async def test_fetch_truncates_large_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1000, headers={"content-type": "text/plain"})

    fetcher = SafeFetcher(max_bytes=100, client=_client(handler))
    result = await fetcher.fetch("http://example.com/big")
    assert len(result.body) == 100
    assert result.truncated is True


async def test_fetch_rejects_embedded_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    fetcher = SafeFetcher(client=_client(handler))
    with pytest.raises(SecurityError):
        await fetcher.fetch("http://user:pass@example.com/")


async def test_fetch_reports_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": True}, headers={"content-type": "application/json; charset=utf-8"}
        )

    fetcher = SafeFetcher(client=_client(handler))
    result = await fetcher.fetch("http://example.com/data.json")
    assert result.content_type == "application/json"
    assert result.is_text
