from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from gacha_service.web import api


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api.build_router(object()))
    return app


def test_build_router_is_idempotent_and_does_not_share_mutable_routes() -> None:
    first = api.build_router(object())
    second = api.build_router(object())

    first_paths = [route.path for route in first.routes]
    second_paths = [route.path for route in second.routes]

    assert first is not second
    assert first_paths == second_paths
    assert len(first_paths) == len(set(first_paths))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/v1/gacha/pull", {"user_id": 101, "banner": "genshin"}),
        ("/v1/gacha/pull/purchase", {"user_id": 101, "banner": "genshin"}),
        ("/v1/gacha/pulls/1/sell", {"user_id": 101}),
    ],
)
async def test_mutating_user_endpoints_require_service_token(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
) -> None:
    async def fake_session_dependency(_session_factory):
        yield object()

    monkeypatch.setattr(api, "session_dependency", fake_session_dependency)
    monkeypatch.setattr(api, "settings", api.settings.model_copy(update={"service_token": "service-secret"}))
    app = _build_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        missing = await client.post(path, json=payload)
        invalid = await client.post(
            path,
            json=payload,
            headers={"X-Gacha-Service-Token": "wrong-secret"},
        )

    assert missing.status_code == 403
    assert invalid.status_code == 403


@pytest.mark.asyncio
async def test_mutating_user_endpoints_fail_closed_when_service_token_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_session_dependency(_session_factory):
        yield object()

    monkeypatch.setattr(api, "session_dependency", fake_session_dependency)
    monkeypatch.setattr(api, "settings", api.settings.model_copy(update={"service_token": ""}))
    app = _build_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/gacha/pull",
            json={"user_id": 101, "banner": "genshin"},
            headers={"X-Gacha-Service-Token": "anything"},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_public_profile_for_unknown_user_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"get": 0, "create": 0}

    class FakeRepo:
        def __init__(self, session) -> None:
            _ = session

        async def get_player(self, *, user_id: int, banner: str | None = None):
            _ = user_id, banner
            calls["get"] += 1
            return None

        async def get_or_create_player(self, **kwargs):
            _ = kwargs
            calls["create"] += 1
            raise AssertionError("GET profile must not create a player")

        async def get_collection_stats(self, **kwargs):
            _ = kwargs
            return 0, 0

        async def get_user_collection(self, **kwargs):
            _ = kwargs
            return []

        async def get_recent_pulls_by_banner(self, **kwargs):
            _ = kwargs
            return []

    async def fake_session_dependency(_session_factory):
        yield object()

    monkeypatch.setattr(api, "GachaRepository", FakeRepo)
    monkeypatch.setattr(api, "session_dependency", fake_session_dependency)
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/gacha/users/987654/profile?banner=genshin")

    assert response.status_code == 200
    assert response.json()["player"]["user_id"] == 987654
    assert response.json()["player"]["total_points"] == 0
    assert calls == {"get": 1, "create": 0}


@pytest.mark.asyncio
async def test_history_entries_expose_pull_id_for_recovery_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull_id must round-trip through /history so a client can tell a
    known pull apart from an older one when recovering from a lost
    response (see docs/GACHA_MODERNIZATION_TODO.md, Этап 0)."""

    class FakePullHistoryRow:
        def __init__(self, *, pull_id: int) -> None:
            self.id = pull_id
            self.banner = "genshin"
            self.character_code = "traveler"
            self.character_name = "Traveler"
            self.rarity = "epic"
            self.points = 10
            self.primogems = 5
            self.adventure_xp = 3
            self.image_url = "https://example.test/traveler.png"
            self.pulled_at = __import__("datetime").datetime(2026, 8, 19, tzinfo=__import__("datetime").timezone.utc)

    class FakeRepo:
        def __init__(self, session) -> None:
            _ = session

        async def get_recent_pulls_by_banner(self, **kwargs):
            _ = kwargs
            return [FakePullHistoryRow(pull_id=42)]

    async def fake_session_dependency(_session_factory):
        yield object()

    monkeypatch.setattr(api, "GachaRepository", FakeRepo)
    monkeypatch.setattr(api, "session_dependency", fake_session_dependency)
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/gacha/users/101/history?banner=genshin")

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries == [{**entries[0], "pull_id": 42}]
    assert entries[0]["pull_id"] == 42


@pytest.mark.asyncio
async def test_banner_cards_endpoint_returns_full_catalog_without_auth() -> None:
    """Needed for the animated-pull reel (docs/GACHA_MODERNIZATION_TODO.md,
    Этап 3): the main bot has to pick filler characters for the scroll
    animation, but no endpoint exposed the banner's card catalog at all —
    only per-user profile/history/collection existed. This is pure static
    config data (no per-user info, no economy state), so it follows the
    same unauthenticated-read precedent as profile/history/collection."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/gacha/banners/genshin/cards")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["banner"] == "genshin"
    assert len(payload["cards"]) > 0
    first = payload["cards"][0]
    assert {"code", "name", "rarity", "rarity_label", "image_url"} <= first.keys()


@pytest.mark.asyncio
async def test_banner_cards_endpoint_supports_conditional_get_with_etag() -> None:
    """Selara caches the banner catalog client-side (docs/GACHA_MODERNIZATION_TODO.md,
    Этап 3) using this ETag for deterministic invalidation — it must change
    only when the catalog content actually changes (deploy), never on a
    timer, and a matching If-None-Match must return an empty 304 (no need
    to re-transfer ~35KB of JSON on every pull)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.get("/v1/gacha/banners/genshin/cards")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag

        revalidated = await client.get(
            "/v1/gacha/banners/genshin/cards", headers={"If-None-Match": etag}
        )
        assert revalidated.status_code == 304
        assert revalidated.content == b""

        stale = await client.get(
            "/v1/gacha/banners/genshin/cards", headers={"If-None-Match": '"not-the-real-etag"'}
        )
        assert stale.status_code == 200
        assert stale.headers.get("etag") == etag


@pytest.mark.asyncio
async def test_banner_cards_endpoint_404_for_unknown_banner() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/gacha/banners/unknown/cards")

    assert response.status_code == 404
