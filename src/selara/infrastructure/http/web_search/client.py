"""WebSearchClient: provider-backed search plus an SSRF-guarded page reader.

fetch_page is reachable with model-chosen URLs, so it validates every hop
including redirects: only public http(s) URLs on standard ports, no
credentials, and every resolved IP must be global (no loopback/private/
link-local/reserved targets). This is a deliberate boundary -- the model's
URL choice is untrusted input, exactly like the page content itself.

The DNS-rebinding TOCTOU window is closed by pinning: each hop's hostname is
resolved, every address is validated, and the connection is then pinned to
that IP via PinnedDnsTransport while the URL keeps the original hostname
(TLS SNI / Host header stay correct).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging

import httpx

from selara.infrastructure.http.web_search.dns_pinning import PinnedDnsTransport
from selara.infrastructure.http.web_search.htmlutil import extract_text, extract_title
from selara.infrastructure.http.web_search.models import PageContent, SearchResultItem, WebSearchError
from selara.infrastructure.http.web_search.providers import (
    DEFAULT_BRAVE_BASE_URL,
    DEFAULT_DUCKDUCKGO_BASE_URL,
    DEFAULT_SEARXNG_URL,
    DEFAULT_TAVILY_BASE_URL,
    USER_AGENT,
    BraveProvider,
    DuckDuckGoProvider,
    FallbackProvider,
    SearchProvider,
    SearxngProvider,
    TavilyProvider,
)

log = logging.getLogger(__name__)

_MAX_REDIRECTS = 3
_MAX_TITLE_CHARS = 256
_DEFAULT_MAX_PAGE_BYTES = 1_000_000
# Accepted textual types. XHTML is accepted but NOT passed through raw: it has
# real markup (e.g. <script>) and goes through extract_text like HTML.
_TEXTUAL_CONTENT_TYPES = {"application/json", "application/xml", "application/xhtml+xml", "text/xml"}
_RAW_PASSTHROUGH_CONTENT_TYPES = {"application/json", "application/xml", "text/xml"}
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
    try:
        url = httpx.URL(raw)
    except httpx.InvalidURL as exc:
        raise WebSearchError("Некорректная ссылка: не удалось разобрать адрес.") from exc
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
        return url  # hostname: resolved, validated and pinned per request in _resolve_pinned_host
    _ensure_global_ip(ip, host)
    return url


async def _resolve_addresses(host: str) -> set[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return {info[4][0] for info in infos}


async def _resolve_pinned_host(host: str, *, resolve_timeout: float) -> str:
    """Resolve a hostname and return the IP the connection must be pinned to.

    Literal IPs are validated and returned as-is; hostnames are resolved via
    _resolve_addresses bounded by resolve_timeout, every resolved address must
    be global, and the first IPv4 address is preferred (IPv6-capable hosts
    still connect: without an IPv4 the first address is used). The caller
    passes the returned IP to PinnedDnsTransport.pin so the actual TCP connect
    cannot re-resolve DNS (anti-rebinding).
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _ensure_global_ip(ip, host)
        return str(ip)
    try:
        addresses = await asyncio.wait_for(_resolve_addresses(host), timeout=resolve_timeout)
    except asyncio.TimeoutError as exc:
        raise WebSearchError("Не удалось определить адрес сайта: таймаут DNS.", is_timeout=True) from exc
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
    ipv4 = sorted(address for address in addresses if ":" not in address)
    if ipv4:
        return ipv4[0]
    return sorted(addresses)[0]


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
        # Test-only seam: an injected transport (httpx.MockTransport in unit
        # tests) replaces the production pinned-DNS transport entirely, so
        # pinning is never exercised under the seam. Production always gets a
        # fresh PinnedDnsTransport per call (pins cannot go stale, and the
        # transport is closed with the client via aclose()).
        transport = (
            self._transport
            if self._transport is not None
            else PinnedDnsTransport(resolve_timeout=self._timeout_seconds)
        )
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,  # redirects are followed manually so every hop is validated
            transport=transport,
            trust_env=False,  # explicit: proxies from env would resolve DNS outside our guard
        ) as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                # Resolve + validate first, then pin: the request URL keeps
                # the original hostname, only the TCP connect target is
                # substituted, so TLS SNI and the Host header stay correct.
                pinned_ip = await _resolve_pinned_host(current.host, resolve_timeout=self._timeout_seconds)
                if hasattr(transport, "pin"):
                    # Pin by the exact host representation httpcore passes to
                    # the network backend: httpcore.URL is built from
                    # url.raw_host, which is PUNYCODE ascii for IDN
                    # ("пример.рф" -> "xn--e1afmkfd.xn--p1ai") and lowercase
                    # ascii otherwise. Resolving stays on the unicode form
                    # (socket.getaddrinfo handles IDN). The backend is
                    # fail-closed: an unpinned host aborts the fetch.
                    pin_key = current.raw_host.decode("ascii").lower()
                    transport.pin(pin_key, pinned_ip)
                try:
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
                except httpx.TimeoutException as exc:
                    raise WebSearchError(
                        "Не удалось загрузить страницу: превышено время ожидания.", is_timeout=True
                    ) from exc
                except httpx.LocalProtocolError as exc:
                    # The pinned backend raises this on a pin-table miss: an
                    # invariant violation, not "site unavailable". Must precede
                    # the generic HTTPError handler (it is a subclass).
                    raise WebSearchError(f"Внутренняя ошибка пиннинга DNS: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise WebSearchError("Не удалось загрузить страницу: сайт недоступен.") from exc
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
        if content_type in _RAW_PASSTHROUGH_CONTENT_TYPES:
            # JSON/XML have no markup to strip; HTMLParser would only mangle them.
            # XHTML is NOT here: it has real markup (e.g. <script>) and goes
            # through extract_text like HTML.
            text = html
        else:
            text = extract_text(html)
        truncated = body_truncated or len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return PageContent(
            url=requested_url,
            final_url=str(response.url),
            title=extract_title(html)[:_MAX_TITLE_CHARS],  # unbounded titles are a context-window DoS
            text=text,
            truncated=truncated,
            content_type=content_type,
        )


def build_web_search_client(
    *,
    enabled: bool,
    provider: str,
    base_url: str = "",
    api_key: str = "",
    searxng_url: str = "",
    timeout_seconds: float = 15.0,
) -> WebSearchClient | None:
    """Composition-root factory: None means web tools stay disabled.

    - auto (default): self-hosted SearXNG first, DuckDuckGo as fallback. Without a
      reachable SearXNG the bot just uses DuckDuckGo (no crash, no extra latency
      after the first failure).
    - searxng: same chain, explicit.
    - tavily/brave: need WEB_SEARCH_API_KEY; fall back to DuckDuckGo.
    - duckduckgo: DuckDuckGo only.
    """
    if not enabled:
        return None
    normalized = (provider or "auto").strip().lower() or "auto"
    ddg = DuckDuckGoProvider(
        base_url=base_url if normalized == "duckduckgo" and base_url else DEFAULT_DUCKDUCKGO_BASE_URL,
        timeout_seconds=timeout_seconds,
    )
    search_provider: SearchProvider
    if normalized == "duckduckgo":
        search_provider = ddg
    elif normalized in ("auto", "searxng"):
        searxng = SearxngProvider(base_url=searxng_url or DEFAULT_SEARXNG_URL,
                                  timeout_seconds=min(timeout_seconds, 8.0))
        search_provider = FallbackProvider([searxng, ddg])
    elif normalized in ("tavily", "brave"):
        if not api_key.strip():
            log.warning("WEB_SEARCH: для провайдера %s нужен WEB_SEARCH_API_KEY — используется duckduckgo.", normalized)
            search_provider = ddg
        else:
            keyed_cls, default_url = (
                (TavilyProvider, DEFAULT_TAVILY_BASE_URL) if normalized == "tavily"
                else (BraveProvider, DEFAULT_BRAVE_BASE_URL)
            )
            keyed = keyed_cls(api_key=api_key.strip(), base_url=base_url or default_url,
                              timeout_seconds=timeout_seconds)
            search_provider = FallbackProvider([keyed, ddg])
    else:
        log.warning("WEB_SEARCH: неизвестный провайдер %r — поиск отключён.", provider)
        return None
    return WebSearchClient(provider=search_provider, timeout_seconds=timeout_seconds)
