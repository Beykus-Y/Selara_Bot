from __future__ import annotations

import httpx
import pytest

from selara.infrastructure.http.gacha_client import HttpGachaClient


class _FakeAsyncClient:
    def __init__(self, *, response: httpx.Response) -> None:
        self._response = response
        self.captured_headers: dict[str, str] | None = None

    def __call__(self, *, base_url: str, timeout: float):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
        assert path == "/v1/gacha/banners/genshin/cards"
        self.captured_headers = headers or {}
        return self._response


@pytest.mark.asyncio
async def test_get_banner_cards_returns_response_and_etag_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "http://gacha.local/v1/gacha/banners/genshin/cards")
    response = httpx.Response(
        200,
        headers={"etag": '"abc123"'},
        json={"status": "ok", "banner": "genshin", "cards": []},
        request=request,
    )
    fake = _FakeAsyncClient(response=response)
    monkeypatch.setattr("selara.infrastructure.http.gacha_client.httpx.AsyncClient", fake)

    client = HttpGachaClient(base_url="http://gacha.local", timeout_seconds=10.0)
    result, etag = await client.get_banner_cards(banner="genshin")

    assert result is not None
    assert result.banner == "genshin"
    assert etag == '"abc123"'
    assert fake.captured_headers == {}


@pytest.mark.asyncio
async def test_get_banner_cards_sends_if_none_match_and_handles_304(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "http://gacha.local/v1/gacha/banners/genshin/cards")
    response = httpx.Response(304, headers={"etag": '"abc123"'}, request=request)
    fake = _FakeAsyncClient(response=response)
    monkeypatch.setattr("selara.infrastructure.http.gacha_client.httpx.AsyncClient", fake)

    client = HttpGachaClient(base_url="http://gacha.local", timeout_seconds=10.0)
    result, etag = await client.get_banner_cards(banner="genshin", if_none_match='"abc123"')

    assert result is None
    assert etag == '"abc123"'
    assert fake.captured_headers == {"If-None-Match": '"abc123"'}
