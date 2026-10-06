"""Pet bag purchases on PostgreSQL: economy debit, gifts, caps and concurrent buys."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.ai_pets import mechanics as m
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.domain.economy_entities import EconomyScope
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pets import AiPetService
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    AiPetEventModel,
    AiPetInventoryModel,
    AiPetItemModel,
    EconomyAccountModel,
    EconomyLedgerModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
CHAT = -400900
OWNER, GUEST = 901, 902
GLOBAL = EconomyScope(scope_id="global", scope_type="global", chat_id=None)


def _user(user_id: int) -> UserSnapshot:
    return UserSnapshot(telegram_user_id=user_id, username=None, first_name=f"U{user_id}", last_name=None, is_bot=False)


@pytest.fixture
async def setup():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await SqlAlchemyActivityRepository(db).upsert_chat_settings(
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Pets"), values={"pets_enabled": True}
        )
        db.add_all(UserModel(telegram_user_id=user_id, first_name="U") for user_id in (OWNER, GUEST))
        await db.flush()
        db.add(
            UserEntitlementModel(
                user_id=OWNER, product_key=SELARA_PERSONAL_PRODUCT_KEY, status="active",
                valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=29),
            )
        )
        db.add_all(
            [
                AiPetItemModel(code="dry_food", title="Сухой корм", kind="food", price=40, effects={"satiety": 15}, sort_order=1),
                AiPetItemModel(code="bow", title="Бантик", kind="cosmetic", price=150, effects={}, slot="neck", sort_order=2),
            ]
        )
        await db.commit()
        pet = await AiPetService(db).create_pet(
            owner=_user(OWNER), chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Pets"),
            species_raw="кот", name_raw="Мурка", now=NOW,
        )
        await db.commit()
        for user_id in (OWNER, GUEST):
            repo = SqlAlchemyEconomyRepository(db)
            account, _ = await repo.get_or_create_account(scope=GLOBAL, user_id=user_id)
            await repo.add_balance(account_id=account.id, delta=1000)
        await db.commit()
    yield factory, pet.id
    await engine.dispose()


async def _buy(factory, pet_id: int, *, actor: int, code: str, key: str):
    async with factory() as db:
        result = await AiPetService(db).buy_to_bag(
            pet_id=pet_id, chat_id=CHAT, actor=_user(actor), item_code=code, idempotency_key=key,
            economy_mode="global", now=NOW,
        )
        await db.commit()
        return result


async def _balance(factory, user_id: int) -> int:
    async with factory() as db:
        return int(
            await db.scalar(
                select(EconomyAccountModel.balance).where(
                    EconomyAccountModel.scope_id == "global", EconomyAccountModel.user_id == user_id
                )
            )
        )


async def test_a_guest_gift_lands_in_the_pets_bag_and_is_charged_to_the_guest(setup) -> None:
    factory, pet_id = setup
    result = await _buy(factory, pet_id, actor=GUEST, code="bow", key="gift")
    assert (result.status, result.new_balance) == ("ok", 850)
    assert await _balance(factory, OWNER) == 1000
    async with factory() as db:
        owned = await db.get(AiPetInventoryModel, {"pet_id": pet_id, "item_code": "bow"})
        assert (owned.quantity, owned.equipped) == (1, False)
        ledger = (await db.scalars(select(EconomyLedgerModel).where(EconomyLedgerModel.reason == "ai_pet_bag"))).one()
        assert ledger.amount == 150
        event = (await db.scalars(select(AiPetEventModel).where(AiPetEventModel.event_type == "bag_add"))).one()
        assert event.effects == {"item": "bow", "price": 150, "gift": True}
    # A cosmetic is owned once: a second purchase is refused without charging.
    assert (await _buy(factory, pet_id, actor=OWNER, code="bow", key="again")).status == "blocked"
    assert await _balance(factory, OWNER) == 1000


async def test_food_stacks_up_to_the_limit(setup) -> None:
    factory, pet_id = setup
    async with factory() as db:
        db.add(AiPetInventoryModel(pet_id=pet_id, item_code="dry_food", quantity=m.BAG_STACK_LIMIT - 1, acquired_at=NOW))
        await db.commit()
    assert (await _buy(factory, pet_id, actor=OWNER, code="dry_food", key="f1")).status == "ok"
    assert (await _buy(factory, pet_id, actor=OWNER, code="dry_food", key="f2")).status == "blocked"
    assert await _balance(factory, OWNER) == 960


async def test_concurrent_purchases_of_one_cosmetic_charge_once(setup) -> None:
    factory, pet_id = setup
    results = await asyncio.gather(*(_buy(factory, pet_id, actor=GUEST, code="bow", key=f"race{i}") for i in range(5)))
    assert sorted(result.status for result in results) == ["blocked"] * 4 + ["ok"]
    assert await _balance(factory, GUEST) == 850
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(EconomyLedgerModel).where(EconomyLedgerModel.reason == "ai_pet_bag")) == 1
