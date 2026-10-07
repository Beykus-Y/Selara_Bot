from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from selara.application.use_cases import gacha as gacha_use_cases
from selara.application.use_cases.gacha import GachaUseCaseError
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
from selara.infrastructure.http.gacha_client import (
    GachaCollectionCardPayload,
    GachaCollectionResponse,
    GachaHistoryEntryPayload,
    GachaPlayerPayload,
    GachaProfileResponse,
    GachaRarityCountPayload,
)
from selara.web.miniapp_gacha import build_miniapp_gacha_router


def _settings(**overrides) -> Settings:
    payload = {
        "BOT_TOKEN": "123456:TEST",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "WEB_AUTH_SECRET": "test-secret",
        "WEB_BASE_URL": "http://testserver",
        "BOT_USERNAME": "selara_test_bot",
        "GACHA_BASE_URL": "http://gacha.local",
        "GACHA_SERVICE_TOKEN": "service-secret",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


class _Session:
    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class _SessionFactory:
    def __call__(self) -> _Session:
        return _Session()


def _profile(user_id: int, banner: str, limit: int = 5) -> GachaProfileResponse:
    return GachaProfileResponse(
        status="ok",
        banner=banner,
        message="Готово",
        player=GachaPlayerPayload(
            user_id=user_id,
            adventure_rank=2,
            adventure_xp=120,
            xp_into_rank=120,
            xp_for_next_rank=450,
            total_points=6000,
            total_primogems=2612,
        ),
        unique_cards=1,
        total_copies=3,
        rarity_counts=[
            GachaRarityCountPayload(rarity="epic", rarity_label="🟪 Эпическая", summary_label="🟪 1", count=1)
        ],
        recent_pulls=[
            GachaHistoryEntryPayload(
                pull_id=42,
                pulled_at="2026-01-01T00:00:00Z",
                card_name="Оророн",
                rarity="epic",
                rarity_label="🟪 Эпическая",
                points=5000,
                primogems=10,
                adventure_xp_gained=150,
                image_url="/images/ororon.png",
            )
        ],
    )


def _collection(user_id: int, banner: str) -> GachaCollectionResponse:
    return GachaCollectionResponse(
        status="ok",
        banner=banner,
        user_id=user_id,
        cards=[
            GachaCollectionCardPayload(
                code="ororon",
                name="Оророн",
                rarity="epic",
                rarity_label="🟪 Эпическая",
                copies_owned=2,
                image_url="/images/ororon.png",
            )
        ],
        total_unique=1,
        total_copies=2,
    )


class _Env:
    def __init__(self, client: httpx.AsyncClient, calls: list[tuple]) -> None:
        self.client = client
        self.calls = calls

    def as_user(self, user_id: int | None) -> dict[str, str]:
        return {} if user_id is None else {"x-test-user": str(user_id)}


def _build_app(*, profile_loader=None, collection_loader=None, settings: Settings | None = None) -> FastAPI:
    async def load_user(session, request):
        raw = request.headers.get("x-test-user")
        if raw is None:
            return None
        return UserSnapshot(
            telegram_user_id=int(raw),
            username=None,
            first_name="T",
            last_name=None,
            is_bot=False,
        )

    app = FastAPI()
    app.include_router(
        build_miniapp_gacha_router(
            settings=settings or _settings(),
            session_factory=_SessionFactory(),
            load_user=load_user,
            profile_loader=profile_loader,
            collection_loader=collection_loader,
        )
    )
    return app


@pytest.fixture
def calls() -> list[tuple]:
    return []


@pytest.fixture
def fake_loaders(calls: list[tuple]):
    async def load_profile(settings, *, user_id, banner, limit):
        calls.append(("profile", user_id, banner, limit))
        return _profile(user_id, banner, limit)

    async def load_collection(settings, *, user_id, banner):
        calls.append(("collection", user_id, banner))
        return _collection(user_id, banner)

    return load_profile, load_collection


@pytest_asyncio.fixture
async def env(fake_loaders, calls):
    app = _build_app(profile_loader=fake_loaders[0], collection_loader=fake_loaders[1])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield _Env(client, calls)


@pytest.mark.asyncio
async def test_every_gacha_endpoint_requires_a_session(env):
    for url in (
        "/api/miniapp/gacha/profile?banner=genshin",
        "/api/miniapp/gacha/collection?banner=genshin",
    ):
        response = await env.client.get(url)
        assert response.status_code == 401, url
        body = response.json()
        assert body["ok"] is False and body["message"]


@pytest.mark.asyncio
async def test_profile_uses_only_the_session_user(env, calls):
    # A client-supplied user_id (inside a path, query or body) must have no effect at all.
    response = await env.client.get(
        "/api/miniapp/gacha/profile?banner=genshin&limit=6&user_id=999&telegram_user_id=999",
        headers=env.as_user(1),
    )

    assert response.status_code == 200
    assert calls == [("profile", 1, "genshin", 6)]


@pytest.mark.asyncio
async def test_collection_uses_only_the_session_user(env, calls):
    response = await env.client.get(
        "/api/miniapp/gacha/collection?banner=hsr&user_id=999",
        headers=env.as_user(2),
    )

    assert response.status_code == 200
    assert calls == [("collection", 2, "hsr")]


@pytest.mark.asyncio
async def test_profile_payload_is_passed_through_unchanged(env):
    response = await env.client.get("/api/miniapp/gacha/profile?banner=genshin", headers=env.as_user(1))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == _profile(1, "genshin").model_dump(mode="json")


@pytest.mark.asyncio
async def test_collection_payload_is_passed_through_unchanged(env):
    response = await env.client.get("/api/miniapp/gacha/collection?banner=hsr", headers=env.as_user(1))

    assert response.status_code == 200
    body = response.json()
    assert body == _collection(1, "hsr").model_dump(mode="json")
    # The frontend reads these fields directly.
    assert {"status", "banner", "user_id", "cards", "total_unique", "total_copies"} <= body.keys()
    assert {"code", "name", "rarity", "rarity_label", "copies_owned", "image_url"} <= body["cards"][0].keys()


@pytest.mark.asyncio
async def test_unknown_banner_is_rejected_without_calling_upstream(env, calls):
    for url in (
        "/api/miniapp/gacha/profile?banner=honkai",
        "/api/miniapp/gacha/collection?banner=honkai",
    ):
        response = await env.client.get(url, headers=env.as_user(1))
        assert response.status_code == 422, url
        assert response.json()["ok"] is False

    assert calls == []


@pytest.mark.asyncio
async def test_banner_is_normalized_and_limit_is_clamped(env, calls):
    await env.client.get("/api/miniapp/gacha/profile?banner=HSR&limit=999", headers=env.as_user(1))
    await env.client.get("/api/miniapp/gacha/profile?banner=genshin&limit=-5", headers=env.as_user(1))
    # No banner/limit at all: the documented defaults apply.
    await env.client.get("/api/miniapp/gacha/profile", headers=env.as_user(1))

    assert calls == [
        ("profile", 1, "hsr", 10),
        ("profile", 1, "genshin", 1),
        ("profile", 1, "genshin", 5),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operational", "expected_status"),
    [(True, 503), (False, 502)],
)
async def test_upstream_transport_failure_is_mapped_without_leaking_internals(operational, expected_status):
    async def failing_loader(settings, *, user_id, banner, limit):
        raise GachaUseCaseError("Гача-сервер вернул ошибку.", is_operational=operational)

    app = _build_app(profile_loader=failing_loader)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/miniapp/gacha/profile?banner=genshin", headers={"x-test-user": "1"}
        )

    assert response.status_code == expected_status
    assert response.json()["ok"] is False
    assert "Гача-сервер вернул ошибку." not in response.json()["message"]


@pytest.mark.asyncio
async def test_router_calls_the_real_use_case_with_the_service_token(monkeypatch):
    """The default wiring (no injected loaders) must build the gacha client with GACHA_SERVICE_TOKEN."""
    seen: list[tuple] = []

    class FakeClient:
        def __init__(self, *, base_url: str, timeout_seconds: float, service_token: str = "") -> None:
            seen.append(("init", base_url, service_token))

        async def get_collection(self, *, user_id: int, banner: str) -> GachaCollectionResponse:
            seen.append(("collection", user_id, banner))
            return _collection(user_id, banner)

    monkeypatch.setattr(gacha_use_cases, "HttpGachaClient", FakeClient)

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/miniapp/gacha/collection?banner=genshin", headers={"x-test-user": "7"}
        )

    assert response.status_code == 200
    assert seen == [
        ("init", "http://gacha.local", "service-secret"),
        ("collection", 7, "genshin"),
    ]
