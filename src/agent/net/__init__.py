"""Networking helpers: safe HTTP fetch, content extraction and attachments."""

from agent.net.attachments import Attachment, AttachmentBundle, AttachmentHandler
from agent.net.extract import decode_bytes, extract_title, html_to_text, pretty_json
from agent.net.fetch import FetchResult, SafeFetcher

__all__ = [
    "Attachment",
    "AttachmentBundle",
    "AttachmentHandler",
    "FetchResult",
    "SafeFetcher",
    "decode_bytes",
    "extract_title",
    "html_to_text",
    "pretty_json",
]
