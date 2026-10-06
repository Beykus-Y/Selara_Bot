from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import (
    AccessTier,
    FeatureAccessDecision,
    FeatureUsageSummary,
    PersonalQuotaLimits,
)
from decimal import Decimal

from selara.application.model_catalog import CatalogModel, CatalogSnapshot, ModelCapabilities, ModelProfile
from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider, ail_limits_from
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    PersonalAiMemoryModel,
    PersonalAiMessageModel,
    PersonalAiProfileModel,
    UserModel,
)
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.handlers import personal_ai
from selara.web.miniapp_personal import build_miniapp_personal_router

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _config(**changes) -> PersonalConfig:
    base = PersonalConfig(
        price_stars=69,
        duration_days=30,
        limits=PersonalQuotaLimits(free_daily=5, paid_daily=150),
        memory_free_limit=20,
        memory_paid_limit=200,
        memory_auto_extract=True,
        memory_extract_every=10,
    )
    return replace(base, **changes)


class FakeAccess:
    """Stands in for FeatureAccessService: the web layer must only read its answers."""

    def __init__(self, tier: AccessTier = AccessTier.FREE, *, fail: bool = False, valid_until=None) -> None:
        self.tier = tier
        self.fail = fail
        self.valid_until = valid_until
        self.reserved = 0

    async def resolve_feature_access(self, *, feature, chat_id, trigger, scope=None, owner_exempt=False, now=None):
        if self.fail:
            raise RuntimeError("boom")
        assert feature == AiFeature.PERSONAL_CHAT
        assert scope is not None and scope.scope_id == chat_id
        tier = AccessTier.OWNER_INTERNAL if owner_exempt else self.tier
        return FeatureAccessDecision(
            allowed=True,
            feature=feature,
            scope_type="user",
            scope_id=str(chat_id),
            access_tier=tier,
            quota_limit=150 if tier == AccessTier.PAID else 5,
            quota_used=1,
            quota_remaining=4,
            period_start=None,
            period_end=None,
            owner_exempt=owner_exempt,
            entitlement_valid_until=self.valid_until if tier == AccessTier.PAID else None,
            entitlement_source="telegram_stars" if tier == AccessTier.PAID else None,
        )

    async def get_usage_summary(self, *, feature, chat_id, trigger, timezone_name, scope=None, owner_exempt=False, now=None):
        if self.fail:
            raise RuntimeError("boom")
        paid = self.tier == AccessTier.PAID
        limit = 150 if paid else 5
        return FeatureUsageSummary(
            feature, "user", str(chat_id), self.tier, limit, 3, limit - 3, NOW, NOW + timedelta(hours=5),
            NOW + timedelta(hours=5), False, owner_exempt, "personal",
        )

    async def reserve_feature_usage(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.reserved += 1
        raise AssertionError("the Mini App must not spend quota")


def _settings(admin_user_id: int | None = 999) -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "WEB_AUTH_SECRET": "test-secret",
            "WEB_BASE_URL": "http://testserver",
            "ADMIN_USER_ID": admin_user_id,
            "BOT_USERNAME": "selara_test_bot",
        }
    )


def _snapshot():
    models = tuple(
        CatalogModel(key, model_id, key.title(), capabilities=ModelCapabilities())
        for key, model_id in (("base", "provider/basic-model"), ("a", "provider/analytics-model"))
    )
    return CatalogSnapshot(models, (
        ModelProfile("basic", "Базовая", "base", Decimal("1")),
        ModelProfile("analytics", "Аналитик", "a", Decimal("2.5")),
        ModelProfile("creative", "Творческая", None, Decimal("5")),
    ))


class _Catalog:
    async def get(self):
        return _snapshot()


class Env:
    def __init__(self, client, factory, access):
        self.client = client
        self.factory = factory
        self.access = access

    def as_user(self, user_id: int | None) -> dict[str, str]:
        return {} if user_id is None else {"x-test-user": str(user_id)}


JSON = {"content-type": "application/json"}


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([UserModel(telegram_user_id=uid, is_bot=False) for uid in (1, 2, 999)])
        await session.commit()
    access = FakeAccess()
    state = {"config": _config()}

    class _Provider:
        async def get(self):
            return state["config"]

    async def load_user(session, request):
        raw = request.headers.get("x-test-user")
        return None if raw is None else UserSnapshot(telegram_user_id=int(raw), username=None, first_name="T", last_name=None, is_bot=False)

    app = FastAPI()
    app.include_router(
        build_miniapp_personal_router(
            settings=_settings(),
            session_factory=factory,
            load_user=load_user,
            personal_config=_Provider(),
            access_service=access,
            offer_checker=lambda _settings, config: config.price_stars is not None,
            model_catalog=_Catalog(),
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        e = Env(client, factory, access)
        e.state = state
        yield e
    await engine.dispose()


async def _count(env: Env, model, **where) -> int:
    async with env.factory() as session:
        query = select(func.count()).select_from(model)
        for key, value in where.items():
            query = query.where(getattr(model, key) == value)
        return int(await session.scalar(query) or 0)


async def _add_fact(env: Env, user_id: int, text: str, *, pinned: bool = False) -> int:
    async with env.factory() as session:
        result = await PersonalAiRepository(session).add_memory(user_id=user_id, content=text, source="explicit", limit=999)
        if pinned:
            await PersonalAiRepository(session).set_memory_pinned(user_id=user_id, memory_id=result.memory.id, pinned=True)
        await session.commit()
        return result.memory.id


# --- reading -------------------------------------------------------------------------------


async def test_every_endpoint_requires_a_session(env):
    for method, url in (
        ("GET", "/api/miniapp/personal"),
        ("POST", "/api/miniapp/personal/memory"),
        ("DELETE", "/api/miniapp/personal/memory/1"),
        ("POST", "/api/miniapp/personal/memory/1/pin"),
        ("PUT", "/api/miniapp/personal/settings"),
        ("PUT", "/api/miniapp/personal/model"),
        ("POST", "/api/miniapp/personal/forget-all"),
    ):
        response = await env.client.request(method, url, headers=JSON, content=b"{}")
        assert response.status_code == 401, url
        assert response.json()["ok"] is False and response.json()["message"]


async def test_new_user_overview_is_read_only_and_uses_free_limits(env):
    response = await env.client.get("/api/miniapp/personal", headers=env.as_user(1))

    body = response.json()
    assert response.status_code == 200 and body["ok"] is True
    assert response.headers["cache-control"] == "no-store"
    assert body["timezone"] == "UTC"
    assert body["subscription"]["tier"] == "free"
    assert body["subscription"]["active"] is False
    assert body["subscription"]["offer_available"] is True
    assert body["subscription"]["price_stars"] == 69
    assert body["subscription"]["purchase"] == {"command": "/premium", "bot_dm_url": "https://t.me/selara_test_bot"}
    assert body["quota"] == {
        "status": "ok", "unit": "request", "used": 3, "limit": 5, "remaining": 2,
        "reset_at": (NOW + timedelta(hours=5)).isoformat(), "exhausted": False,
    }
    assert body["memory"] == {"count": 0, "limit": 20, "items": []}
    assert body["profile"]["memory_enabled"] is True
    assert body["profile"]["auto_memory_enabled"] is False
    # Looking at the page must not create a profile row or spend quota.
    assert await _count(env, PersonalAiProfileModel) == 0
    assert env.access.reserved == 0


async def test_paid_user_sees_subscription_term_and_paid_limit(env):
    env.access.tier = AccessTier.PAID
    env.access.valid_until = datetime.now(timezone.utc) + timedelta(days=3, hours=1)

    body = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()

    assert body["subscription"]["tier"] == "paid" and body["subscription"]["active"] is True
    assert body["subscription"]["days_left"] == 4
    assert body["subscription"]["expiring_soon"] is True
    assert body["memory"]["limit"] == 200
    assert body["quota"]["limit"] == 150
    assert body["profile"]["auto_memory_available"] is True


async def test_auto_memory_is_not_available_when_the_switch_is_off_or_tier_is_free(env):
    free = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()
    assert free["profile"]["auto_memory_available"] is False

    env.access.tier = AccessTier.PAID
    env.state["config"] = _config(memory_auto_extract=False)
    off = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()
    assert off["profile"]["auto_memory_available"] is False


async def test_owner_is_reported_as_owner_internal(env):
    body = (await env.client.get("/api/miniapp/personal", headers=env.as_user(999))).json()

    assert body["subscription"]["tier"] == "owner_internal"
    assert body["subscription"]["owner_exempt"] is True
    assert body["memory"]["limit"] == 200


async def test_offer_is_hidden_until_a_price_is_configured(env):
    env.state["config"] = _config(price_stars=None)

    body = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()

    assert body["subscription"]["offer_available"] is False
    assert body["subscription"]["price_stars"] is None


async def test_access_failure_is_unavailable_not_free_and_blocks_writes(env):
    await _add_fact(env, 1, "я веган")
    env.access.fail = True

    body = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()
    assert body["subscription"]["state"] == "unavailable"
    assert body["subscription"]["tier"] is None
    assert body["quota"]["status"] == "unavailable"
    assert body["memory"]["limit"] is None
    assert [item["content"] for item in body["memory"]["items"]] == ["я веган"]

    refused = await env.client.post(
        "/api/miniapp/personal/memory", headers={**JSON, **env.as_user(1)}, json={"content": "новое"}
    )
    assert refused.status_code == 503
    assert await _count(env, PersonalAiMemoryModel, user_id=1) == 1


# --- memory --------------------------------------------------------------------------------


async def test_add_list_pin_and_delete_a_fact(env):
    headers = {**JSON, **env.as_user(1)}

    added = await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": "  я   веган "})
    assert added.status_code == 200 and added.json()["status"] == "added"
    item = added.json()["item"]
    assert item["content"] == "я веган" and item["pinned"] is False and item["source"] == "explicit"

    again = await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": "Я ВЕГАН"})
    assert again.status_code == 200 and again.json()["status"] == "duplicate"

    pinned = await env.client.post(
        f"/api/miniapp/personal/memory/{item['id']}/pin", headers=headers, json={"pinned": True}
    )
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    listed = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()["memory"]
    assert listed["count"] == 1 and listed["items"][0]["pinned"] is True

    deleted = await env.client.delete(f"/api/miniapp/personal/memory/{item['id']}", headers=env.as_user(1))
    assert deleted.status_code == 200
    assert await _count(env, PersonalAiMemoryModel, user_id=1) == 0


async def test_adding_respects_validation_limit_and_memory_switch(env):
    headers = {**JSON, **env.as_user(1)}
    env.state["config"] = _config(memory_free_limit=2, memory_paid_limit=5)

    assert (await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": "   "})).status_code == 422
    assert (await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": "x" * 301})).status_code == 422
    assert (await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": 5})).status_code == 422
    assert (await env.client.post("/api/miniapp/personal/memory", headers=headers, json={})).status_code == 422

    for text in ("один", "два"):
        assert (await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": text})).status_code == 200
    full = await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": "три"})
    assert full.status_code == 409 and "лимит" in full.json()["message"].lower()
    assert await _count(env, PersonalAiMemoryModel, user_id=1) == 2

    await env.client.put("/api/miniapp/personal/settings", headers=headers, json={"memory_enabled": False})
    off = await env.client.post("/api/miniapp/personal/memory", headers=headers, json={"content": "четыре"})
    assert off.status_code == 409 and "выключена" in off.json()["message"].lower()


async def test_memory_is_private_between_users(env):
    mine = await _add_fact(env, 1, "секрет первого")
    theirs = await _add_fact(env, 2, "секрет второго")

    seen_by_1 = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()["memory"]
    assert [i["content"] for i in seen_by_1["items"]] == ["секрет первого"]

    assert (await env.client.delete(f"/api/miniapp/personal/memory/{theirs}", headers=env.as_user(1))).status_code == 404
    assert (
        await env.client.post(
            f"/api/miniapp/personal/memory/{theirs}/pin", headers={**JSON, **env.as_user(1)}, json={"pinned": True}
        )
    ).status_code == 404
    async with env.factory() as session:
        row = await session.get(PersonalAiMemoryModel, theirs)
        assert row is not None and row.pinned is False
    assert mine != theirs


async def test_pin_requires_a_boolean(env):
    fact = await _add_fact(env, 1, "факт")
    response = await env.client.post(
        f"/api/miniapp/personal/memory/{fact}/pin", headers={**JSON, **env.as_user(1)}, json={"pinned": "yes"}
    )
    assert response.status_code == 422


# --- settings ------------------------------------------------------------------------------


async def test_settings_toggle_memory_and_auto_memory_and_start_extraction_from_now(env):
    headers = {**JSON, **env.as_user(1)}
    async with env.factory() as session:
        repo = PersonalAiRepository(session)
        await repo.get_or_create_profile(1)
        await repo.add_message(user_id=1, thread="assistant", role="user", content="старый текст")
        await repo.add_message(user_id=1, thread="assistant", role="user", content="ещё старее")
        await session.commit()
        last_id = max(m.id for m in await repo.recent_messages(user_id=1, thread="assistant", limit=10))

    response = await env.client.put("/api/miniapp/personal/settings", headers=headers, json={"auto_memory_enabled": True})

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["auto_memory_enabled"] is True and body["profile"]["memory_enabled"] is True
    async with env.factory() as session:
        stored = await PersonalAiRepository(session).get_profile(1)
        assert stored.auto_memory_enabled is True
        assert stored.memory_extract_cursor == last_id

    off = await env.client.put("/api/miniapp/personal/settings", headers=headers, json={"memory_enabled": False})
    assert off.json()["profile"]["memory_enabled"] is False


async def test_settings_reject_unknown_fields_wrong_types_and_empty_bodies(env):
    headers = {**JSON, **env.as_user(1)}
    for payload in ({}, {"memory_enabled": "yes"}, {"mode": "roleplay"}, {"memory_enabled": True, "display_name": "x"}):
        response = await env.client.put("/api/miniapp/personal/settings", headers=headers, json=payload)
        assert response.status_code == 422, payload
    assert await _count(env, PersonalAiProfileModel) == 0


async def test_auto_memory_cannot_be_enabled_while_memory_is_off(env):
    headers = {**JSON, **env.as_user(1)}
    await env.client.put("/api/miniapp/personal/settings", headers=headers, json={"memory_enabled": False})

    alone = await env.client.put("/api/miniapp/personal/settings", headers=headers, json={"auto_memory_enabled": True})
    both = await env.client.put(
        "/api/miniapp/personal/settings", headers=headers, json={"memory_enabled": False, "auto_memory_enabled": True}
    )
    assert alone.status_code == 422 and both.status_code == 422
    async with env.factory() as session:
        assert (await PersonalAiRepository(session).get_profile(1)).auto_memory_enabled is False

    together = await env.client.put(
        "/api/miniapp/personal/settings", headers=headers, json={"memory_enabled": True, "auto_memory_enabled": True}
    )
    assert together.status_code == 200 and together.json()["profile"]["auto_memory_enabled"] is True


async def test_oversized_body_is_refused_by_content_length_before_reading(env):
    response = await env.client.post(
        "/api/miniapp/personal/memory",
        headers={**JSON, **env.as_user(1), "content-length": "999999"},
        content=b'{"content": "x"}',
    )
    assert response.status_code == 413


async def test_mutations_require_a_json_content_type(env):
    for method, url in (
        ("POST", "/api/miniapp/personal/memory"),
        ("PUT", "/api/miniapp/personal/settings"),
        ("PUT", "/api/miniapp/personal/model"),
        ("POST", "/api/miniapp/personal/forget-all"),
    ):
        response = await env.client.request(
            method, url, headers={"content-type": "text/plain", **env.as_user(1)}, content=b'{"content": "x", "confirm": true}'
        )
        assert response.status_code == 415, url
    assert await _count(env, PersonalAiMemoryModel) == 0


async def test_oversized_bodies_are_refused(env):
    response = await env.client.post(
        "/api/miniapp/personal/memory", headers={**JSON, **env.as_user(1)}, content=b'{"content": "' + b"a" * 20000 + b'"}'
    )
    assert response.status_code == 413


# --- forget all ----------------------------------------------------------------------------


async def test_forget_all_needs_confirmation_and_only_erases_the_caller(env):
    await _add_fact(env, 1, "мой факт")
    await _add_fact(env, 2, "чужой факт")
    async with env.factory() as session:
        repo = PersonalAiRepository(session)
        await repo.add_message(user_id=1, thread="assistant", role="user", content="привет")
        await repo.add_message(user_id=2, thread="assistant", role="user", content="привет от второго")
        await session.commit()
    headers = {**JSON, **env.as_user(1)}

    not_confirmed = await env.client.post("/api/miniapp/personal/forget-all", headers=headers, json={})
    assert not_confirmed.status_code == 422
    loose = await env.client.post("/api/miniapp/personal/forget-all", headers=headers, json={"confirm": "true"})
    assert loose.status_code == 422
    assert await _count(env, PersonalAiMemoryModel, user_id=1) == 1

    done = await env.client.post("/api/miniapp/personal/forget-all", headers=headers, json={"confirm": True})

    assert done.status_code == 200
    assert done.json()["removed"] == {"memories": 1, "messages": 1, "summaries": 0, "profile": True}
    assert await _count(env, PersonalAiMemoryModel, user_id=1) == 0
    assert await _count(env, PersonalAiMessageModel, user_id=1) == 0
    assert await _count(env, PersonalAiProfileModel, user_id=1) == 0
    assert await _count(env, PersonalAiMemoryModel, user_id=2) == 1
    assert await _count(env, PersonalAiMessageModel, user_id=2) == 1
    assert await _count(env, PersonalAiProfileModel, user_id=2) == 1
    assert await _count(env, UserModel, telegram_user_id=1) == 1  # account and billing stay


async def test_forget_all_waits_for_a_running_turn_and_releases_its_lock(env):
    await _add_fact(env, 1, "факт")
    headers = {**JSON, **env.as_user(1)}
    personal_ai._inflight_users.add(1)
    try:
        busy = await env.client.post("/api/miniapp/personal/forget-all", headers=headers, json={"confirm": True})
        assert busy.status_code == 409
        assert await _count(env, PersonalAiMemoryModel, user_id=1) == 1
    finally:
        personal_ai._inflight_users.discard(1)

    done = await env.client.post("/api/miniapp/personal/forget-all", headers=headers, json={"confirm": True})
    assert done.status_code == 200
    assert 1 not in personal_ai._inflight_users


# --- model profile (PR 13) ----------------------------------------------------------------


def _ail_config():
    return _config(quota_mode="ail", ail_limits=ail_limits_from(10, 100))


async def test_overview_shows_profiles_without_physical_ids_and_locks_them_in_requests_mode(env):
    body = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()
    model = body["model"]
    assert model["selectable"] is False and model["quota_mode"] == "requests"
    assert (model["selected"], model["effective"], model["cost_ail"]) == ("basic", "basic", "1")
    options = {o["profile_key"]: o for o in model["options"]}
    assert options["analytics"] == {
        "profile_key": "analytics", "emoji": "🧠", "display_name": "Аналитик",
        "description": "Для сложного анализа", "ail_multiplier": "2.5", "available": True,
    }
    assert options["creative"]["available"] is False  # no model assigned
    assert "provider/" not in str(body)
    response = await env.client.put("/api/miniapp/personal/model", headers={**JSON, **env.as_user(1)},
                                    json={"profile_key": "analytics"})
    assert response.status_code == 409 and "AI Limits" in response.json()["message"]


async def test_model_choice_is_saved_in_the_same_field_as_ai(env):
    env.state["config"] = _ail_config()
    headers = {**JSON, **env.as_user(1)}
    response = await env.client.put("/api/miniapp/personal/model", headers=headers, json={"profile_key": "analytics"})
    assert response.status_code == 200 and response.json()["model"]["selected"] == "analytics"
    assert response.json()["model"]["cost_ail"] == "2.5"
    async with env.factory() as session:
        assert (await PersonalAiRepository(session).get_profile(1)).model_profile_key == "analytics"
    body = (await env.client.get("/api/miniapp/personal", headers=env.as_user(1))).json()
    assert body["model"]["selectable"] is True and body["model"]["effective"] == "analytics"


@pytest.mark.parametrize("payload", [
    {"profile_key": "creative"}, {"profile_key": "gpt-4o"}, {"profile_key": "provider/analytics-model"},
    {"profile_key": 1}, {"profile_key": "analytics", "model_id": "x"}, {},
])
async def test_forged_or_unavailable_profiles_are_rejected(env, payload):
    env.state["config"] = _ail_config()
    response = await env.client.put("/api/miniapp/personal/model", headers={**JSON, **env.as_user(1)}, json=payload)
    assert response.status_code in (409, 422) and response.json()["ok"] is False
    async with env.factory() as session:
        stored = await PersonalAiRepository(session).get_profile(1)
    assert stored is None or stored.model_profile_key == "basic"
