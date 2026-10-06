"""AI pets on PostgreSQL: economy purchases, row locks and idempotency under concurrency."""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.ai_pets import mechanics as m
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.domain.economy_entities import EconomyScope
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pets import AiPetService, PetDomainError
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    AiPetEventModel,
    AiPetItemModel,
    AiPetModel,
    EconomyAccountModel,
    EconomyLedgerModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 6)
CHAT = -100500
OWNER = 501
GUESTS = tuple(range(600, 608))
GLOBAL = EconomyScope(scope_id="global", scope_type="global", chat_id=None)


def _user(user_id: int) -> UserSnapshot:
    return UserSnapshot(telegram_user_id=user_id, username=None, first_name=f"U{user_id}", last_name=None, is_bot=False)


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
                user_id=OWNER,
                product_key=SELARA_PERSONAL_PRODUCT_KEY,
                status="active",
                valid_from=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(days=29),
            )
        )
        db.add_all(
            [
                AiPetItemModel(code="dry_food", title="Сухой корм", kind="food", price=40, effects={"satiety": 15, "xp": 2}, sort_order=1),
                AiPetItemModel(code="ball", title="Мячик", kind="toy", price=120, effects={"mood": 10, "xp": 5}, sort_order=2),
                AiPetItemModel(code="puzzle", title="Головоломка", kind="toy", price=400, effects={"mood": 15}, min_level=5, sort_order=3),
                AiPetItemModel(code="broken", title="Сломанный", kind="food", price=1, effects={"satiety": "a lot"}, sort_order=0),
            ]
        )
        await db.commit()
    yield sessions
    await engine.dispose()


async def _create_pet(factory, name: str = "Мурка") -> int:
    async with factory() as db:
        pet = await AiPetService(db).create_pet(
            owner=_user(OWNER),
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Pets"),
            species_raw="кот",
            name_raw=name,
            now=NOW,
        )
        await db.commit()
        return pet.id


async def _fund(factory, user_id: int, amount: int) -> None:
    async with factory() as db:
        repo = SqlAlchemyEconomyRepository(db)
        account, _ = await repo.get_or_create_account(scope=GLOBAL, user_id=user_id)
        await repo.add_balance(account_id=account.id, delta=amount)
        await db.commit()


async def _balance(factory, user_id: int) -> int:
    async with factory() as db:
        return int(
            await db.scalar(
                select(EconomyAccountModel.balance).where(
                    EconomyAccountModel.scope_id == "global", EconomyAccountModel.user_id == user_id
                )
            )
        )


async def _use_item(factory, pet_id: int, *, actor: int, key: str, code: str | None, kind: str | None = None):
    async with factory() as db:
        result = await AiPetService(db).use_item(
            pet_id=pet_id, chat_id=CHAT, actor=_user(actor), item_code=code, kind=kind,
            idempotency_key=key, economy_mode="global", today=TODAY, now=NOW,
        )
        await db.commit()
        return result


async def test_feeding_buys_the_cheapest_valid_food_and_debits_the_economy(factory) -> None:
    pet_id = await _create_pet(factory)
    guest = GUESTS[0]
    await _fund(factory, guest, 100)
    result = await _use_item(factory, pet_id, actor=guest, key="feed1", code=None, kind="food")
    assert result.status == "ok"
    assert result.item.code == "dry_food"  # the cheaper but broken row is never sold
    assert (result.new_balance, result.applied["satiety"]) == (60, 15)
    assert (await _use_item(factory, pet_id, actor=guest, key="feed1", code=None, kind="food")).status == "duplicate"
    assert await _balance(factory, guest) == 60
    async with factory() as db:
        ledger = (await db.scalars(select(EconomyLedgerModel).where(EconomyLedgerModel.reason == "ai_pet_item"))).one()
        assert (ledger.direction, ledger.amount, ledger.meta_json["item"]) == ("out", 40, "dry_food")
        event = (await db.scalars(select(AiPetEventModel).where(AiPetEventModel.idempotency_key == "feed1"))).one()
        assert (event.effects["price"], event.effects["item"]) == (40, "dry_food")


async def test_purchase_refusals_do_not_charge(factory) -> None:
    pet_id = await _create_pet(factory)
    guest = GUESTS[0]
    await _fund(factory, guest, 50)
    assert (await _use_item(factory, pet_id, actor=guest, key="r1", code="ball")).status == "insufficient_funds"
    assert (await _use_item(factory, pet_id, actor=guest, key="r2", code="puzzle")).status == "level_too_low"
    assert (await _use_item(factory, pet_id, actor=guest, key="r3", code="broken")).status == "item_unavailable"
    async with factory() as db:
        row = await db.get(AiPetModel, pet_id)
        row.satiety = 100
        await db.commit()
    assert (await _use_item(factory, pet_id, actor=guest, key="r4", code="dry_food")).status == "blocked"
    assert await _balance(factory, guest) == 50


async def test_replayed_update_is_applied_once_under_concurrency(factory) -> None:
    pet_id = await _create_pet(factory)
    guest = GUESTS[0]
    await _fund(factory, guest, 1000)
    results = await asyncio.gather(
        *(_use_item(factory, pet_id, actor=guest, key="same-update", code="dry_food") for _ in range(6))
    )
    assert sorted(result.status for result in results) == ["duplicate"] * 5 + ["ok"]
    assert await _balance(factory, guest) == 960
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(EconomyLedgerModel).where(EconomyLedgerModel.reason == "ai_pet_item")) == 1


async def test_concurrent_purchases_never_overdraw(factory) -> None:
    pet_id = await _create_pet(factory)
    guest = GUESTS[0]
    await _fund(factory, guest, 130)  # enough for food (40) or the ball (120), not both
    results = await asyncio.gather(
        _use_item(factory, pet_id, actor=guest, key="p-food", code="dry_food"),
        _use_item(factory, pet_id, actor=guest, key="p-ball", code="ball"),
    )
    statuses = sorted(result.status for result in results)
    assert statuses == ["insufficient_funds", "ok"]
    spent = next(result.item.price for result in results if result.status == "ok")
    assert await _balance(factory, guest) == 130 - spent


async def test_concurrent_actions_from_many_people_lose_no_updates(factory) -> None:
    pet_id = await _create_pet(factory)

    async def pat(user_id: int):
        async with factory() as db:
            result = await AiPetService(db).perform_action(
                pet_id=pet_id, chat_id=CHAT, actor_user_id=user_id, action_key="pat",
                idempotency_key=f"pat:{user_id}", today=TODAY, now=NOW,
            )
            await db.commit()
            return result

    results = await asyncio.gather(*(pat(user_id) for user_id in GUESTS))
    assert all(result.status == "ok" for result in results)
    async with factory() as db:
        row = await db.get(AiPetModel, pet_id)
        assert row.xp == m.ACTIONS["pat"].xp * len(GUESTS)
        assert row.version == len(GUESTS)


async def test_concurrent_creation_keeps_one_pet_per_owner(factory) -> None:
    async def create(name: str):
        try:
            return await _create_pet(factory, name)
        except PetDomainError:
            return None

    created = await asyncio.gather(*(create(name) for name in ("Мурка", "Барсик", "Рыжик")))
    assert sum(1 for pet_id in created if pet_id is not None) == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AiPetModel).where(AiPetModel.owner_user_id == OWNER)) == 1
