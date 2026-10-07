from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.llm_routes import (
    CachedFeatureRoutes, FEATURE_ROUTES, ROUTE_GROUP_ASK, ROUTE_PETS, validate_route,
)
from selara.application.model_catalog import CachedModelCatalogProvider, CatalogModel, CatalogSnapshot, ModelCapabilities, ModelProfile
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.llm_routes import build_feature_routes
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmConfig

MODEL = CatalogModel("big", "provider/big", "Big", capabilities=ModelCapabilities(True, True, False))
SNAPSHOT = CatalogSnapshot((MODEL,), (ModelProfile("analytics", "Аналитик", "big"),))


def _response():
    return SimpleNamespace(model="provider/big", usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                           choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])


def _client(routes):
    async def load():
        return SNAPSHOT
    llm = LlmClient(LlmConfig(api_key="t", model="legacy", summary_model="summary"),
                    model_catalog=CachedModelCatalogProvider(load), feature_routes=routes)
    llm._client.chat.completions.create = AsyncMock(return_value=_response())
    return llm


def _ctx(feature):
    return LlmAccountingContext(invocation_id=1, feature=feature, stage="s", chat_id=1)


def _sent_model(llm):
    return llm._client.chat.completions.create.await_args.kwargs["model"]


@pytest.mark.asyncio
async def test_routed_feature_uses_profile_model_and_others_keep_legacy():
    async def load():
        return {ROUTE_GROUP_ASK: "analytics", ROUTE_PETS: None}
    llm = _client(CachedFeatureRoutes(load))
    await llm.chat_with_tools([{"role": "user", "content": "q"}], [], accounting_context=_ctx("llm_admin"))
    assert _sent_model(llm) == "provider/big"
    await llm.chat_simple([{"role": "user", "content": "q"}], accounting_context=_ctx("pet_talk"))
    assert _sent_model(llm) == "legacy"  # pets route unset -> LLM_MODEL
    await llm.chat_simple([{"role": "user", "content": "q"}], accounting_context=_ctx("personal_chat"))
    assert _sent_model(llm) == "legacy"  # not a routed feature
    await llm.chat_simple([{"role": "user", "content": "q"}])
    assert _sent_model(llm) == "legacy"  # no accounting context


@pytest.mark.asyncio
async def test_route_failure_falls_back_to_legacy_model():
    routes = SimpleNamespace(profile_for_feature=AsyncMock(side_effect=RuntimeError("boom")))
    llm = _client(routes)
    await llm.chat_simple([{"role": "user", "content": "q"}], accounting_context=_ctx("llm_admin"))
    assert _sent_model(llm) == "legacy"


@pytest.mark.asyncio
async def test_unusable_profile_falls_back_to_legacy_model():
    async def load():
        return {ROUTE_GROUP_ASK: "fast"}  # profile missing from the catalog snapshot
    llm = _client(CachedFeatureRoutes(load))
    await llm.chat_simple([{"role": "user", "content": "q"}], accounting_context=_ctx("llm_admin"))
    assert _sent_model(llm) == "legacy"


@pytest.mark.asyncio
async def test_cache_ttl_invalidate_and_last_known_good():
    now = [0.0]
    values = [{ROUTE_GROUP_ASK: "basic"}, {ROUTE_GROUP_ASK: "fast"}]
    calls = {"n": 0, "fail": False}

    async def load():
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("db down")
        return values[min(calls["n"] - 1, 1)]

    routes = CachedFeatureRoutes(load, ttl_seconds=15, clock=lambda: now[0])
    assert await routes.profile_for_feature("llm_admin") == "basic"
    now[0] = 10
    assert await routes.profile_for_feature("llm_admin") == "basic" and calls["n"] == 1
    now[0] = 16
    assert await routes.profile_for_feature("llm_admin") == "fast"
    calls["fail"] = True
    routes.invalidate()
    assert await routes.profile_for_feature("llm_admin") == "fast"  # last known good


def test_validation_and_feature_mapping():
    assert FEATURE_ROUTES["pet_talk"] == FEATURE_ROUTES["pet_event_text"] == ROUTE_PETS
    assert "daily_summary" not in FEATURE_ROUTES
    validate_route(ROUTE_PETS, None)
    for route, profile in (("nope", None), (ROUTE_PETS, "bogus")):
        with pytest.raises(ValueError):
            validate_route(route, profile)


@pytest.mark.asyncio
async def test_store_roundtrip_and_invalidates_cache():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    provider, store = build_feature_routes(async_sessionmaker(engine, expire_on_commit=False))
    assert await provider.profile_for_feature("pet_talk") is None
    await store.save(ROUTE_PETS, "fast", updated_by=1)
    assert await provider.profile_for_feature("pet_talk") == "fast"  # invalidated on save
    await store.save(ROUTE_PETS, None)
    assert await provider.profile_for_feature("pet_event_text") is None
    with pytest.raises(ValueError):
        await store.save("nope", None)
    await engine.dispose()


@pytest.fixture
async def api():
    import httpx
    from fastapi import FastAPI

    from selara.application.model_catalog import PROFILE_NAMES
    from selara.core.config import Settings
    from selara.infrastructure.db.model_catalog import build_model_catalog
    from selara.web.miniapp_admin import build_miniapp_admin_router

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _, catalog_store = build_model_catalog(factory)
    for key, name in PROFILE_NAMES.items():
        await catalog_store.save_profile(ModelProfile(key, name))

    async def load_user(session, request):
        return SimpleNamespace(telegram_user_id=77)

    settings = Settings(BOT_TOKEN="123456:test", DATABASE_URL="sqlite+aiosqlite:///:memory:", ADMIN_USER_ID=77)
    app = FastAPI()
    app.include_router(build_miniapp_admin_router(
        settings=settings, session_factory=factory, load_user=load_user,
        broadcast_preview_handler=AsyncMock(), broadcast_start_handler=AsyncMock(),
        broadcast_status_handler=AsyncMock(), telegram_bot_probe=AsyncMock(),
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory
    await engine.dispose()


async def test_admin_api_reads_and_saves_routes(api):
    client, factory = api
    url = "/api/miniapp/admin/ai/feature-routes"
    data = (await client.get(url)).json()
    assert {i["route_key"] for i in data["items"]} == {"group_ask", "group_member", "pets"}
    assert all(i["profile_key"] is None for i in data["items"])
    assert (await client.put(f"{url}/pets", json={"profile_key": "fast"})).json()["item"]["profile_key"] == "fast"
    _, store = build_feature_routes(factory)
    assert (await store.load())["pets"] == "fast"
    assert (await client.put(f"{url}/pets", json={"profile_key": None})).json()["item"]["profile_key"] is None
    assert (await client.put(f"{url}/pets", json={"profile_key": "bogus"})).status_code == 422
    assert (await client.put(f"{url}/nope", json={"profile_key": None})).status_code == 422
