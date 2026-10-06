"""Search provider abstraction: a provider turns a query into result items.

duckduckgo is the built-in implementation (unofficial lite HTML endpoint, no
API key). Key-based providers (e.g. Tavily) can be added here later; the
WEB_SEARCH_* config already reserves api_key/base_url for them.
"""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from selara.infrastructure.http.web_search.htmlutil import parse_lite_results
from selara.infrastructure.http.web_search.models import SearchResultItem, WebSearchError

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; SelaraBot/1.0; +https://github.com/Beykus-Y/Selara_Bot)"

DEFAULT_DUCKDUCKGO_BASE_URL = "https://lite.duckduckgo.com"


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

    async def search(self, query: str, *, max_results: int) -> list[SearchResultItem]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                transport=self._transport,
                trust_env=False,  # HTTP(S)_PROXY env vars must not bypass our network boundary
            ) as client:
                response = await client.post("/lite/", data={"q": query})
        except httpx.TimeoutException as exc:
            raise WebSearchError("Поиск не ответил вовремя. Попробуй позже или переформулируй запрос.",
                                 is_timeout=True) from exc
        except httpx.HTTPError as exc:
            log.warning("duckduckgo search transport failure: %s", exc)
            raise WebSearchError("Поисковый сервис недоступен. Попробуй позже.") from exc

        if response.status_code in (403, 429):
            # Immediate retries do not help against a challenge page.
            log.warning("duckduckgo search rejected the request: status=%s", response.status_code)
            raise WebSearchError("Поисковый сервис временно отклоняет запросы с этого сервера. Попробуй позже.")
        if response.status_code != 200:
            raise WebSearchError(f"Поисковый сервис вернул ошибку (HTTP {response.status_code}). Попробуй позже.")

        return [
            SearchResultItem(title=title, url=url, snippet=snippet)
            for title, url, snippet in parse_lite_results(response.text, limit=max_results)
        ]
