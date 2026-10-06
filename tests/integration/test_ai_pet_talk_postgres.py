"""Pet talk on PostgreSQL: the guest share under concurrency and the owner's pet_daily quota."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
    QuotaScope,
)
from selara.application.personal_config import StaticPersonalConfigProvider, config_from_settings
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pet_dialogue import AiPetDialogueRepository
from selara.infrastructure.db.ai_pets import AiPetService
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.models import AiPetMessageModel, UserEntitlementModel, UserModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.features import AiFeature

pytestmark = [pytest.mark.integration]

CHAT = -300700
OWNER = 701
GUESTS = tuple(range(800, 812))
NOW = datetime.now(timezone.utc)
DAY_START = NOW - timedelta(hours=1)


@pytest.fixture
async def factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        await SqlAlchemyActivityRepository(db).upsert_chat_settings(
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Pets"), values={"pets_enabled": True}
        )
        db.add_all(UserModel(telegram_user_id=user_id, first_name="U") for user_id in (OWNER, *GUESTS))
        await db.flush()
        db.add(
            UserEntitlementModel(
                user_id=OWNER, product_key=SELARA_PERSONAL_PRODUCT_KEY, status="active",
                valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=29),
            )
        )
        await db.commit()
        pet = await AiPetService(db).create_pet(
            owner=UserSnapshot(telegram_user_id=OWNER, username=None, first_name="O", last_name=None, is_bot=False),
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Pets"),
            species_raw="кот",
            name_raw="Мурка",
            now=NOW,
        )
        await db.commit()
    sessions.kw["info"] = {"pet_id": pet.id}
    yield sessions
    await engine.dispose()


async def test_concurrent_guests_never_exceed_their_daily_share(factory) -> None:
    pet_id = factory.kw["info"]["pet_id"]

    async def admit(user_id: int):
        async with factory() as db:
            result = await AiPetDialogueRepository(db).admit_talk(
                pet_id=pet_id, chat_id=CHAT, author_user_id=user_id, content="привет",
                idempotency_key=f"talk:{user_id}", telegram_message_id=None, day_start=DAY_START,
                guests_limit=5, guest_limit=1, now=NOW,
            )
            await db.commit()
            return result.status

    statuses = await asyncio.gather(*(admit(user_id) for user_id in GUESTS))
    assert statuses.count("ok") == 5
    assert statuses.count("guests_exhausted") == len(GUESTS) - 5
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AiPetMessageModel)) == 5


async def test_pet_talk_is_paid_from_the_owners_pet_pool(factory) -> None:
    settings = Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///", pet_talk_daily_limit=3,
                        pet_talk_guests_daily_limit=2, pet_talk_guest_daily_limit=1)
    config = StaticPersonalConfigProvider(config_from_settings(settings))
    service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(factory, config, pet_daily_limit=3),
        personal_config=config,
    )

    async def reserve(owner: int, key: str):
        return await service.reserve_feature_usage(
            feature=AiFeature.PET_TALK, chat_id=CHAT, chat_type="supergroup", chat_title="Pets",
            scope=QuotaScope.user(owner), actor_user_id=GUESTS[0], trigger="telegram_message",
            timezone_name="UTC", idempotency_key=key, source_message_id=None,
        )

    decisions = [await reserve(OWNER, f"pet:{index}") for index in range(4)]
    assert [decision.allowed for decision in decisions] == [True, True, True, False]
    assert decisions[0].access_tier == AccessTier.PAID and decisions[0].quota_limit == 3
    assert decisions[-1].reason == AccessReason.QUOTA_EXHAUSTED

    # An owner without Selara Personal: the free pet pool is zero, the pet cannot talk.
    no_personal = await reserve(GUESTS[1], "pet:free")
    assert not no_personal.allowed and no_personal.access_tier == AccessTier.FREE and no_personal.quota_limit == 0


async def test_bot_owner_pet_talks_without_personal_and_nobody_else_does(factory) -> None:
    owner_admin = GUESTS[2]
    other = GUESTS[3]
    now = NOW

    async with factory() as db:
        assert await AiPetService(db, admin_user_id=owner_admin).has_active_personal(user_id=owner_admin, now=now)
        assert not await AiPetService(db, admin_user_id=owner_admin).has_active_personal(user_id=other, now=now)
        assert not await AiPetService(db).has_active_personal(user_id=owner_admin, now=now)

    settings = Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///", pet_talk_daily_limit=3)
    config = StaticPersonalConfigProvider(config_from_settings(settings))
    service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(factory, config, pet_daily_limit=3),
        personal_config=config,
    )

    async def reserve(owner: int, exempt: bool):
        return await service.reserve_feature_usage(
            feature=AiFeature.PET_TALK, chat_id=CHAT, chat_type="supergroup", chat_title="Pets",
            scope=QuotaScope.user(owner), owner_exempt=exempt, actor_user_id=GUESTS[0],
            trigger="telegram_message", timezone_name="UTC", idempotency_key=f"owner-pet:{owner}:{exempt}",
            source_message_id=None,
        )

    allowed = await reserve(owner_admin, True)
    assert allowed.allowed and allowed.access_tier == AccessTier.OWNER_INTERNAL
    denied = await reserve(other, False)
    assert not denied.allowed and denied.access_tier == AccessTier.FREE
