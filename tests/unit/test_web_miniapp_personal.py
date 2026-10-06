"""Mini App «Моя Selara»: profile, memory and subscription status of the session user."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureUsageSummary,
    PersonalQuotaLimits,
)
from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider
from selara.core.config import Settings
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import PersonalAiMemoryModel, UserModel
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository
from selara.infrastructure.llm.features import AiFeature
from selara.web.miniapp_personal import PersonalAccess, build_access_resolver, build_miniapp_personal_router

URL = "/api/miniapp/personal"
FREE = PersonalAccess(tier="free", quota_limit=5, quota_used=2, quota_remaining=3, unlimited=False)
PAID = PersonalAccess(
    tier="paid",
    valid_until=datetime(2026, 11, 1, tzinfo=timezone.utc),
    quota_limit=150,
    quota_used=10,
    quota_remaining=140,
)


def _settings(**extra) -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "WEB_AUTH_SECRET": "test-secret",
            "WEB_BASE_URL": "http://testserver",
            "ADMIN_USER_ID": 77,
            **extra,
        }
    )


def _config(**extra) -> PersonalConfig:
    values = {
        "price_stars": 69,
        "duration_days": 30,
        "limits": PersonalQuotaLimits(free_daily=5, paid_daily=150),
        "memory_free_limit": 3,
        "memory_paid_limit": 6,
        "memory_auto_extract": True,
        "memory_extract_every": 10,
    }
    values.update(extra)
    return PersonalConfig(**values)


class _Harness:
    def __init__(self, factory, app, access_by_user) -> None:
        self.factory = factory
        self.app = app
        self.access_by_user = access_by_user
        self.current_user_id: int | None = 1

    def client(self, user_id: int | None = 1) -> httpx.AsyncClient:
        self.current_user_id = user_id
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")


@pytest_asyncio.fixture
async def harness():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([UserModel(telegram_user_id=uid, is_bot=False) for uid in (1, 2)])
        await session.commit()

    access_by_user: dict[int, PersonalAccess] = {}
    holder: dict[str, _Harness] = {}

    async def load_user(_session, _request):
        user_id = holder["h"].current_user_id
        if user_id is None:
            return None
        return UserSnapshot(telegram_user_id=user_id, username=None, first_name="U", last_name=None, is_bot=False)

    async def resolve_access(user_id: int) -> PersonalAccess:
        return access_by_user.get(user_id, FREE)

    app = FastAPI()
    app.include_router(
        build_miniapp_personal_router(
            settings=_settings(),
            session_factory=factory,
            load_user=load_user,
            personal_config=StaticPersonalConfigProvider(_config()),
            resolve_access=resolve_access,
            bot_url="https://t.me/selara_test_bot",
        )
    )
    h = _Harness(factory, app, access_by_user)
    holder["h"] = h
    yield h
    await engine.dispose()


async def _memories(h: _Harness, user_id: int) -> list[str]:
    async with h.factory() as session:
        return [m.content for m in await PersonalAiRepository(session).list_memories(user_id=user_id)]


# --- session and isolation ---------------------------------------------------------------------


async def test_every_route_requires_a_miniapp_session(harness):
    async with harness.client(None) as client:
        calls = [
            client.get(URL),
            client.patch(f"{URL}/profile", json={"revision": 0}),
            client.get(f"{URL}/memories"),
            client.post(f"{URL}/memories", json={"content": "x"}),
            client.patch(f"{URL}/memories/1", json={"pinned": True}),
            client.delete(f"{URL}/memories/1"),
        ]
        statuses = [(await call).status_code for call in calls]
    assert statuses == [401] * 6


async def test_mutations_accept_json_only(harness):
    async with harness.client() as client:
        profile = await client.patch(f"{URL}/profile", content="revision=0", headers={"content-type": "text/plain"})
        memory = await client.post(
            f"{URL}/memories", content="content=hi", headers={"content-type": "application/x-www-form-urlencoded"}
        )
    assert profile.status_code == 415
    assert memory.status_code == 415


async def test_user_id_in_the_request_is_ignored(harness):
    async with harness.client(1) as client:
        await client.post(f"{URL}/memories", json={"content": "факт первого", "user_id": 2})
        await client.get(f"{URL}/memories?user_id=2")
    assert await _memories(harness, 1) == ["факт первого"]
    assert await _memories(harness, 2) == []


async def test_users_cannot_read_pin_or_delete_each_others_facts(harness):
    async with harness.client(1) as client:
        created = (await client.post(f"{URL}/memories", json={"content": "секрет первого"})).json()["item"]
    async with harness.client(2) as client:
        listing = (await client.get(f"{URL}/memories")).json()
        pin = await client.patch(f"{URL}/memories/{created['id']}", json={"pinned": True})
        delete = await client.delete(f"{URL}/memories/{created['id']}")
    assert listing["items"] == [] and listing["count"] == 0
    assert pin.status_code == 404 and delete.status_code == 404
    assert await _memories(harness, 1) == ["секрет первого"]
    async with harness.factory() as session:
        row = (await PersonalAiRepository(session).list_memories(user_id=1))[0]
        assert row.pinned is False


# --- overview --------------------------------------------------------------------------------


async def test_overview_for_a_free_user(harness):
    async with harness.client() as client:
        response = await client.get(URL)

    body = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert body["profile"]["display_name"] == "Selara"
    assert body["profile"]["revision"] == 0
    assert body["profile"]["memory_enabled"] is True and body["profile"]["auto_memory_enabled"] is False
    keys = [item["key"] for item in body["options"]["presets"]]
    assert keys[0] == "assistant" and keys[-1] == "custom"
    subscription = body["subscription"]
    assert subscription["tier"] == "free" and subscription["valid_until"] is None
    assert subscription["quota"] == {"limit": 5, "used": 2, "remaining": 3, "reset_at": None, "unlimited": False}
    assert subscription["price_stars"] == 69 and subscription["purchase_command"] == "/premium"
    assert subscription["bot_url"] == "https://t.me/selara_test_bot"
    assert body["memory"] == {
        "count": 0,
        "limit": 3,
        "max_length": 300,
        "auto_extract_enabled_by_admin": True,
        "auto_extract_available": False,
    }


async def test_overview_for_a_paid_user_has_the_paid_limit_and_auto_memory(harness):
    harness.access_by_user[1] = PAID
    async with harness.client() as client:
        body = (await client.get(URL)).json()

    assert body["subscription"]["tier"] == "paid"
    assert body["subscription"]["valid_until"] == "2026-11-01T00:00:00+00:00"
    assert body["memory"]["limit"] == 6 and body["memory"]["auto_extract_available"] is True


async def test_unresolved_access_is_reported_and_never_unlocks_anything(harness):
    harness.access_by_user[1] = PersonalAccess(available=False)
    async with harness.client() as client:
        body = (await client.get(URL)).json()
        add = await client.post(f"{URL}/memories", json={"content": "факт"})
        auto = await client.patch(f"{URL}/profile", json={"revision": 0, "auto_memory_enabled": True})

    assert body["subscription"]["available"] is False and body["subscription"]["tier"] is None
    assert body["memory"]["limit"] is None and body["memory"]["auto_extract_available"] is False
    assert add.status_code == 503 and add.json()["code"] == "access_unavailable"
    assert auto.status_code == 409
    assert await _memories(harness, 1) == []


# --- profile -----------------------------------------------------------------------------------


async def test_profile_update_applies_changes_and_bumps_the_revision(harness):
    async with harness.client() as client:
        response = await client.patch(
            f"{URL}/profile",
            json={
                "revision": 0,
                "display_name": "  Мира  ",
                "character_preset": "sarcastic",
                "address_form": "Илья",
                "formality": "vy",
                "reply_length": "long",
                "emoji_enabled": False,
                "mode": "roleplay",
                "memory_enabled": False,
            },
        )
        again = await client.get(URL)

    profile = response.json()["profile"]
    assert response.status_code == 200
    assert profile["display_name"] == "Мира" and profile["character_preset"] == "sarcastic"
    assert profile["address_form"] == "Илья" and profile["formality"] == "vy"
    assert profile["reply_length"] == "long" and profile["emoji_enabled"] is False
    assert profile["mode"] == "roleplay" and profile["memory_enabled"] is False
    assert profile["revision"] == 1
    assert again.json()["profile"] == profile


async def test_a_stale_revision_is_a_conflict_that_returns_the_current_profile(harness):
    async with harness.client() as client:
        first = await client.patch(f"{URL}/profile", json={"revision": 0, "display_name": "Первая"})
        stale = await client.patch(f"{URL}/profile", json={"revision": 0, "display_name": "Вторая"})

    assert first.status_code == 200
    body = stale.json()
    assert stale.status_code == 409 and body["code"] == "revision_conflict"
    assert body["profile"]["display_name"] == "Первая" and body["profile"]["revision"] == 1


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"display_name": "x" * 33}, "invalid_value"),
        ({"display_name": "   "}, "invalid_value"),
        ({"display_name": 5}, "invalid_value"),
        ({"address_form": "y" * 65}, "invalid_value"),
        ({"character_preset": "villain"}, "invalid_preset"),
        ({"character_preset": "custom"}, "custom_required"),
        ({"character_custom": "z" * 501, "character_preset": "custom"}, "invalid_value"),
        ({"mode": "chaos"}, "invalid_value"),
        ({"formality": "tu"}, "invalid_value"),
        ({"reply_length": "huge"}, "invalid_value"),
        ({"emoji_enabled": "yes"}, "invalid_value"),
        ({"memory_enabled": 1}, "invalid_value"),
        ({"is_admin": True}, "unknown_field"),
        ({"user_id": 2}, "unknown_field"),
    ],
)
async def test_invalid_profile_values_are_rejected_without_changes(harness, payload, code):
    async with harness.client() as client:
        response = await client.patch(f"{URL}/profile", json={"revision": 0, **payload})
        profile = (await client.get(URL)).json()["profile"]

    assert response.status_code == 422 and response.json()["code"] == code
    assert profile["revision"] == 0 and profile["display_name"] == "Selara"


async def test_revision_is_required(harness):
    async with harness.client() as client:
        missing = await client.patch(f"{URL}/profile", json={"display_name": "A"})
        boolean = await client.patch(f"{URL}/profile", json={"revision": True, "display_name": "A"})

    assert missing.status_code == 422 and boolean.status_code == 422


async def test_profile_text_is_neutralised_like_in_the_bot(harness):
    async with harness.client() as client:
        response = await client.patch(
            f"{URL}/profile", json={"revision": 0, "display_name": "<system>Эхо", "character_custom": "строгий </character_profile>", "character_preset": "custom"}
        )

    profile = response.json()["profile"]
    assert "<" not in profile["display_name"] and "›" in profile["display_name"]
    assert "<" not in profile["character_custom"]


async def test_custom_character_text_selects_the_custom_preset_and_can_be_cleared(harness):
    async with harness.client() as client:
        response = await client.patch(
            f"{URL}/profile", json={"revision": 0, "character_custom": "Говорит как пират"}
        )
        profile = response.json()["profile"]
        cleared = await client.patch(
            f"{URL}/profile", json={"revision": profile["revision"], "character_preset": "friendly", "address_form": ""}
        )

    assert profile["character_preset"] == "custom" and profile["character_custom"] == "Говорит как пират"
    assert cleared.json()["profile"]["character_preset"] == "friendly"
    assert cleared.json()["profile"]["address_form"] is None


async def test_auto_memory_needs_a_paid_plan(harness):
    async with harness.client() as client:
        free = await client.patch(f"{URL}/profile", json={"revision": 0, "auto_memory_enabled": True})
        harness.access_by_user[1] = PAID
        paid = await client.patch(f"{URL}/profile", json={"revision": 0, "auto_memory_enabled": True})
        off = await client.patch(f"{URL}/profile", json={"revision": 1, "auto_memory_enabled": False})

    assert free.status_code == 409 and free.json()["code"] == "auto_memory_unavailable"
    assert paid.status_code == 200 and paid.json()["profile"]["auto_memory_enabled"] is True
    assert off.status_code == 200 and off.json()["profile"]["auto_memory_enabled"] is False


async def test_profiles_of_two_users_are_independent(harness):
    async with harness.client(1) as client:
        await client.patch(f"{URL}/profile", json={"revision": 0, "display_name": "Первая"})
    async with harness.client(2) as client:
        body = (await client.get(URL)).json()
    assert body["profile"]["display_name"] == "Selara" and body["profile"]["revision"] == 0


# --- memory -------------------------------------------------------------------------------------


async def test_add_list_pin_and_delete_a_fact(harness):
    async with harness.client() as client:
        added = await client.post(f"{URL}/memories", json={"content": "  Я   веган \n"})
        item = added.json()["item"]
        pinned = await client.patch(f"{URL}/memories/{item['id']}", json={"pinned": True})
        listing = (await client.get(f"{URL}/memories")).json()
        removed = await client.delete(f"{URL}/memories/{item['id']}")
        after = (await client.get(f"{URL}/memories")).json()

    assert added.status_code == 201 and item["content"] == "Я веган"
    assert item["source"] == "explicit" and item["pinned"] is False and added.json()["count"] == 1
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    assert [(m["content"], m["pinned"]) for m in listing["items"]] == [("Я веган", True)]
    assert listing["count"] == 1 and listing["limit"] == 3 and listing["max_length"] == 300
    assert removed.status_code == 200 and removed.json()["count"] == 0
    assert after["items"] == []


async def test_a_duplicate_is_refused_case_insensitively(harness):
    async with harness.client() as client:
        await client.post(f"{URL}/memories", json={"content": "Живу в Казани"})
        again = await client.post(f"{URL}/memories", json={"content": "живу в КАЗАНИ"})

    assert again.status_code == 409 and again.json()["code"] == "duplicate"
    assert await _memories(harness, 1) == ["Живу в Казани"]


async def test_the_limit_is_enforced_per_tier_and_nothing_is_evicted(harness):
    async with harness.client() as client:
        for number in range(3):
            assert (await client.post(f"{URL}/memories", json={"content": f"факт {number}"})).status_code == 201
        blocked = await client.post(f"{URL}/memories", json={"content": "лишний"})
        harness.access_by_user[1] = PAID
        allowed = await client.post(f"{URL}/memories", json={"content": "лишний"})

    assert blocked.status_code == 409 and blocked.json()["code"] == "limit_reached" and blocked.json()["limit"] == 3
    assert allowed.status_code == 201
    assert await _memories(harness, 1) == ["факт 0", "факт 1", "факт 2", "лишний"]


@pytest.mark.parametrize("content", ["", "   ", "я" * 301, 5, None])
async def test_invalid_facts_are_rejected(harness, content):
    async with harness.client() as client:
        response = await client.post(f"{URL}/memories", json={"content": content})
    assert response.status_code == 422 and response.json()["code"] == "invalid_value"
    assert await _memories(harness, 1) == []


async def test_a_fact_of_exactly_the_maximum_length_is_stored(harness):
    async with harness.client() as client:
        response = await client.post(f"{URL}/memories", json={"content": "я" * 300})
    assert response.status_code == 201


async def test_facts_are_not_stored_while_memory_is_switched_off(harness):
    async with harness.client() as client:
        await client.patch(f"{URL}/profile", json={"revision": 0, "memory_enabled": False})
        response = await client.post(f"{URL}/memories", json={"content": "факт"})
        listing = (await client.get(f"{URL}/memories")).json()

    assert response.status_code == 409 and response.json()["code"] == "memory_disabled"
    assert listing["memory_enabled"] is False


async def test_pin_validation_and_missing_facts(harness):
    async with harness.client() as client:
        bad = await client.patch(f"{URL}/memories/1", json={"pinned": "yes"})
        missing_pin = await client.patch(f"{URL}/memories/999", json={"pinned": True})
        missing_delete = await client.delete(f"{URL}/memories/999")

    assert bad.status_code == 422
    assert missing_pin.status_code == 404 and missing_delete.status_code == 404


async def test_memory_rows_survive_a_profile_change(harness):
    async with harness.client() as client:
        await client.post(f"{URL}/memories", json={"content": "я веган"})
        await client.patch(f"{URL}/profile", json={"revision": 0, "mode": "roleplay"})
    assert await _memories(harness, 1) == ["я веган"]
    async with harness.factory() as session:
        assert await session.get(PersonalAiMemoryModel, 1) is not None


# --- access resolver ---------------------------------------------------------------------------


def _decision(tier: AccessTier, *, valid_until=None, reason=None, owner=False) -> FeatureAccessDecision:
    return FeatureAccessDecision(
        allowed=True,
        feature=AiFeature.PERSONAL_CHAT,
        scope_type="user",
        scope_id="1",
        access_tier=tier,
        quota_limit=None,
        quota_used=None,
        quota_remaining=None,
        period_start=None,
        period_end=None,
        reason=reason,
        owner_exempt=owner,
        entitlement_valid_until=valid_until,
    )


def _summary(tier: AccessTier, *, limit, used, unlimited=False) -> FeatureUsageSummary:
    reset = datetime(2026, 10, 7, tzinfo=timezone.utc)
    return FeatureUsageSummary(
        feature=AiFeature.PERSONAL_CHAT,
        scope_type="user",
        scope_id="1",
        access_tier=tier,
        quota_limit=limit,
        quota_used=used,
        quota_remaining=None if limit is None else limit - used,
        period_start=None,
        period_end=None,
        reset_at=None if unlimited else reset,
        unlimited=unlimited,
        owner_exempt=unlimited,
        policy_key=None,
    )


class _Service:
    def __init__(self, decision=None, summary=None, error=None) -> None:
        self.decision, self.summary, self.error = decision, summary, error
        self.calls: list[tuple[str, dict]] = []

    async def resolve_feature_access(self, **kwargs):
        self.calls.append(("access", kwargs))
        if self.error:
            raise self.error
        return self.decision

    async def get_usage_summary(self, **kwargs):
        self.calls.append(("usage", kwargs))
        return self.summary


def _resolver(service):
    return build_access_resolver(
        settings=_settings(), session_factory=None, personal_config=StaticPersonalConfigProvider(_config()), service=service
    )


async def test_resolver_reports_a_paid_subscription_with_its_end_date():
    until = datetime.now(timezone.utc) + timedelta(days=12)
    service = _Service(_decision(AccessTier.PAID, valid_until=until), _summary(AccessTier.PAID, limit=150, used=7))

    access = await _resolver(service)(5)

    assert (access.tier, access.available, access.paid) == ("paid", True, True)
    assert access.valid_until == until
    assert (access.quota_limit, access.quota_used, access.quota_remaining) == (150, 7, 143)
    kinds = {name: kwargs for name, kwargs in service.calls}
    assert kinds["access"]["scope"].scope_id == 5 and kinds["usage"]["scope"].scope_id == 5
    assert kinds["access"]["owner_exempt"] is False and kinds["usage"]["feature"] == AiFeature.PERSONAL_CHAT


async def test_resolver_reports_the_free_tier_without_an_end_date():
    until = datetime.now(timezone.utc) - timedelta(days=1)
    service = _Service(
        _decision(AccessTier.FREE, valid_until=until, reason=AccessReason.ACCESS_REQUIRED),
        _summary(AccessTier.FREE, limit=5, used=5),
    )

    access = await _resolver(service)(5)

    assert (access.tier, access.paid, access.valid_until) == ("free", False, None)
    assert access.quota_remaining == 0


async def test_resolver_treats_the_bot_owner_as_unlimited():
    service = _Service(
        _decision(AccessTier.OWNER_INTERNAL, owner=True),
        _summary(AccessTier.OWNER_INTERNAL, limit=None, used=0, unlimited=True),
    )

    access = await _resolver(service)(77)

    assert (access.tier, access.paid, access.unlimited) == ("owner", True, True)
    assert {name: kwargs["owner_exempt"] for name, kwargs in service.calls} == {"access": True, "usage": True}


async def test_resolver_fails_closed_when_access_cannot_be_resolved():
    unavailable = _Service(_decision(AccessTier.FREE, reason=AccessReason.ACCESS_UNAVAILABLE), None)
    broken = _Service(error=RuntimeError("db is down"))

    for service in (unavailable, broken):
        access = await _resolver(service)(5)
        assert access.available is False and access.paid is False
    assert [name for name, _ in unavailable.calls] == ["access"]

