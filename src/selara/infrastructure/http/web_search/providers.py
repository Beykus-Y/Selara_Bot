"""Search provider abstraction: a provider turns a query into result items.

duckduckgo is the built-in implementation (unofficial lite HTML endpoint, no
API key; WEB_SEARCH_BASE_URL points it at a gateway). Tavily/Brave use
WEB_SEARCH_API_KEY and always talk to their own fixed hosts, SearXNG uses
WEB_SEARCH_SEARXNG_URL: a credential is never sent to another provider's URL.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

import httpx

from selara.infrastructure.http.web_search.htmlutil import parse_lite_results
from selara.infrastructure.http.web_search.models import SearchResultItem, WebSearchError

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; SelaraBot/1.0; +https://github.com/Beykus-Y/Selara_Bot)"

DEFAULT_DUCKDUCKGO_BASE_URL = "https://lite.duckduckgo.com"
DEFAULT_TAVILY_BASE_URL = "https://api.tavily.com"
DEFAULT_BRAVE_BASE_URL = "https://api.search.brave.com"
DEFAULT_SEARXNG_URL = "http://searxng:8080"  # compose service name, internal network only

# One delayed retry for 5xx. 202/403/429 from DuckDuckGo are anti-bot blocks and
# are not retried (immediate retries only worsen the bot score).
_RETRY_DELAY_SECONDS = 1.5
_RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})
_BODY_LOG_CHARS = 200


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int) -> list[SearchResultItem]:
        ...


class DuckDuckGoProvider:
    """Unofficial scrape of the DuckDuckGo lite endpoint.

    DuckDuckGo challenges datacenter IPs with 403/captcha and is blocked in
    some regions (notably Russia) -- both surface as WebSearchError and are
    reported to the model as "search temporarily unavailable", not as a crash.
    """

    name = "duckduckgo"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_DUCKDUCKGO_BASE_URL,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_DUCKDUCKGO_BASE_URL).rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def _post(self, query: str) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                transport=self._transport,
                trust_env=False,  # HTTP(S)_PROXY env vars must not bypass our network boundary
            ) as client:
                return await client.post("/lite/", data={"q": query})
        except httpx.TimeoutException as exc:
            raise WebSearchError("Поиск не ответил вовремя. Попробуй позже или переформулируй запрос.",
                                 is_timeout=True) from exc
        except httpx.HTTPError as exc:
            log.warning("duckduckgo search transport failure: %s", exc)
            raise WebSearchError("Поисковый сервис недоступен. Попробуй позже.") from exc

    async def search(self, query: str, *, max_results: int) -> list[SearchResultItem]:
        response = await self._post(query)
        if response.status_code in _RETRYABLE_STATUSES:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            response = await self._post(query)

        if response.status_code == 202:
            # DuckDuckGo answers 202 with an anti-bot challenge page ("anomaly")
            # to datacenter IPs; it is a block, not a transient server error.
            log.warning("duckduckgo search got a bot challenge: status=202 body=%r",
                        response.text[:_BODY_LOG_CHARS])
            raise WebSearchError("Поисковый сервис временно отклоняет запросы с этого сервера. Попробуй позже.")
        if response.status_code in (403, 429):
            # Immediate retries do not help against a challenge page.
            log.warning("duckduckgo search rejected the request: status=%s body=%r",
                        response.status_code, response.text[:_BODY_LOG_CHARS])
            raise WebSearchError("Поисковый сервис временно отклоняет запросы с этого сервера. Попробуй позже.")
        if response.status_code != 200:
            log.warning("duckduckgo search failed: status=%s body=%r",
                        response.status_code, response.text[:_BODY_LOG_CHARS])
            raise WebSearchError(f"Поисковый сервис вернул ошибку (HTTP {response.status_code}). Попробуй позже.")

        return [
            SearchResultItem(title=title, url=url, snippet=snippet)
            for title, url, snippet in parse_lite_results(response.text, limit=max_results)
        ]


class _KeyedJsonProvider:
    """Shared plumbing for API-key JSON providers (Tavily, Brave)."""

    name = "keyed"
    _label = "Поисковый сервис"

    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def _request(self, query: str, max_results: int) -> httpx.Response:
        raise NotImplementedError

    def _parse(self, payload: dict) -> list[SearchResultItem]:
        raise NotImplementedError

    async def search(self, query: str, *, max_results: int) -> list[SearchResultItem]:
        try:
            response = await self._request(query, max_results)
            if response.status_code in _RETRYABLE_STATUSES:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
                response = await self._request(query, max_results)
        except httpx.TimeoutException as exc:
            raise WebSearchError("Поиск не ответил вовремя. Попробуй позже или переформулируй запрос.",
                                 is_timeout=True) from exc
        except httpx.HTTPError as exc:
            log.warning("%s search transport failure: %s", self.name, exc)
            raise WebSearchError("Поисковый сервис недоступен. Попробуй позже.") from exc
        if response.status_code != 200:
            log.warning("%s search failed: status=%s body=%r", self.name, response.status_code,
                        response.text[:_BODY_LOG_CHARS])
            raise WebSearchError(f"Поисковый сервис вернул ошибку (HTTP {response.status_code}). Попробуй позже.")
        try:
            payload = response.json()
        except ValueError as exc:
            log.warning("%s search returned non-JSON body: %r", self.name, response.text[:_BODY_LOG_CHARS])
            raise WebSearchError("Поисковый сервис вернул некорректный ответ. Попробуй позже.") from exc
        if not isinstance(payload, dict):
            raise WebSearchError("Поисковый сервис вернул некорректный ответ. Попробуй позже.")
        return self._parse(payload)[:max_results]

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            transport=self._transport,
            trust_env=False,
        )


def _items(rows: object, *, snippet_key: str) -> list[SearchResultItem]:
    result: list[SearchResultItem] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        title = str(row.get("title") or "")
        if title and url.startswith(("http://", "https://")):
            result.append(SearchResultItem(title=title, url=url, snippet=str(row.get(snippet_key) or "")))
    return result


class TavilyProvider(_KeyedJsonProvider):
    name = "tavily"

    async def _request(self, query: str, max_results: int) -> httpx.Response:
        async with self._client() as client:
            return await client.post(
                "/search",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"query": query, "max_results": max_results, "topic": "general"},
            )

    def _parse(self, payload: dict) -> list[SearchResultItem]:
        return _items(payload.get("results"), snippet_key="content")


class BraveProvider(_KeyedJsonProvider):
    name = "brave"

    async def _request(self, query: str, max_results: int) -> httpx.Response:
        async with self._client() as client:
            return await client.get(
                "/res/v1/web/search",
                headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
                params={"q": query, "count": max_results},
            )

    def _parse(self, payload: dict) -> list[SearchResultItem]:
        web = payload.get("web")
        return _items(web.get("results") if isinstance(web, dict) else None, snippet_key="description")


class SearxngProvider(_KeyedJsonProvider):
    """Self-hosted SearXNG metasearch (JSON format must be enabled in its settings).

    Unreachable/absent instances raise WebSearchError so FallbackProvider moves on.
    """

    name = "searxng"

    def __init__(self, *, base_url: str = DEFAULT_SEARXNG_URL, timeout_seconds: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(api_key="", base_url=base_url or DEFAULT_SEARXNG_URL,
                         timeout_seconds=timeout_seconds, transport=transport)

    async def _request(self, query: str, max_results: int) -> httpx.Response:
        async with self._client() as client:
            return await client.get("/search", params={"q": query, "format": "json"},
                                    headers={"Accept": "application/json"})

    def _parse(self, payload: dict) -> list[SearchResultItem]:
        return _items(payload.get("results"), snippet_key="content")


class FallbackProvider:
    """Try providers in order; the first one that answers wins.

    A provider that failed is skipped for ``cooldown_seconds`` so a missing or
    down primary (e.g. no SearXNG container) does not add latency to every query.
    If every provider is cooling down, all are tried anyway.
    """

    def __init__(self, providers: list[SearchProvider], *, cooldown_seconds: float = 60.0) -> None:
        self._providers = providers
        self._cooldown_seconds = cooldown_seconds
        self._down_until: dict[int, float] = {}
        self.name = "+".join(p.name for p in providers)

    async def search(self, query: str, *, max_results: int) -> list[SearchResultItem]:
        now = time.monotonic()
        candidates = [p for p in self._providers if self._down_until.get(id(p), 0.0) <= now]
        if not candidates:
            candidates = list(self._providers)
        last_error: WebSearchError | None = None
        for provider in candidates:
            try:
                result = await provider.search(query, max_results=max_results)
            except WebSearchError as exc:
                log.warning("web search provider %s failed, trying next: %s", provider.name, exc.message)
                self._down_until[id(provider)] = time.monotonic() + self._cooldown_seconds
                last_error = exc
                continue
            self._down_until.pop(id(provider), None)
            return result
        if last_error is None:
            raise WebSearchError("Поисковый сервис не настроен.")
        raise last_error
