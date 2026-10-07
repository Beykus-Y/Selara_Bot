"""Stdlib HTML parsing for web search: result extraction and text conversion.

Only the standard library is used on purpose -- the parsed HTML is untrusted
external content, and both parsers must survive arbitrarily malformed input
without ever raising into the caller.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "ul",
})

_DUCK_HOSTS = {"duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com", "lite.duckduckgo.com"}


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)


def extract_text(html: str) -> str:
    """Human-readable text of an HTML document; never raises on malformed input."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    lines = (_clean(line) for line in "".join(parser._parts).split("\n"))
    return "\n".join(line for line in lines if line)


def extract_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    if match is None:
        return ""
    return _clean(unescape(match.group(1)))


class _LiteResultsParser(HTMLParser):
    """Collector for the DuckDuckGo lite result page: result-link anchors and
    result-snippet cells are emitted in matching order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.urls: list[str] = []
        self.snippets: list[str] = []
        self._in_link = False
        self._in_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result-link" in classes:
            self._in_link = True
            self._title_parts = []
            self._href = attributes.get("href") or ""
        elif tag == "td" and "result-snippet" in classes:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_link and tag == "a":
            self._in_link = False
            self.titles.append(_clean("".join(self._title_parts)))
            self.urls.append(self._href)
        elif self._in_snippet and tag == "td":
            self._in_snippet = False
            self.snippets.append(_clean("".join(self._snippet_parts)))

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)


def _unwrap_ddg_link(href: str) -> str:
    """Resolve //duckduckgo.com/l/?uddg=<encoded-url> tracking redirects;
    plain direct hrefs are returned unchanged."""
    value = (href or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    if "://" not in value:
        return ""
    try:
        from urllib.parse import parse_qs, unquote, urlsplit

        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.netloc.lower() in _DUCK_HOSTS and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [""])[0]
        if re.match(r"^https?%3a", target, re.IGNORECASE):
            # Double-encoded target: parse_qs consumed one level only.
            target = unquote(target)
        return target
    return value


def parse_lite_results(html: str, *, limit: int) -> list[tuple[str, str, str]]:
    """(title, url, snippet) triples from a DuckDuckGo lite results page."""
    parser = _LiteResultsParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    results: list[tuple[str, str, str]] = []
    for title, url, snippet in zip(parser.titles, parser.urls, parser.snippets):
        resolved = _unwrap_ddg_link(url)
        if not title or not resolved.startswith(("http://", "https://")):
            continue
        results.append((title, resolved, snippet))
        if len(results) >= limit:
            break
    return results
