"""Paste service: long-answer upload and threshold handling."""

from __future__ import annotations

import httpx
import pytest

from agent.config.models import PasteConfig
from agent.net.paste import PasteError, PasteService


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_pastefy_returns_url_in_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/"
        body = request.read().decode("utf-8", "replace")
        assert "hello paste" in body
        assert "multipart/form-data" in request.headers.get("content-type", "")
        return httpx.Response(200, text="https://paste.example/abc123\n")

    service = PasteService("https://paste.example", field="f", client=_client(handler))
    assert await service.upload("hello paste") == "https://paste.example/abc123"


async def test_pastefy_sends_bearer_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, text="https://paste.example/tok")

    service = PasteService(
        "https://paste.example", field="f", token="secret-token", client=_client(handler)
    )
    await service.upload("x")
    assert seen["auth"] == "Bearer secret-token"


async def test_microbin_follows_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/upload"
        body = request.read().decode("utf-8", "replace")
        assert "hello paste" in body
        return httpx.Response(
            302, headers={"location": "https://paste.example/upload/red-fox-tail"}
        )

    service = PasteService("https://paste.example", field="content", client=_client(handler))
    assert await service.upload("hello paste") == "https://paste.example/upload/red-fox-tail"


async def test_microbin_relative_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/upload/green-owl-wing"})

    service = PasteService("https://paste.example", field="content", client=_client(handler))
    assert await service.upload("x") == "https://paste.example/upload/green-owl-wing"


async def test_upload_reports_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    service = PasteService("https://paste.example", client=_client(handler))
    with pytest.raises(PasteError):
        await service.upload("x")


async def test_upload_without_url_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="no url here")

    service = PasteService("https://paste.example", client=_client(handler))
    with pytest.raises(PasteError):
        await service.upload("x")


def test_paste_config_defaults() -> None:
    cfg = PasteConfig()
    assert cfg.enabled is False
    assert cfg.threshold == 250
    assert cfg.field == "f"


# -- application threshold behaviour -----------------------------------------


async def test_short_answer_not_pasted(tmp_path) -> None:
    from tests.test_app import _app

    app = _app(tmp_path)
    app.paste = None  # type: ignore[assignment]
    assert await app._maybe_paste("short") == "short"


async def test_long_answer_pasted_and_linked(tmp_path) -> None:
    from tests.test_app import _app

    app = _app(tmp_path)
    app.config.paste.enabled = True
    app.config.paste.threshold = 10
    app.config.paste.url = "https://paste.example"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="https://paste.example/long-one")

    app.paste = PasteService("https://paste.example", client=_client(handler))
    body = "x" * 500
    result = await app._maybe_paste(body)
    assert "https://paste.example/long-one" in result
    assert body not in result


async def test_paste_failure_falls_back_to_full_text(tmp_path) -> None:
    from tests.test_app import _app

    app = _app(tmp_path)
    app.config.paste.enabled = True
    app.config.paste.threshold = 5

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    app.paste = PasteService("https://paste.example", client=_client(handler))
    body = "y" * 100
    assert await app._maybe_paste(body) == body
