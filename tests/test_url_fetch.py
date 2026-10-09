"""``url_fetch`` skill and the attachment pipeline (SPEC §21, §25)."""

from __future__ import annotations

import base64

import httpx

from agent.net.attachments import AttachmentHandler
from agent.net.fetch import SafeFetcher
from agent.security.capabilities import CapabilitySet
from agent.skills.base import SkillContext
from agent.skills.url_fetch import UrlFetchSkill


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _context() -> SkillContext:
    return SkillContext(
        jid="alice@example.com",
        capabilities=CapabilitySet.from_mapping({"network": []}),
    )


# -- url_fetch ---------------------------------------------------------------


async def test_url_fetch_extracts_html_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body><h1>Title</h1><p>Body text</p></body></html>",
            headers={"content-type": "text/html"},
        )

    skill = UrlFetchSkill(SafeFetcher(client=_client(handler)))
    result = await skill.execute({"url": "http://example.com/"}, _context())
    assert result.ok
    assert "Title" in result.content
    assert "Body text" in result.content
    assert "UNTRUSTED_EXTERNAL_CONTENT" in result.content


async def test_url_fetch_rejects_ssrf() -> None:
    skill = UrlFetchSkill(SafeFetcher(client=_client(lambda r: httpx.Response(200))))
    result = await skill.execute({"url": "http://169.254.169.254/"}, _context())
    assert not result.ok
    assert "rejected" in (result.error or "").lower()


async def test_url_fetch_handles_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    skill = UrlFetchSkill(SafeFetcher(client=_client(handler)))
    result = await skill.execute({"url": "http://example.com/missing"}, _context())
    assert not result.ok
    assert "404" in (result.error or "")


async def test_url_fetch_rejects_binary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\x00\x01", headers={"content-type": "application/octet-stream"}
        )

    skill = UrlFetchSkill(SafeFetcher(client=_client(handler)))
    result = await skill.execute({"url": "http://example.com/blob"}, _context())
    assert not result.ok
    assert "content type" in (result.error or "").lower()


# -- attachments -------------------------------------------------------------


def _png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


async def test_attachment_image_becomes_data_url(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_png(), headers={"content-type": "image/png"})

    handler_obj = AttachmentHandler(fetcher=SafeFetcher(client=_client(handler)), max_size_mb=1)
    try:
        bundle = await handler_obj.process(["http://example.com/pic.png"])
        assert len(bundle.images) == 1
        assert bundle.images[0].startswith("data:image/png;base64,")
        assert bundle.attachments[0].kind == "image"
        assert "image attached" in bundle.text_summary()
    finally:
        handler_obj.cleanup(bundle)


async def test_attachment_text_extracted(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello attachment", headers={"content-type": "text/plain"})

    handler_obj = AttachmentHandler(fetcher=SafeFetcher(client=_client(handler)))
    try:
        bundle = await handler_obj.process(["http://example.com/notes.txt"])
        assert bundle.attachments[0].kind == "text"
        assert "hello attachment" in bundle.attachments[0].text
    finally:
        handler_obj.cleanup(bundle)


async def test_attachment_temp_files_cleaned(tmp_path) -> None:
    import os

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data", headers={"content-type": "text/plain"})

    handler_obj = AttachmentHandler(
        fetcher=SafeFetcher(client=_client(handler)), temp_dir=str(tmp_path)
    )
    bundle = await handler_obj.process(["http://example.com/a.txt"])
    temp_dir = bundle.temp_dir
    assert os.path.isdir(temp_dir)
    handler_obj.cleanup(bundle)
    assert not os.path.isdir(temp_dir)


async def test_attachment_ssrf_rejected() -> None:
    handler_obj = AttachmentHandler(
        fetcher=SafeFetcher(client=_client(lambda r: httpx.Response(200)))
    )
    bundle = await handler_obj.process(["http://10.0.0.1/secret"])
    assert not bundle.attachments[0].ok


async def test_attachment_size_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/plain"})

    handler_obj = AttachmentHandler(fetcher=SafeFetcher(client=_client(handler)), max_size_mb=0)
    # 0 MB means a 0-byte limit; everything is rejected.
    bundle = await handler_obj.process(["http://example.com/big.txt"])
    assert not bundle.attachments[0].ok
