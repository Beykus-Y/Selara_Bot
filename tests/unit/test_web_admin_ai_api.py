"""Owner AI settings in /app/admin: the Mini App admin endpoints behind the admin web session."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.model_catalog import PROFILE_NAMES, ModelProfile
from selara.core.config import Settings
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.model_catalog import build_model_catalog
from selara.infrastructure.db.models import ChatModel
from selara.web import app as web_app_module
from selara.web.miniapp_admin import build_miniapp_admin_router

PREFIX = "/app/admin/api"
HEADERS = {"X-Selara-Admin": "1"}


def _settings() -> Settings:
    return Settings(
        BOT_TOKEN="123456:test", DATABASE_URL="sqlite+aiosqlite:///:memory:", ADMIN_USER_ID=77,
        WEB_AUTH_SECRET="secret", WEB_BASE_URL="http://127.0.0.1:8080", BOT_USERNAME="selara_test_bot",
    )


def _guard(request: Request) -> None:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-selara-admin") != "1":
        raise HTTPException(403, "header")


@pytest.fixture
async def api():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _, store = build_model_catalog(factory)
    for key, name in PROFILE_NAMES.items():
        await store.save_profile(ModelProfile(key, name))
    actor = SimpleNamespace(id=77)

    async def load_user(session, request):
        return None if actor.id is None else SimpleNamespace(telegram_user_id=actor.id)

    app = FastAPI()
    app.include_router(build_miniapp_admin_router(
        settings=_settings(), session_factory=factory, load_user=load_user,
        broadcast_preview_handler=AsyncMock(), broadcast_start_handler=AsyncMock(),
        broadcast_status_handler=AsyncMock(), telegram_bot_probe=AsyncMock(),
        prefix=PREFIX, ai_only=True, unauthorized_detail="Сессия админки истекла.", mutation_guard=_guard,
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, actor
    await engine.dispose()


async def test_only_the_ai_and_monetization_routes_are_mounted_for_the_web_admin(api):
    client, _ = api
    assert (await client.get(f"{PREFIX}/ai/model-profiles")).status_code == 200
    assert (await client.get(f"{PREFIX}/ai/models")).status_code == 200
    assert (await client.get(f"{PREFIX}/ai/feature-routes")).status_code == 200
    assert (await client.get(f"{PREFIX}/monetization/quota-mode")).status_code == 200
    assert (await client.get(f"{PREFIX}/monetization/personal-config")).status_code == 200
    assert (await client.get(f"{PREFIX}/monetization/grants")).status_code == 200
    assert (await client.get(f"{PREFIX}/ai/breakdown?period_days=7")).status_code == 200
    for path in ("feedback", "logs", "alerts", "audience", "broadcasts", "summary", "health"):
        assert (await client.get(f"{PREFIX}/{path}")).status_code == 404, path


@pytest.mark.parametrize("actor_id,status", [(None, 401), (42, 403)])
async def test_the_web_admin_api_requires_the_owner(api, actor_id, status):
    client, actor = api
    actor.id = actor_id
    for path in ("ai/models", "ai/model-profiles", "monetization/quota-mode", "monetization/grants"):
        assert (await client.get(f"{PREFIX}/{path}")).status_code == status
    body = {"quota_mode": "requests"}
    assert (await client.put(f"{PREFIX}/monetization/quota-mode", json=body, headers=HEADERS)).status_code == status


async def test_every_change_needs_the_admin_header_but_reads_do_not(api):
    client, _ = api
    body = {"quota_mode": "requests"}
    assert (await client.put(f"{PREFIX}/monetization/quota-mode", json=body)).status_code == 403
    assert (await client.put(f"{PREFIX}/monetization/quota-mode", json=body, headers=HEADERS)).status_code == 200
    grant = {"scope": "user", "target_id": 5, "days": 1, "reason": "x", "idempotency_key": "k1"}
    assert (await client.post(f"{PREFIX}/monetization/grants", json=grant)).status_code == 403


async def test_grants_from_the_web_admin_are_journaled_as_admin_panel(api):
    client, _ = api
    grant = {"scope": "user", "target_id": 5, "days": 3, "reason": "тест", "idempotency_key": "web-1", "notify": False}
    assert (await client.post(f"{PREFIX}/monetization/grants", json=grant, headers=HEADERS)).status_code == 200
    rows = (await client.get(f"{PREFIX}/monetization/grants")).json()["items"]
    assert [row["source"] for row in rows] == ["admin_panel"]


async def test_models_and_profiles_are_changed_through_the_same_endpoints(api):
    client, _ = api
    model = {
        "key": "one", "model_id": "provider/one", "display_name": "One",
        "prompt_price_usd_per_million": "1", "completion_price_usd_per_million": "2",
        "capabilities": {"supports_tools": True}, "aliases": [],
    }
    assert (await client.post(f"{PREFIX}/ai/models", json=model)).status_code == 403
    assert (await client.post(f"{PREFIX}/ai/models", json=model, headers=HEADERS)).status_code == 201
    profile = (await client.get(f"{PREFIX}/ai/model-profiles")).json()["items"][0]
    payload = {key: profile[key] for key in ("display_name", "model_key", "ail_multiplier", "enabled", "revision")}
    payload["model_key"] = "one"
    updated = await client.put(f"{PREFIX}/ai/model-profiles/{profile['profile_key']}", json=payload, headers=HEADERS)
    assert updated.status_code == 200
    assert updated.json()["item"]["effective"]["model_id"] == "provider/one"


# --- the page, auth and the per-chat Selara settings ----------------------------------------------------------


class _NoCommitSession:
    async def commit(self) -> None:
        return None


class _SessionFactory:
    def __call__(self):
        session = _NoCommitSession()

        class _Manager:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Manager()


async def _client(factory):
    app = web_app_module.create_web_app(settings=_settings(), session_factory=factory)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", follow_redirects=False)


async def test_the_ai_page_and_api_need_an_admin_session():
    client = await _client(_SessionFactory())
    try:
        page = await client.get("/app/admin/ai")
        api_response = await client.get("/app/admin/api/ai/models")
        chat = await client.get("/app/admin/api/chats/-100/selara")
    finally:
        await client.aclose()
    assert page.status_code == 303 and page.headers["location"] == "/app/admin/login"
    assert api_response.status_code == 401 and api_response.json()["ok"] is False
    assert chat.status_code == 401


def _admin_session(monkeypatch, admin_id: int | None = 77):
    class FakeAuthRepo:
        def __init__(self, session) -> None:
            pass

        async def get_admin_by_session(self, *, session_token, now, touch):
            return admin_id

    monkeypatch.setattr(web_app_module, "SqlAlchemyAdminAuthRepository", FakeAuthRepo)
    return {"selara_admin_session": "token"}


async def test_the_ai_page_renders_for_the_admin_with_its_script(monkeypatch):
    cookies = _admin_session(monkeypatch)
    settings = _settings()
    client = await _client(_SessionFactory())
    try:
        response = await client.get("/app/admin/ai", cookies={settings.admin_session_cookie_name: "token"})
        nav = await client.get("/app/admin/ai", cookies={settings.admin_session_cookie_name: "token"})
    finally:
        await client.aclose()
    assert cookies and response.status_code == 200
    assert "admin-ai.js" in response.text and "admin-ai.css" in response.text
    assert "/app/admin/ai" in nav.text  # the admin navigation links to the page


async def test_owner_manages_selara_in_a_group_without_chat_rights(monkeypatch):
    _admin_session(monkeypatch)
    settings = _settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([ChatModel(telegram_chat_id=-100, type="supergroup", title="Группа"),
                         ChatModel(telegram_chat_id=5, type="private", title=None)])
        await session.commit()
    client = await _client(factory)
    cookie = {settings.admin_session_cookie_name: "token"}
    try:
        shown = await client.get("/app/admin/api/chats/-100/selara", cookies=cookie)
        private = await client.get("/app/admin/api/chats/5/selara", cookies=cookie)
        missing = await client.get("/app/admin/api/chats/-999/selara", cookies=cookie)
        no_header = await client.post("/app/admin/api/chats/-100/selara", cookies=cookie,
                                      content="action=member_mode&value=1",
                                      headers={"content-type": "application/x-www-form-urlencoded"})
        changed = await client.post("/app/admin/api/chats/-100/selara", cookies=cookie,
                                    content="action=member_mode&value=1",
                                    headers={"content-type": "application/x-www-form-urlencoded", **HEADERS})
    finally:
        await client.aclose()
        await engine.dispose()
    assert shown.status_code == 200 and shown.json()["can_manage"] is True and shown.json()["chat_title"] == "Группа"
    assert private.status_code == 404 and missing.status_code == 404
    assert no_header.status_code == 403
    assert changed.status_code == 200 and changed.json()["member_mode"] is True


async def test_miniapp_selara_in_chat_routes_refuse_requests_without_a_session():
    client = await _client(_SessionFactory())
    try:
        read = await client.get("/api/miniapp/chat/-100/selara")
        write = await client.post("/api/miniapp/chat/-100/selara", content="action=member_mode&value=1",
                                  headers={"content-type": "application/x-www-form-urlencoded"})
    finally:
        await client.aclose()
    assert read.status_code == 401 and write.status_code == 401
