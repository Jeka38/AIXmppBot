"""Content extraction helpers (SPEC §21)."""

from __future__ import annotations

from agent.net.extract import decode_bytes, extract_title, html_to_text, pretty_json

_HTML = """
<html>
  <head>
    <title>Example Page</title>
    <style>body { color: red; }</style>
    <script>alert('x')</script>
  </head>
  <body>
    <h1>Welcome</h1>
    <p>First paragraph.</p>
    <p>Second paragraph.</p>
    <ul><li>one</li><li>two</li></ul>
  </body>
</html>
"""


def test_html_to_text_strips_markup_and_scripts() -> None:
    text = html_to_text(_HTML)
    assert "Welcome" in text
    assert "First paragraph." in text
    assert "Second paragraph." in text
    assert "alert" not in text
    assert "color: red" not in text
    assert "<p>" not in text


def test_extract_title() -> None:
    assert extract_title(_HTML) == "Example Page"


def test_decode_bytes_respects_charset() -> None:
    body = "Привет".encode("koi8-r")
    assert decode_bytes(body, "text/plain; charset=koi8-r") == "Привет"


def test_decode_bytes_falls_back_to_utf8() -> None:
    assert decode_bytes("héllo".encode()) == "héllo"


def test_pretty_json_formats() -> None:
    assert pretty_json('{"a":1}') == '{\n  "a": 1\n}'


def test_pretty_json_passthrough_on_invalid() -> None:
    assert pretty_json("not json") == "not json"


def test_html_parser_survives_malformed() -> None:
    assert "hello" in html_to_text("<p>hello<b>world")
