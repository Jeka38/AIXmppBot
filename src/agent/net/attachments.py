"""Attachment handling (SPEC §25).

Downloads XEP-0066 OOB attachments through the SSRF-guarded fetcher, enforces
the configured size limit, stores them in a per-invocation temp directory, and
prepares them for the LLM:

* images → base64 data URLs for a vision-capable model
* text/JSON/XML → decoded text
* PDF → text via ``pypdf`` when installed, otherwise a clear note

Temp files are removed by :meth:`AttachmentHandler.cleanup`.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agent.errors import AgentError
from agent.net.extract import decode_bytes, html_to_text
from agent.net.fetch import SafeFetcher
from agent.security.injection import sanitize_external

log = logging.getLogger(__name__)

_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}
_TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/xml",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
}
_PDF_MIMES = {"application/pdf"}
#: Per-file extracted-text cap fed to the LLM.
_MAX_TEXT_CHARS = 20000


@dataclass
class Attachment:
    url: str
    mime: str
    size: int
    kind: str  # image | text | pdf | other
    local_path: str = ""
    text: str = ""
    data_url: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class AttachmentBundle:
    attachments: list[Attachment] = field(default_factory=list)
    temp_dir: str = ""

    @property
    def images(self) -> list[str]:
        return [a.data_url for a in self.attachments if a.kind == "image" and a.data_url]

    def text_summary(self) -> str:
        blocks: list[str] = []
        for attachment in self.attachments:
            name = attachment.local_path or attachment.url
            if attachment.error:
                blocks.append(f"[attachment {name}: unavailable — {attachment.error}]")
            elif attachment.kind == "image":
                blocks.append(f"[image attached: {name} ({attachment.mime})]")
            elif attachment.text:
                blocks.append(f"[attachment {name} ({attachment.mime})]\n{attachment.text}")
            else:
                blocks.append(f"[attachment {name} ({attachment.mime}), no text extracted]")
        return "\n\n".join(blocks)


class AttachmentHandler:
    def __init__(
        self,
        *,
        fetcher: SafeFetcher,
        max_size_mb: int = 25,
        temp_dir: str | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.max_size = max_size_mb * 1024 * 1024
        self._base_temp = Path(temp_dir) if temp_dir else None

    async def process(self, urls: list[str]) -> AttachmentBundle:
        bundle = AttachmentBundle()
        if not urls:
            return bundle
        directory = Path(
            tempfile.mkdtemp(
                prefix="agent-att-",
                dir=str(self._base_temp) if self._base_temp else None,
            )
        )
        bundle.temp_dir = str(directory)
        for url in urls:
            bundle.attachments.append(await self._process_one(url, directory))
        return bundle

    async def _process_one(self, url: str, directory: Path) -> Attachment:
        try:
            result = await self.fetcher.fetch(url)
        except AgentError as exc:
            return Attachment(url=url, mime="", size=0, kind="other", error=str(exc))

        if result.status_code >= 400:
            return Attachment(
                url=url,
                mime="",
                size=0,
                kind="other",
                error=f"HTTP {result.status_code}",
            )
        if len(result.body) > self.max_size:
            return Attachment(
                url=url,
                mime=result.content_type,
                size=len(result.body),
                kind="other",
                error="file exceeds the configured size limit",
            )

        mime = result.content_type or _guess_mime(url)
        kind = _classify(mime)
        suffix = _safe_suffix(url, mime)
        path = directory / f"{uuid.uuid4().hex}{suffix}"
        path.write_bytes(result.body)

        attachment = Attachment(
            url=url, mime=mime, size=len(result.body), kind=kind, local_path=str(path)
        )
        if kind == "image":
            attachment.data_url = _to_data_url(mime, result.body)
        elif kind == "text":
            raw = decode_bytes(result.body, mime)
            text = html_to_text(raw) if "html" in mime or "xml" in mime else raw
            attachment.text = sanitize_external(text, max_chars=_MAX_TEXT_CHARS)
        elif kind == "pdf":
            attachment.text = sanitize_external(
                _extract_pdf_text(result.body), max_chars=_MAX_TEXT_CHARS
            )
        return attachment

    def cleanup(self, bundle: AttachmentBundle) -> None:
        if bundle.temp_dir:
            shutil.rmtree(bundle.temp_dir, ignore_errors=True)
            bundle.temp_dir = ""


def _classify(mime: str) -> str:
    mime = mime.lower()
    if mime in _IMAGE_MIMES or mime.startswith("image/"):
        return "image"
    if mime in _PDF_MIMES:
        return "pdf"
    if mime in _TEXT_MIMES or mime.startswith("text/"):
        return "text"
    return "other"


def _guess_mime(url: str) -> str:
    guessed, _ = mimetypes.guess_type(url)
    return guessed or "application/octet-stream"


def _safe_suffix(url: str, mime: str) -> str:
    import os

    _, ext = os.path.splitext(url.split("?", 1)[0])
    if ext and len(ext) <= 8 and ext[1:].isalnum():
        return ext.lower()
    guessed = mimetypes.guess_extension(mime) or ""
    return guessed if len(guessed) <= 8 else ""


def _to_data_url(mime: str, body: bytes) -> str:
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_pdf_text(body: bytes) -> str:
    """Extract text from a PDF using pypdf if available."""
    try:  # pragma: no cover - optional dependency
        import io

        from pypdf import PdfReader
    except Exception:
        return "[PDF received; text extraction requires the optional 'pypdf' package]"
    try:
        reader = PdfReader(io.BytesIO(body))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip() or "[PDF contained no extractable text]"
    except Exception as exc:  # noqa: BLE001 - corrupt PDFs must not crash
        return f"[PDF text extraction failed: {exc}]"
