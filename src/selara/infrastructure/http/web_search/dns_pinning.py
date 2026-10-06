"""Pinned-DNS transport for WebSearchClient.fetch_page (production only).

Closes the TOCTOU / DNS-rebinding gap in the SSRF guard: the caller resolves
the hostname once, validates every resolved IP, and then pins the connection
to that exact IP via this transport. The OS resolver is never consulted again
between validation and connect, so a hostile DNS answer that flips between the
check and the connection is impossible. The URL keeps the original hostname,
so TLS SNI, certificate hostname verification and the HTTP Host header all
stay correct -- only the TCP connect target is substituted in the network
backend.

Adapted to the installed httpcore 1.0.x internals (verified against
httpcore 1.0.9 / httpx 0.28.1):
- there is no public `httpcore.backends.asyncio` module in this version; the
  asyncio-compatible backend is `AnyIOBackend` in `httpcore._backends.anyio`
  (what httpcore's own AutoBackend instantiates for asyncio), so we subclass
  that instead of `AsyncIOBackend`;
- httpx 0.28 builds `httpcore.Request` from URL parts (`raw_scheme` /
  `raw_host` / `port` / `raw_path`) instead of the deprecated `URL.raw`.

Note: this module is production-only. Unit tests inject httpx.MockTransport
through the client's `transport=` seam and never exercise pinning.
"""
from __future__ import annotations

import contextlib
import logging
import ssl
import typing

import httpcore
import httpx

from selara.infrastructure.http.web_search.models import WebSearchError

try:  # private httpcore module: guarded so an upstream rename degrades
    from httpcore._backends.anyio import AnyIOBackend
except ImportError:  # pragma: no cover - depends on installed httpcore
    AnyIOBackend = None  # type: ignore[assignment, misc]

log = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from httpcore._backends.base import AsyncNetworkStream

__all__ = ["PinnedDnsTransport", "pinning_available"]


def pinning_available() -> bool:
    return AnyIOBackend is not None

_HTTPCORE_EXC_MAP: dict[type[Exception], type[httpx.HTTPError]] = {
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.WriteTimeout: httpx.WriteTimeout,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.WriteError: httpx.WriteError,
    httpcore.ProxyError: httpx.ProxyError,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
    httpcore.ProtocolError: httpx.ProtocolError,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
}


@contextlib.contextmanager
def _map_httpcore_exceptions() -> typing.Iterator[None]:
    """Same mapping as httpx._transports.default.map_httpcore_exceptions.

    Picks the most specific mapping so e.g. httpcore.ReadTimeout maps to
    httpx.ReadTimeout, not just httpx.TimeoutException.
    """
    try:
        yield
    except Exception as exc:
        mapped_exc: type[httpx.HTTPError] | None = None
        for from_exc, to_exc in _HTTPCORE_EXC_MAP.items():
            if not isinstance(exc, from_exc):
                continue
            if mapped_exc is None or issubclass(to_exc, mapped_exc):
                mapped_exc = to_exc
        if mapped_exc is None:  # pragma: no cover
            raise
        raise mapped_exc(str(exc)) from exc


def _normalize_host(host: str) -> str:
    return host.lower().strip("[]")


class _PinnedBackend(AnyIOBackend if typing.TYPE_CHECKING else object):
    """Network backend that substitutes validated IPs for pinned hosts.

    connect_tcp signature matches httpcore 1.0.x
    (`AsyncNetworkBackend.connect_tcp`); unknown hosts pass through unchanged
    so behavior degrades to stock httpcore.
    """

    def __init__(self, pins: dict[str, str]) -> None:
        if AnyIOBackend is not None:  # pragma: no branch - guarded by pinning_available()
            super().__init__()
        self._pins = pins

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        target = self._pins.get(_normalize_host(host), host)
        return await AnyIOBackend.connect_tcp(
            self,
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _AsyncResponseStream(httpx.AsyncByteStream):
    """Wraps the httpcore async byte stream, mapping transport errors."""

    def __init__(self, httpcore_stream: typing.AsyncIterable[bytes]) -> None:
        self._httpcore_stream = httpcore_stream

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        with _map_httpcore_exceptions():
            async for part in self._httpcore_stream:
                yield part

    async def aclose(self) -> None:
        if hasattr(self._httpcore_stream, "aclose"):
            await self._httpcore_stream.aclose()


class PinnedDnsTransport(httpx.AsyncBaseTransport):
    """httpx transport that connects only to caller-pinned IPs.

    The pin table lives for the lifetime of the transport; WebSearchClient
    creates a fresh transport per fetch_page call, so pins cannot go stale.
    """

    def __init__(self, *, resolve_timeout: float) -> None:
        if AnyIOBackend is None:
            # Fail closed: without a pinnable backend we refuse to fetch rather
            # than silently connecting without the validated-IP guarantee.
            log.error("httpcore AnyIO backend unavailable - fetch_page pinning disabled")
            raise WebSearchError(
                "Чтение страниц временно недоступно на этом сервере (не удалось инициализировать сетевой транспорт)."
            )
        self._resolve_timeout = resolve_timeout
        self._pins: dict[str, str] = {}
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=_PinnedBackend(self._pins),
            retries=0,
            ssl_context=ssl.create_default_context(),
        )

    def pin(self, host: str, ip: str) -> None:
        self._pins[_normalize_host(host)] = ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        extensions = dict(request.extensions)
        extensions.setdefault(
            "timeout",
            {
                "connect": self._resolve_timeout,
                "read": self._resolve_timeout,
                "write": self._resolve_timeout,
                "pool": self._resolve_timeout,
            },
        )
        httpcore_request = httpcore.Request(
            method=request.method.encode("ascii"),
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=extensions,
        )
        with _map_httpcore_exceptions():
            httpcore_response = await self._pool.handle_async_request(httpcore_request)
        assert isinstance(httpcore_response.stream, typing.AsyncIterable)
        return httpx.Response(
            status_code=httpcore_response.status,
            headers=httpcore_response.headers,
            stream=_AsyncResponseStream(httpcore_response.stream),
            extensions=httpcore_response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
