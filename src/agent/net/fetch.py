"""Safe HTTP fetching shared by ``url_fetch`` and attachment download.

Every request re-validates the URL (including each redirect hop) against the
SSRF policy, enforces a byte cap and a timeout, and returns the content type
plus decoded body. Text extraction is left to :mod:`agent.net.extract`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from agent.errors import SecurityError
from agent.security.ssrf import validate_public_url

log = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_USER_AGENT = "XMPPAIAgent/0.1 (+https://example.invalid/agent)"


@dataclass
class FetchResult:
    url: str
    status_code: int
    content_type: str
    body: bytes
    truncated: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return self.content_type.startswith("text/") or self.content_type in {
            "application/json",
            "application/xml",
            "application/xhtml+xml",
            "application/rss+xml",
            "application/atom+xml",
        }


class SafeFetcher:
    """Fetches URLs with SSRF protection, redirect checks and size limits."""

    def __init__(
        self,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        timeout: float = 30.0,
        allow_private: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.allow_private = allow_private
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        )
        self._owns_client = client is None

    async def fetch(self, url: str) -> FetchResult:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            validated = validate_public_url(current, allow_private=self.allow_private)
            # Reject credentials in the URL: they must never reach logs or history.
            if "@" in current.split("://", 1)[-1].split("?", 1)[0]:
                raise SecurityError("URLs with embedded credentials are not allowed")

            try:
                async with self._client.stream("GET", validated.url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise SecurityError("Redirect without a Location header")
                        current = str(httpx.URL(validated.url).join(location))
                        continue
                    return await self._read(response, validated.url)
            except httpx.HTTPError as exc:
                raise SecurityError(
                    self._safe_message(f"Failed to fetch {current}: {exc}")
                ) from exc
        raise SecurityError(f"Too many redirects while fetching {url}")

    async def _read(self, response: httpx.Response, url: str) -> FetchResult:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                remaining = self.max_bytes - (total - len(chunk))
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                truncated = True
                break
            chunks.append(chunk)
        return FetchResult(
            url=url,
            status_code=response.status_code,
            content_type=content_type,
            body=b"".join(chunks),
            truncated=truncated,
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    @staticmethod
    def _safe_message(message: str) -> str:
        return message

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
