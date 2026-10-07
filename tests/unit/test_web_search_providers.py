"""Web search providers: DuckDuckGo 202 challenge, retry, keyed providers, fallback."""
import httpx
import pytest

from selara.infrastructure.http.web_search import WebSearchError, build_web_search_client
from selara.infrastructure.http.web_search import providers
from selara.infrastructure.http.web_search.providers import (
    BraveProvider,
    DuckDuckGoProvider,
    FallbackProvider,
    TavilyProvider,
)

DDG_HTML = (
    '<a rel="nofollow" href="https://example.com/a" class="result-link">Title A</a>'
    '<td class="result-snippet">Snippet A</td>'
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def fake_sleep(_):
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", fake_sleep)


def _transport(statuses, body="", calls=None):
    seq = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        status = seq.pop(0) if len(seq) > 1 else seq[0]
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_ddg_202_challenge_is_reported_as_block_after_one_retry():
    calls = []
    provider = DuckDuckGoProvider(transport=_transport([202], "anomaly", calls))
    with pytest.raises(WebSearchError) as exc:
        await provider.search("погода", max_results=5)
    assert "отклоняет" in exc.value.message
    assert "202" not in exc.value.message
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_ddg_transient_202_then_200_succeeds():
    seq = [202, 200]

    def handler(request):
        status = seq.pop(0)
        return httpx.Response(status, text=DDG_HTML if status == 200 else "")

    provider = DuckDuckGoProvider(transport=httpx.MockTransport(handler))
    items = await provider.search("x", max_results=5)
    assert [i.url for i in items] == ["https://example.com/a"]


@pytest.mark.asyncio
async def test_tavily_parses_results_and_sends_bearer():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://t.example/1", "content": "C"},
            {"title": "bad", "url": "javascript:x", "content": ""},
        ]})

    provider = TavilyProvider(api_key="k", base_url="https://api.tavily.com", timeout_seconds=5,
                              transport=httpx.MockTransport(handler))
    items = await provider.search("q", max_results=3)
    assert [(i.title, i.snippet) for i in items] == [("T", "C")]
    assert calls[0].headers["Authorization"] == "Bearer k"


@pytest.mark.asyncio
async def test_brave_parses_results_and_sends_token():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"web": {"results": [
            {"title": "B", "url": "https://b.example", "description": "D"}]}})

    provider = BraveProvider(api_key="k", base_url="https://api.search.brave.com", timeout_seconds=5,
                             transport=httpx.MockTransport(handler))
    items = await provider.search("q", max_results=3)
    assert items[0].snippet == "D"
    assert calls[0].headers["X-Subscription-Token"] == "k"


@pytest.mark.asyncio
async def test_keyed_provider_error_status_and_bad_json_map_to_web_search_error():
    for transport in (httpx.MockTransport(lambda r: httpx.Response(401, text="no")),
                      httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))):
        provider = TavilyProvider(api_key="k", base_url="https://x", timeout_seconds=5, transport=transport)
        with pytest.raises(WebSearchError):
            await provider.search("q", max_results=3)


@pytest.mark.asyncio
async def test_fallback_uses_next_provider_when_first_fails():
    failing = TavilyProvider(api_key="k", base_url="https://x", timeout_seconds=5,
                             transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    ddg = DuckDuckGoProvider(transport=_transport([200], DDG_HTML))
    items = await FallbackProvider([failing, ddg]).search("q", max_results=5)
    assert items[0].url == "https://example.com/a"


@pytest.mark.asyncio
async def test_fallback_raises_last_error_when_all_fail():
    ddg = DuckDuckGoProvider(transport=_transport([403]))
    with pytest.raises(WebSearchError):
        await FallbackProvider([ddg]).search("q", max_results=5)


def test_build_client_provider_selection():
    assert build_web_search_client(enabled=False, provider="tavily", api_key="k") is None
    assert build_web_search_client(enabled=True, provider="nope") is None
    assert build_web_search_client(enabled=True, provider="duckduckgo").provider_name == "duckduckgo"
    assert build_web_search_client(enabled=True, provider="tavily", api_key="k").provider_name == "tavily+duckduckgo"
    assert build_web_search_client(enabled=True, provider="brave", api_key="k").provider_name == "brave+duckduckgo"
    # keyed provider without a key degrades to duckduckgo instead of disabling search
    assert build_web_search_client(enabled=True, provider="tavily", api_key=" ").provider_name == "duckduckgo"


from selara.infrastructure.http.web_search.providers import SearxngProvider


@pytest.mark.asyncio
async def test_searxng_parses_json_and_uses_json_format():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [
            {"title": "S", "url": "https://s.example", "content": "C"}]})

    provider = SearxngProvider(base_url="http://searxng:8080", transport=httpx.MockTransport(handler))
    items = await provider.search("q", max_results=5)
    assert items[0].snippet == "C"
    assert calls[0].url.params["format"] == "json"


@pytest.mark.asyncio
async def test_searxng_unreachable_falls_back_to_ddg_and_is_skipped_during_cooldown():
    searx_calls = []

    def down(request):
        searx_calls.append(request)
        raise httpx.ConnectError("no such host")

    searx = SearxngProvider(transport=httpx.MockTransport(down))
    ddg = DuckDuckGoProvider(transport=_transport([200], DDG_HTML))
    chain = FallbackProvider([searx, ddg])
    assert (await chain.search("q", max_results=5))[0].url == "https://example.com/a"
    assert (await chain.search("q", max_results=5))[0].url == "https://example.com/a"
    assert len(searx_calls) == 1  # second query skipped the failed primary


@pytest.mark.asyncio
async def test_searxng_403_json_disabled_falls_back():
    searx = SearxngProvider(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="forbidden")))
    ddg = DuckDuckGoProvider(transport=_transport([200], DDG_HTML))
    assert (await FallbackProvider([searx, ddg]).search("q", max_results=5))[0].title == "Title A"


def test_build_client_defaults_to_searxng_then_duckduckgo():
    assert build_web_search_client(enabled=True, provider="auto").provider_name == "searxng+duckduckgo"
    assert build_web_search_client(enabled=True, provider="").provider_name == "searxng+duckduckgo"
    assert build_web_search_client(enabled=True, provider="searxng").provider_name == "searxng+duckduckgo"


def test_config_default_provider_is_auto(monkeypatch):
    from selara.core.config import Settings

    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    assert Settings.model_fields["web_search_provider"].default == "auto"
