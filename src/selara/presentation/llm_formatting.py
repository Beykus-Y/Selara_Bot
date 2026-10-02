"""Convert model Markdown to escaped Telegram HTML and balanced chunks."""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

import mistune

_MARKDOWN = mistune.create_markdown(renderer="ast", plugins=["strikethrough", "table"])


def _render(tokens: list[dict]) -> str:
    output = []
    for token in tokens:
        kind = token["type"]
        children = token.get("children", [])
        if kind in {"text", "inline_html", "block_html"}:
            output.append(escape(token.get("raw", "")))
        elif kind in {"softbreak", "linebreak"}:
            output.append("\n")
        elif kind == "blank_line":
            continue
        elif kind in {"strong", "emphasis", "strikethrough"}:
            tag = {"strong": "b", "emphasis": "i", "strikethrough": "s"}[kind]
            output.append(f"<{tag}>{_render(children)}</{tag}>")
        elif kind == "codespan":
            output.append(f"<code>{escape(token['raw'])}</code>")
        elif kind == "block_code":
            output.append(f"<pre>{escape(token['raw'].rstrip(chr(10)))}</pre>\n\n")
        elif kind == "link":
            url = token.get("attrs", {}).get("url", "")
            label = _render(children)
            try:
                allowed = len(url) <= 2048 and urlsplit(url).scheme.lower() in {"http", "https", "tg"}
            except ValueError:
                allowed = False
            if allowed:
                output.append(f'<a href="{escape(url, quote=True)}">{label}</a>')
            else:
                output.append(label)
        elif kind == "image":
            output.append(_render(children))
        elif kind == "heading":
            output.append(_render(children) + "\n\n")
        elif kind == "block_quote":
            # Flatten nested quotes; Telegram does not allow nested blockquotes.
            output.append(_render(children).rstrip() + "\n\n")
        elif kind == "list":
            for child in children:
                output.append(_render(child.get("children", [])).rstrip() + "\n\n")
        elif kind in {"paragraph", "block_text"}:
            output.append(_render(children) + "\n\n")
        elif kind == "thematic_break":
            continue
        elif kind in {"table_head", "table_row"}:
            output.append(", ".join(_render(cell.get("children", [])) for cell in children) + "\n\n")
        else:
            output.append(_render(children) if children else escape(token.get("raw", "")))
    return "".join(output)


class _Chunker(HTMLParser):
    def __init__(self, max_units: int):
        super().__init__(convert_charrefs=True)
        self.max_units = max_units
        self.units = 0
        self.stack: list[tuple[str, str]] = []
        self.parts: list[str] = []
        self.chunks: list[str] = []
        self.plain_parts: list[str] = []

    def _flush(self) -> None:
        if self.units:
            self.chunks.append("".join(self.parts) + "".join(f"</{tag}>" for tag, _ in reversed(self.stack)))
        self.parts = [opening for _, opening in self.stack]
        self.units = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        opening = "<" + tag + "".join(f' {key}="{escape(value or "", quote=True)}"' for key, value in attrs) + ">"
        self.stack.append((tag, opening))
        self.parts.append(opening)

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.plain_parts.append(data)
        # UTF-16 units are conservative for Telegram's limit, including emoji.
        for char in data:
            units = 2 if ord(char) > 0xFFFF else 1
            if self.units + units > self.max_units:
                self._flush()
            self.parts.append(escape(char))
            self.units += units


def render_llm_html(text: str, *, max_units: int = 3500) -> list[str]:
    if max_units < 2:
        raise ValueError("max_units must be at least 2")
    rendered = _render(_MARKDOWN(text)).strip()
    parser = _Chunker(max_units)
    parser.feed(rendered)
    parser._flush()
    return parser.chunks or ["Ассистент не дал ответа."]


def html_to_plain_text(text: str) -> str:
    parser = _Chunker(3500)
    parser.feed(text)
    return "".join(parser.plain_parts)
