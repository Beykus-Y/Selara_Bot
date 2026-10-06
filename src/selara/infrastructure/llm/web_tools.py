"""Internet-access tools (web_search / fetch_page / web_research) for the ?/?? assistant.

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

WEB_TOOL_NAMES: frozenset[str] = frozenset({"web_search", "fetch_page", "web_research"})

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

    def reserve(self, slots: int) -> int:
        """Atomically reserve up to `slots` budget slots; returns how many
        were granted. Compound tools must reserve every HTTP request they
        intend to make up-front -- otherwise several same-batch calls could
        jointly exceed max_calls (review 5432070938)."""
        granted = max(0, min(slots, self.max_calls - self.calls_used))
        self.calls_used += granted
        return granted

    @property
    def exhausted(self) -> bool:
        return self.client is None or self.calls_used >= self.max_calls

    def _limit_message(self) -> str:
        return (f"Лимит обращений к интернету в этом запросе исчерпан ({self.max_calls}). "
                "Используй уже полученные результаты и ответь без новых запросов.")


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def restrict_tools_after_web(tools: list[dict], web_context: WebToolContext) -> list[dict]:
    """Deterministic research boundary for web results.

    Once a web_search/fetch_page result has entered the model context, the
    invocation must no longer combine that untrusted content with ANY tool:
    a poisoned page could steer mutating actions (confused deputy -- execute_tool's
    auth re-checks stop privilege escalation, not unintended-but-authorized
    actions) or chain read tools (history, members, audit log) into another
    outbound web request to exfiltrate private context. So the answer: the
    tool list is withdrawn entirely and the model finishes with a plain text
    answer from what it already has. Parallel web calls made in the same
    round as the first one still execute; everything after does not.

    The unconditional withdraw also covers the exhausted-budget case, so the
    model never burns LLM rounds on guaranteed 'limit exhausted' errors.
    """
    del tools, web_context  # the boundary is total; arguments kept for call-site stability
    return []


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
    # Server-side cap: the model-provided max_results (or its absence) is always
    # clamped into [1, min(_MAX_RESULTS_LIMIT, configured max_results)], so a bad
    # or missing config value can never widen what reaches the provider.
    server_max = max(1, min(_MAX_RESULTS_LIMIT, web_context.max_results))
    max_results = _clamp_int(call.arguments.get("max_results"), server_max, 1, server_max)
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


@register_tool(
    "web_research",
    _schema(
        "Комплексное исследование: сам выполняет поиск в интернете и открывает верхние результаты, "
        "возвращая в одном ответе и сниппеты, и тексты страниц. Используй его, когда нужны подробности, "
        "а не только ссылки. Помни: после любого веб-инструмента остаток запроса выполняется без инструментов.",
        {
            "query": {"type": "string", "maxLength": _MAX_QUERY_LENGTH,
                      "description": "Поисковый запрос"},
            "open_top": {"type": "integer", "minimum": 1, "maximum": 3,
                         "description": "Сколько верхних результатов открыть полным текстом (по умолчанию 2)"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": _MAX_RESULTS_LIMIT,
                            "description": "Сколько результатов поиска вернуть (по умолчанию 5)"},
        },
        ["query"],
    ),
    "Исследую веб: {query}...",
)
async def _exec_web_research(call: ToolCall, *, web_context: WebToolContext | None = None, **_: Any) -> ToolResult:
    if web_context is None or web_context.client is None:
        return _err(call.call_id, call.name, "Доступ к интернету отключён на этом сервере.")
    query = str(call.arguments.get("query", "")).strip()
    if not query:
        return _err(call.call_id, call.name, "Укажи непустой поисковый запрос.")
    if len(query) > _MAX_QUERY_LENGTH:
        query = query[:_MAX_QUERY_LENGTH]
    server_max = max(1, min(_MAX_RESULTS_LIMIT, web_context.max_results))
    max_results = _clamp_int(call.arguments.get("max_results"), server_max, 1, server_max)
    open_top = _clamp_int(call.arguments.get("open_top"), 2, 1, 3)
    # Budget semantics (review 5432070938): the search slot AND every page
    # fetch are reserved ATOMICALLY before any HTTP happens, so several
    # web_research calls in one same-round batch cannot jointly exceed the
    # invocation budget (e.g. 4x web_research at max_calls=4 -> at most
    # 4 HTTP requests in total, not 10).
    granted = web_context.reserve(1 + open_top)
    if granted == 0:
        return _err(call.call_id, call.name, web_context._limit_message())
    pages_to_open = granted - 1
    try:
        results = await web_context.client.search(query, max_results=max_results)
    except WebSearchError as exc:
        return _err(call.call_id, call.name, exc.message)
    results_payload = [
        {
            "title": _untrusted(item.title[:_MAX_SNIPPET_LENGTH]),
            "url": item.url,
            "snippet": _untrusted(item.snippet[:_MAX_SNIPPET_LENGTH]),
        }
        for item in results
    ]
    pages: list[dict] = []
    for item in results[:pages_to_open]:
        try:
            page = await web_context.client.fetch_page(item.url, max_chars=web_context.max_page_chars)
        except WebSearchError as exc:
            # One bad page must not fail the whole research call.
            pages.append({"url": item.url, "error": exc.message})
            continue
        pages.append({
            "url": page.final_url,
            "title": _untrusted(page.title),
            "text": _untrusted(page.text),
            "truncated": page.truncated,
        })
    payload = {"query": query, "pages_opened": len(pages), "results": results_payload, "pages": pages}
    return _ok(call.call_id, call.name, payload, f"Веб-исследование: {query[:60]}")
