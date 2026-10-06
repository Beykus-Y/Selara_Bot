"""WebSearchClient: provider-backed search plus an SSRF-guarded page reader.

fetch_page is reachable with model-chosen URLs, so it validates every hop
including redirects: only public http(s) URLs on standard ports, no
credentials, and every resolved IP must be global (no loopback/private/
link-local/reserved targets). This is a deliberate boundary -- the model's
URL choice is untrusted input, exactly like the page content itself.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging

import httpx

from selara.infrastructure.http.web_search.htmlutil import extract_text, extract_title
from selara.infrastructure.http.web_search.models import PageContent, SearchResultItem, WebSearchError
from selara.infrastructure.http.web_search.providers import (
    DEFAULT_DUCKDUCKGO_BASE_URL,
    USER_AGENT,
    DuckDuckGoProvider,
    SearchProvider,
)

log = logging.getLogger(__name__)

_MAX_REDIRECTS = 3
_DEFAULT_MAX_PAGE_BYTES = 1_000_000
_TEXTUAL_CONTENT_TYPES = {"application/json", "application/xml", "application/xhtml+xml", "text/xml"}
_ALLOWED_PORTS = {80, 443}


def _ensure_global_ip(ip: ipaddress._BaseAddress, host: str) -> None:
    if not ip.is_global:
        raise WebSearchError(
            f"Адрес {host} не является публичным: внутренние и локальные ресурсы недоступны."
        )


def _validate_public_http_url(value: str) -> httpx.URL:
    raw = (value or "").strip()
    if not raw:
        raise WebSearchError("Укажи непустую ссылку.")
    if len(raw) > 2048:
        raise WebSearchError("Ссылка слишком длинная.")
    url = httpx.URL(raw)
    if url.scheme not in ("http", "https"):
        raise WebSearchError("Поддерживаются только ссылки http:// и https://.")
    if url.userinfo:
        raise WebSearchError("Ссылки с логином или паролем не поддерживаются.")
    host = url.host
    if not host:
        raise WebSearchError("В ссылке нет адреса сайта.")
    if url.port is not None and url.port not in _ALLOWED_PORTS:
        raise WebSearchError("Разрешены только стандартные порты 80 и 443.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url  # hostname: resolved and checked per request in _ensure_public_host
    _ensure_global_ip(ip, host)
    return url


async def _resolve_addresses(host: str) -> set[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return {info[4][0] for info in infos}


async def _ensure_public_host(url: httpx.URL) -> None:
    """DNS-level guard for hostname URLs; literal IPs were checked already.
    TOCTOU DNS rebinding is out of scope: this defends against fetching
    internal addresses by name/IP, not against a hostile DNS server."""
    host = url.host or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _ensure_global_ip(ip, host)
        return
    try:
        addresses = await _resolve_addresses(host)
    except OSError as exc:
        raise WebSearchError(f"Не удалось определить адрес сайта {host}: возможно, опечатка в ссылке.") from exc
    if not addresses:
        raise WebSearchError(f"Не удалось определить адрес сайта {host}: возможно, опечатка в ссылке.")
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        _ensure_global_ip(ip, host)


class WebSearchClient:
    def __init__(
        self,
        *,
        provider: SearchProvider,
        timeout_seconds: float = 15.0,
        max_page_bytes: int = _DEFAULT_MAX_PAGE_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_page_bytes = max_page_bytes
        self._transport = transport

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", "unknown")

    async def search(self, query: str, *, max_results: int) -> list[SearchResultItem]:
        clean_query = query.strip()
        if not clean_query:
            raise WebSearchError("Пустой поисковый запрос.")
        return await self._provider.search(clean_query, max_results=max_results)

    async def fetch_page(self, url: str, *, max_chars: int) -> PageContent:
        current = _validate_public_http_url(url)
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,  # redirects are followed manually so every hop is validated
            transport=self._transport,
        ) as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                await _ensure_public_host(current)
                response = await client.send(client.build_request("GET", current), stream=True)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        next_url = _validate_public_http_url(str(response.url.join(location)))
                        await response.aclose()
                        current = next_url
                        continue
                    return await self._read_page(response, requested_url=url, max_chars=max_chars)
                finally:
                    await response.aclose()
        raise WebSearchError("Слишком много перенаправлений: ссылка не ведёт на страницу.")

    async def _read_page(self, response: httpx.Response, *, requested_url: str, max_chars: int) -> PageContent:
        if response.status_code != 200:
            raise WebSearchError(f"Сайт вернул ошибку (HTTP {response.status_code}).")
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not (content_type.startswith("text/") or content_type in _TEXTUAL_CONTENT_TYPES):
            raise WebSearchError(f"По ссылке не текстовая страница (content-type: {content_type or 'неизвестный'}).")
        chunks: list[bytes] = []
        total = 0
        body_truncated = False
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_page_bytes:
                body_truncated = True
                break
            chunks.append(chunk)
        html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        if content_type in _TEXTUAL_CONTENT_TYPES:
            # JSON/XML have no markup to strip; HTMLParser would only mangle them.
            text = html
        else:
            text = extract_text(html)
        truncated = body_truncated or len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return PageContent(
            url=requested_url,
            final_url=str(response.url),
            title=extract_title(html),
            text=text,
            truncated=truncated,
            content_type=content_type,
        )


def build_web_search_client(
    *,
    enabled: bool,
    provider: str,
    base_url: str = "",
    timeout_seconds: float = 15.0,
) -> WebSearchClient | None:
    """Composition-root factory: None means web tools stay disabled."""
    if not enabled:
        return None
    normalized = (provider or "").strip().lower()
    if normalized == "duckduckgo":
        search_provider: SearchProvider = DuckDuckGoProvider(
            base_url=base_url or DEFAULT_DUCKDUCKGO_BASE_URL,
            timeout_seconds=timeout_seconds,
        )
    else:
        log.warning("WEB_SEARCH: неизвестный провайдер %r — поиск отключён.", provider)
        return None
    return WebSearchClient(provider=search_provider, timeout_seconds=timeout_seconds)
