"""Internet-access tools (web_search / fetch_page) for the ?/?? assistant.

Everything fetched from the web is wrapped with _untrusted() before it
re-enters the model context: page content is attacker-controlled data, never
instructions (same defense-in-depth convention as chat-user text in tools.py).
The WebToolContext mirrors ArtifactRequestContext -- per-invocation state that
bounds how many internet calls a single request may trigger.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from selara.infrastructure.http.web_search import WebSearchClient, WebSearchError
from selara.infrastructure.llm.tools import ToolCall, ToolResult, _err, _ok, _untrusted, register_tool

WEB_TOOL_NAMES: frozenset[str] = frozenset({"web_search", "fetch_page"})

_MAX_QUERY_LENGTH = 400
_MAX_RESULTS_LIMIT = 10
_MAX_SNIPPET_LENGTH = 300


def _schema(description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"description": description, "parameters": {"type": "object", "properties": properties,
        "required": required or [], "additionalProperties": False}}


@dataclass
class WebToolContext:
    client: WebSearchClient | None = None
    max_calls: int = 4
    max_results: int = 5
    max_page_chars: int = 8000
    calls_used: int = 0

    def try_acquire(self) -> bool:
        if self.client is None or self.calls_used >= self.max_calls:
            return False
        self.calls_used += 1
        return True

    def _limit_message(self) -> str:
        return (f"Лимит обращений к интернету в этом запросе исчерпан ({self.max_calls}). "
                "Используй уже полученные результаты и ответь без новых запросов.")


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@register_tool(
    "web_search",
    _schema(
        "Поиск в интернете: список ссылок с заголовками и сниппетами по запросу. "
        "Используй, когда нужны актуальные или внешние данные, которых нет в чате и документации. "
        "Сниппеты короткие: для подробностей открой страницу через fetch_page.",
        {
            "query": {"type": "string", "maxLength": _MAX_QUERY_LENGTH,
                      "description": "Поисковый запрос"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": _MAX_RESULTS_LIMIT,
                            "description": "Сколько результатов вернуть (по умолчанию 5)"},
        },
        ["query"],
    ),
    "Ищу в интернете: {query}...",
)
async def _exec_web_search(call: ToolCall, *, web_context: WebToolContext | None = None, **_: Any) -> ToolResult:
    if web_context is None or web_context.client is None:
        return _err(call.call_id, call.name, "Веб-поиск отключён на этом сервере.")
    query = str(call.arguments.get("query", "")).strip()
    if not query:
        return _err(call.call_id, call.name, "Укажи непустой поисковый запрос.")
    if len(query) > _MAX_QUERY_LENGTH:
        query = query[:_MAX_QUERY_LENGTH]
    if not web_context.try_acquire():
        return _err(call.call_id, call.name, web_context._limit_message())
    max_results = _clamp_int(call.arguments.get("max_results"), web_context.max_results, 1, _MAX_RESULTS_LIMIT)
    try:
        results = await web_context.client.search(query, max_results=max_results)
    except WebSearchError as exc:
        return _err(call.call_id, call.name, exc.message)
    payload = {
        "query": query,
        "results": [
            {
                "title": _untrusted(item.title[:_MAX_SNIPPET_LENGTH]),
                "url": item.url,
                "snippet": _untrusted(item.snippet[:_MAX_SNIPPET_LENGTH]),
            }
            for item in results
        ],
    }
    return _ok(call.call_id, call.name, payload, f"Поиск в интернете: {query[:60]}")


@register_tool(
    "fetch_page",
    _schema(
        "Прочитать текст веб-страницы по ссылке (обычно из результатов web_search). "
        "Только публичные http(s)-адреса; внутренние сети, localhost и нестандартные порты недоступны; "
        "очень большие страницы обрезаются.",
        {"url": {"type": "string", "maxLength": 2048, "description": "Полный URL страницы"}},
        ["url"],
    ),
    "Читаю страницу...",
)
async def _exec_fetch_page(call: ToolCall, *, web_context: WebToolContext | None = None, **_: Any) -> ToolResult:
    if web_context is None or web_context.client is None:
        return _err(call.call_id, call.name, "Доступ к интернету отключён на этом сервере.")
    url = str(call.arguments.get("url", "")).strip()
    if not url:
        return _err(call.call_id, call.name, "Укажи полный URL страницы.")
    if not web_context.try_acquire():
        return _err(call.call_id, call.name, web_context._limit_message())
    try:
        page = await web_context.client.fetch_page(url, max_chars=web_context.max_page_chars)
    except WebSearchError as exc:
        return _err(call.call_id, call.name, exc.message)
    payload = {
        "url": page.url,
        "final_url": page.final_url,
        "title": _untrusted(page.title),
        "text": _untrusted(page.text),
        "truncated": page.truncated,
        "content_type": page.content_type,
    }
    return _ok(call.call_id, call.name, payload, f"Прочитана страница: {page.final_url[:80]}")
