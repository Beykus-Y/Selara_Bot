"""Security-focused unit tests for WebSearchClient.fetch_page and the
pinned-DNS transport. Offline only: HTTP runs through httpx.MockTransport via
the client's transport seam (pinning is production-only and never exercised
through real sockets), DNS runs through monkeypatched _resolve_addresses.

Conventions follow tests/unit/test_llm_web_tools.py.
"""
import asyncio
import json

import httpx
import pytest

from selara.infrastructure.http.web_search import DuckDuckGoProvider, WebSearchClient, WebSearchError
from selara.infrastructure.http.web_search.client import _resolve_pinned_host
from selara.infrastructure.http.web_search.dns_pinning import PinnedDnsTransport
from selara.infrastructure.llm.tools import ToolCall, execute_tool
from selara.infrastructure.llm.web_tools import WebToolContext

PUBLIC_PAGE_URL = "http://1.2.3.4/page"  # literal public IP: no DNS in tests


def _page_transport(
    *,
    status: int = 200,
    html: str = "<html><body><p>ok</p></body></html>",
    content_type: str = "text/html",
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html, headers={"Content-Type": content_type})

    return httpx.MockTransport(handler)


def _client(transport) -> WebSearchClient:
    return WebSearchClient(provider=DuckDuckGoProvider(transport=transport), transport=transport)


# ------------------------------------------------------------------ fetch_page hardening


async def test_fetch_page_caps_title_length():
    long_title = "T" * 5000
    html = f"<html><head><title>{long_title}</title></head><body><p>body</p></body></html>"
    page = await _client(_page_transport(html=html)).fetch_page(PUBLIC_PAGE_URL, max_chars=100)
    assert len(page.title) == 256


async def test_fetch_page_strips_xhtml_markup():
    html = "<html><body><script>payload</script><p>visible</p></body></html>"
    transport = _page_transport(html=html, content_type="application/xhtml+xml")
    page = await _client(transport).fetch_page(PUBLIC_PAGE_URL, max_chars=500)
    assert "visible" in page.text
    assert "payload" not in page.text
    assert page.content_type == "application/xhtml+xml"


async def test_fetch_page_maps_invalid_url_to_web_search_error():
    client = WebSearchClient(provider=DuckDuckGoProvider(), transport=_page_transport())
    with pytest.raises(WebSearchError):
        await client.fetch_page("http://example.com:abc/", max_chars=100)


async def test_fetch_page_invalid_url_maps_to_tool_error():
    web_context = WebToolContext(client=_client(_page_transport()))
    call = ToolCall(name="fetch_page", arguments={"url": "http://example.com:abc/"}, call_id="fps1")
    result = await execute_tool(call, web_context=web_context)
    assert result.success is False
    assert "error" in json.loads(result.result_text)


async def test_fetch_page_maps_transport_timeout_to_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    client = WebSearchClient(provider=DuckDuckGoProvider(), transport=httpx.MockTransport(handler))
    with pytest.raises(WebSearchError) as excinfo:
        await client.fetch_page(PUBLIC_PAGE_URL, max_chars=100)
    assert excinfo.value.is_timeout is True


async def test_fetch_page_maps_transport_error_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = WebSearchClient(provider=DuckDuckGoProvider(), transport=httpx.MockTransport(handler))
    with pytest.raises(WebSearchError) as excinfo:
        await client.fetch_page(PUBLIC_PAGE_URL, max_chars=100)
    assert excinfo.value.is_timeout is False


# --------------------------------------------------------------- pinned host resolution


async def test_resolve_pinned_host_prefers_ipv4(monkeypatch):
    from selara.infrastructure.http.web_search import client as client_module

    async def fake_resolve(host: str) -> set[str]:
        return {"2606:4700::1111", "1.2.3.4"}

    monkeypatch.setattr(client_module, "_resolve_addresses", fake_resolve)
    assert await _resolve_pinned_host("example.com", resolve_timeout=5) == "1.2.3.4"


async def test_resolve_pinned_host_rejects_private_resolution(monkeypatch):
    from selara.infrastructure.http.web_search import client as client_module

    async def fake_resolve(host: str) -> set[str]:
        return {"192.168.0.1"}

    monkeypatch.setattr(client_module, "_resolve_addresses", fake_resolve)
    with pytest.raises(WebSearchError):
        await _resolve_pinned_host("internal.example", resolve_timeout=5)


async def test_resolve_pinned_host_dns_timeout_is_timeout(monkeypatch):
    from selara.infrastructure.http.web_search import client as client_module

    async def slow_resolve(host: str) -> set[str]:
        await asyncio.sleep(1.0)
        return {"1.2.3.4"}

    monkeypatch.setattr(client_module, "_resolve_addresses", slow_resolve)
    with pytest.raises(WebSearchError) as excinfo:
        await _resolve_pinned_host("slow.example", resolve_timeout=0.05)
    assert excinfo.value.is_timeout is True


async def test_resolve_pinned_host_accepts_global_literal():
    assert await _resolve_pinned_host("1.2.3.4", resolve_timeout=5) == "1.2.3.4"


async def test_resolve_pinned_host_rejects_private_literal():
    with pytest.raises(WebSearchError):
        await _resolve_pinned_host("127.0.0.1", resolve_timeout=5)


# ------------------------------------------------------------------ pinned transport


class _DummyStream:
    """Stand-in for httpcore AsyncNetworkStream; only identity is asserted."""


async def test_pinned_backend_substitutes_pinned_ip(monkeypatch):
    from selara.infrastructure.http.web_search import dns_pinning

    captured: list[tuple[str, int]] = []

    async def fake_connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        captured.append((host, port))
        return _DummyStream()

    # Patch the parent backend: only the TCP connect target changes, the
    # hostname used for TLS SNI / Host header is the caller's concern and is
    # preserved by construction (URL keeps the original hostname).
    monkeypatch.setattr(dns_pinning.AnyIOBackend, "connect_tcp", fake_connect_tcp)
    backend = dns_pinning._PinnedBackend({"example.com": "1.2.3.4"})

    stream = await backend.connect_tcp("example.com", 443)
    assert isinstance(stream, _DummyStream)
    assert captured == [("1.2.3.4", 443)]

    # Fail closed: an unpinned host is refused (no ordinary-DNS fallback).
    # A pin-table miss means the resolve-validate-pin invariant upstream was
    # violated; dialing ordinary DNS here would silently re-open the
    # DNS-rebinding TOCTOU window the pinning exists to close.
    with pytest.raises(httpx.LocalProtocolError, match="DNS pin missing for host"):
        await backend.connect_tcp("other.example", 80)
    assert captured == [("1.2.3.4", 443)]


def test_pinned_transport_pin_normalizes_host():
    transport = PinnedDnsTransport(resolve_timeout=1.0)
    transport.pin("Example.com", "1.2.3.4")
    transport.pin("[2001:DB8::1]", "2001:db8::1")
    assert transport._pins == {"example.com": "1.2.3.4", "2001:db8::1": "2001:db8::1"}


async def test_pinned_transport_is_usable_as_httpx_transport(monkeypatch):
    from selara.infrastructure.http.web_search import dns_pinning

    async def fake_connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        raise httpx.ConnectError("no real sockets in unit tests")

    monkeypatch.setattr(dns_pinning.AnyIOBackend, "connect_tcp", fake_connect_tcp)
    transport = PinnedDnsTransport(resolve_timeout=1.0)
    transport.pin("example.com", "1.2.3.4")
    request = httpx.Request("GET", "https://example.com/")
    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(request)
    await transport.aclose()


# ------------------------------------------- IDN pin key + fail-closed regressions


class _PinRecordingTransport(httpx.AsyncBaseTransport):
    """Stub PinnedDnsTransport: records pin() calls, forbids real requests."""

    def __init__(self, *, resolve_timeout: float) -> None:
        self.resolve_timeout = resolve_timeout
        self.pins: dict[str, str] = {}

    def pin(self, host: str, ip: str) -> None:
        self.pins[host] = ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be reached in this test")


def _install_pinning_stub(monkeypatch) -> list[_PinRecordingTransport]:
    """Swap the client's production PinnedDnsTransport for a recording stub.

    Returns the list the stub instances are appended to (fetch_page builds the
    transport internally, so the test reads the pins off the created instance).
    """
    from selara.infrastructure.http.web_search import client as client_module

    async def fake_resolve(host: str) -> set[str]:
        return {"1.2.3.4"}

    monkeypatch.setattr(client_module, "_resolve_addresses", fake_resolve)

    created: list[_PinRecordingTransport] = []

    class StubTransport(_PinRecordingTransport):
        def __init__(self, *, resolve_timeout: float) -> None:
            super().__init__(resolve_timeout=resolve_timeout)
            created.append(self)

    monkeypatch.setattr(client_module, "PinnedDnsTransport", StubTransport)
    return created


async def test_fetch_page_pins_idn_by_punycode_httpcore_key(monkeypatch):
    """IDN URL: the pin key must be the punycode form httpcore dials.

    httpx.URL("https://пример.рф").host is the unicode form while .raw_host is
    b"xn--e1afmkfd.xn--p1ai"; httpcore builds its request URL from raw_host, so
    pinning by the unicode current.host missed the pin table and the backend
    (previously fail-open) fell back to ordinary DNS. This test fails against
    the pre-fix `transport.pin(current.host, ...)` code.
    """
    created = _install_pinning_stub(monkeypatch)
    client = WebSearchClient(provider=DuckDuckGoProvider())

    with pytest.raises(AssertionError, match="network must not be reached"):
        await client.fetch_page("https://пример.рф/doc", max_chars=100)

    # Verified interactively: httpx.URL("https://пример.рф").raw_host
    # == b"xn--e1afmkfd.xn--p1ai".
    assert created[0].pins == {"xn--e1afmkfd.xn--p1ai": "1.2.3.4"}


async def test_fetch_page_pins_literal_ip_by_raw_host_key(monkeypatch):
    created = _install_pinning_stub(monkeypatch)
    client = WebSearchClient(provider=DuckDuckGoProvider())

    with pytest.raises(AssertionError, match="network must not be reached"):
        await client.fetch_page("http://1.2.3.4/x", max_chars=100)

    assert created[0].pins == {"1.2.3.4": "1.2.3.4"}


async def test_pinned_backend_fails_closed_on_missing_pin(monkeypatch):
    from selara.infrastructure.http.web_search import dns_pinning

    async def no_dial(self, host, port, timeout=None, local_address=None, socket_options=None):
        raise AssertionError("backend must not dial for an unpinned host")

    monkeypatch.setattr(dns_pinning.AnyIOBackend, "connect_tcp", no_dial)
    backend = dns_pinning._PinnedBackend({})

    with pytest.raises(httpx.LocalProtocolError, match="DNS pin missing for host"):
        await backend.connect_tcp("example.com", 443)


async def test_pinned_transport_fails_closed_without_pins_end_to_end(monkeypatch):
    from selara.infrastructure.http.web_search import dns_pinning

    async def no_dial(self, host, port, timeout=None, local_address=None, socket_options=None):
        raise AssertionError("backend must not dial for an unpinned host")

    monkeypatch.setattr(dns_pinning.AnyIOBackend, "connect_tcp", no_dial)
    transport = PinnedDnsTransport(resolve_timeout=1.0)
    request = httpx.Request("GET", "https://example.com/")

    with pytest.raises(httpx.LocalProtocolError, match="DNS pin missing for host"):
        await transport.handle_async_request(request)
    await transport.aclose()
