"""Real async SQL storage behind the existing owner authentication dependency."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.model_catalog import CatalogModel, ModelProfile, PROFILE_NAMES
from selara.application.model_router import DefaultModelRouter
from selara.core.config import Settings
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.model_catalog import build_model_catalog
from selara.infrastructure.db.models import LlmModelCatalogModel
from selara.web.miniapp_admin import build_miniapp_admin_router

MODEL = {
    "key": "one", "model_id": "provider/one", "display_name": "One",
    "prompt_price_usd_per_million": "1", "completion_price_usd_per_million": "2",
    "capabilities": {"supports_tools": True}, "aliases": ["provider/snapshot"],
}
PREFIX = "/api/miniapp/admin/ai"


@pytest.fixture
async def api():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cache, store = build_model_catalog(factory)
    for key, name in PROFILE_NAMES.items():
        await store.save_profile(ModelProfile(key, name))
    actor = SimpleNamespace(id=77)
    async def load_user(session, request):
        return None if actor.id is None else SimpleNamespace(telegram_user_id=actor.id)
    settings = Settings(BOT_TOKEN="123456:test", DATABASE_URL="sqlite+aiosqlite:///:memory:", ADMIN_USER_ID=77)
    app = FastAPI()
    app.include_router(build_miniapp_admin_router(
        settings=settings, session_factory=factory, load_user=load_user,
        broadcast_preview_handler=AsyncMock(), broadcast_start_handler=AsyncMock(),
        broadcast_status_handler=AsyncMock(), telegram_bot_probe=AsyncMock(),
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, actor, store, cache, factory
    await engine.dispose()


@pytest.mark.parametrize("actor_id,status", [(None, 401), (42, 403), (43, 403), (77, 200)])
async def test_existing_owner_auth_guards_every_endpoint(api, actor_id, status):
    client, actor, *_ = api
    actor.id = actor_id  # 43 represents a chat administrator; no global owner privilege.
    for path in ("models", "model-profiles"):
        response = await client.get(f"{PREFIX}/{path}")
        assert response.status_code == status
    if status != 200:
        assert (await client.post(f"{PREFIX}/models", json=MODEL)).status_code == status
        assert (await client.put(f"{PREFIX}/models/one", json={**MODEL, "revision": 1})).status_code == status
        assert (await client.put(f"{PREFIX}/model-profiles/basic", json={})).status_code == status
        quota_mode = "/api/miniapp/admin/monetization/quota-mode"
        assert (await client.get(quota_mode)).status_code == status
        assert (await client.put(quota_mode, json={"quota_mode": "ail", "confirm": True})).status_code == status


async def test_catalog_profile_crud_conflict_and_fallback(api):
    client, _, store, _, factory = api
    created = await client.post(f"{PREFIX}/models", json=MODEL)
    assert created.status_code == 201, created.text
    model = created.json()["item"]
    assert model["revision"] == 1
    assert model["updated_by"] == 77
    assert (await client.post(f"{PREFIX}/models", json=MODEL)).status_code == 409
    assert (await client.post(f"{PREFIX}/models", json={**MODEL, "key": "two", "model_id": "other"})).status_code == 409
    profile = (await client.get(f"{PREFIX}/model-profiles")).json()["items"][0]
    payload = {key: profile[key] for key in ("display_name", "model_key", "ail_multiplier", "enabled", "revision")}
    payload.update(model_key="one", ail_multiplier="5")
    updated = await client.put(f"{PREFIX}/model-profiles/{profile['profile_key']}", json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["item"]["effective"]["model_id"] == "provider/one"
    assert (await client.put(f"{PREFIX}/model-profiles/{profile['profile_key']}", json=payload)).status_code == 409
    edit = {key: value for key, value in MODEL.items() if key != "key"}
    edit.update(revision=1, enabled=False)
    assert (await client.put(f"{PREFIX}/models/one", json=edit)).status_code == 422
    disabled = await client.put(f"{PREFIX}/models/one", json={**edit, "confirm_disable": True})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["item"]["revision"] == 2
    assert (await client.put(f"{PREFIX}/models/one", json=edit)).status_code == 409
    profiles = (await client.get(f"{PREFIX}/model-profiles")).json()["items"]
    current = next(p for p in profiles if p["profile_key"] == profile["profile_key"])
    assert current["effective"]["is_fallback"]
    assert (await client.put(f"{PREFIX}/model-profiles/fast", json={
        **payload, "revision": 1, "model_key": "one",
    })).status_code == 422
    for assignment in ("missing", None):
        response = await client.put(f"{PREFIX}/model-profiles/{profile['profile_key']}", json={
            **payload, "revision": current["revision"], "model_key": assignment,
        })
        assert response.status_code == (422 if assignment else 200)
    async with factory() as session:
        assert (await session.get(LlmModelCatalogModel, "one")).updated_by == 77
    assert (await store.load()).models_by_id["provider/snapshot"].key == "one"


@pytest.mark.parametrize("field,value", [
    ("prompt_price_usd_per_million", "-1"), ("completion_price_usd_per_million", "NaN"),
    ("prompt_price_usd_per_million", "Infinity"), ("prompt_price_usd_per_million", "1000001"),
    ("prompt_price_usd_per_million", "0.0000000001"), ("model_id", ""),
    ("aliases", [""]), ("aliases", ["provider/one"]), ("enabled", "yes"),
])
async def test_invalid_models_rejected_before_storage(api, field, value):
    client, *_ = api
    response = await client.post(f"{PREFIX}/models", json={**MODEL, field: value})
    assert response.status_code == 422, response.text
    assert (await client.get(f"{PREFIX}/models")).json()["items"] == []


@pytest.mark.parametrize("value", [None, "0", "0.123456789"])
async def test_unknown_zero_decimal_prices(api, value):
    client, *_ = api
    response = await client.post(f"{PREFIX}/models", json={**MODEL, "prompt_price_usd_per_million": value})
    assert response.status_code == 201, response.text
    actual = response.json()["item"]["prompt_price_usd_per_million"]
    assert (actual is None) if value is None else Decimal(actual) == Decimal(value)


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1001", "0.0000000001"])
async def test_invalid_multiplier(api, value):
    client, *_ = api
    response = await client.put(f"{PREFIX}/model-profiles/basic", json={
        "display_name": "Basic", "model_key": None, "enabled": True, "ail_multiplier": value, "revision": 1,
    })
    assert response.status_code == 422, response.text


async def test_failed_commit_retains_last_known_good(api):
    client, _, store, cache, factory = api
    await store.save_model(CatalogModel("one", "one", "One"))
    await store.save_profile(ModelProfile("basic", "Basic", "one"))
    router = DefaultModelRouter("legacy", cache)
    assert (await router.resolve(profile_key="basic")).model_id == "one"
    generation = cache._generation
    def fail(session):
        if session.new or session.dirty:
            raise OperationalError("COMMIT", {}, Exception("offline"))
    event.listen(factory.class_.sync_session_class, "before_commit", fail)
    try:
        response = await client.put(f"{PREFIX}/models/one", json={
            "model_id": "changed", "display_name": "One", "revision": 1,
        })
        assert response.status_code == 503
        assert "Текущая рабочая конфигурация не изменена" in response.text
    finally:
        event.remove(factory.class_.sync_session_class, "before_commit", fail)
    assert cache._generation == generation
    assert (await router.resolve(profile_key="basic")).model_id == "one"
    assert (await store.load()).models_by_key["one"].revision == 1


async def test_read_database_failure_is_safe(api, monkeypatch):
    client, *_ = api
    async def fail(self):
        raise OperationalError("SELECT", {}, Exception("secret SQL"))
    monkeypatch.setattr("selara.infrastructure.db.model_catalog.SqlAlchemyModelCatalogStore.load", fail)
    response = await client.get(f"{PREFIX}/models")
    assert response.status_code == 503
    assert "secret" not in response.text
