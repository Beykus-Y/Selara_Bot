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
