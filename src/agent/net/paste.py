"""Upload long answers to a paste service and return the resulting URL.

Supports two response styles transparently:

* **Pastefy** (``paste.ubunix.pro``): ``POST /`` multipart with a field named by
  ``field`` (default ``f``), returns the URL as plain text. Optional Bearer
  token.
* **MicroBin** (``paste.ubunix.ru``): ``POST /upload`` multipart with a
  ``content`` field, answers ``302`` with the paste URL in ``Location``.

Only the endpoint and, optionally, the field name and token are configurable.
"""

from __future__ import annotations

import logging

import httpx

from agent.errors import AgentError

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class PasteError(AgentError):
    """A paste upload failed."""


class PasteService:
    """Upload text to a paste server and return its URL."""

    def __init__(
        self,
        base_url: str,
        *,
        field: str = "f",
        token: str = "",
        expiration: str = "3days",
        privacy: str = "unlisted",
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.field = field
        self.token = token
        self.expiration = expiration
        self.privacy = privacy
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    def _endpoint(self) -> str:
        # MicroBin exposes /upload; Pastefy accepts POST at the root.
        return f"{self.base_url}/upload" if self.field == "content" else f"{self.base_url}/"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _form(self, content: str) -> dict[str, tuple[str | None, str]]:
        # MicroBin uses a single ``content`` part; Pastefy uses ``f``.
        form: dict[str, tuple[str | None, str]] = {self.field: (None, content)}
        if self.field == "content":
            form["expiration"] = (None, self.expiration)
            form["privacy"] = (None, self.privacy)
            form["syntax_highlight"] = (None, "none")
        return form

    async def upload(self, content: str) -> str:
        """Upload *content* and return the URL of the created paste."""
        if not self.base_url:
            raise PasteError("No paste service URL configured")
        try:
            response = await self._client.post(
                self._endpoint(),
                files=self._form(content),
                headers=self._headers(),
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise PasteError(f"Paste upload failed: {exc}") from exc

        # MicroBin: 302 with the new paste in Location.
        location = response.headers.get("location")
        if response.status_code in (301, 302, 303, 307, 308) and location:
            return str(httpx.URL(str(response.request.url)).join(location))

        if response.status_code >= 400:
            raise PasteError(
                f"Paste service returned {response.status_code}: {response.text[:200]}"
            )

        # Pastefy: the URL is the plain-text response body.
        text = response.text.strip()
        if text.startswith("http"):
            return text
        raise PasteError("Paste service did not return a URL")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
