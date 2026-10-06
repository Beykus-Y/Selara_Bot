from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.ai_pets import dialogue as d
from selara.application.ai_pets import mechanics as m
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pets import AiPetService, BagEntry, CatalogItem, PetDomainError
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    AiPetInventoryModel,
    AiPetItemModel,
    AiPetModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers import ai_pets

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
CHAT = -4001
OWNER, GUEST = 51, 52


def test_cosmetics_must_have_no_effects() -> None:
    assert m.parse_item_effects({}, kind="cosmetic") == m.ItemEffects()
    assert m.parse_item_effects({"mood": 5}, kind="cosmetic") is None
    assert m.parse_item_effects("{}", kind="cosmetic") is None


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await SqlAlchemyActivityRepository(session).upsert_chat_settings(
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="C"), values={"pets_enabled": True}
        )
        session.add_all(UserModel(telegram_user_id=user_id, first_name="U") for user_id in (OWNER, GUEST))
        await session.flush()
        session.add(
            UserEntitlementModel(
                user_id=OWNER, product_key=SELARA_PERSONAL_PRODUCT_KEY, status="active",
                valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=29),
            )
        )
        session.add_all(
            [
                AiPetItemModel(code="dry_food", title="Сухой корм", kind="food", price=40, effects={"satiety": 15}, sort_order=1),
                AiPetItemModel(code="ball", title="Мячик", kind="toy", price=120, effects={"mood": 10}, sort_order=2),
                AiPetItemModel(code="bow", title="Бантик", kind="cosmetic", price=150, effects={}, slot="neck", sort_order=3),
                AiPetItemModel(code="top_hat", title="Цилиндр", kind="cosmetic", price=300, effects={}, slot="head", sort_order=4),
                AiPetItemModel(code="crown", title="Корона", kind="cosmetic", price=900, effects={}, slot="head", sort_order=5),
                # A mis-edited cosmetic (effects) is never shown or sold.
                AiPetItemModel(code="bad", title="Сломанная", kind="cosmetic", price=1, effects={"mood": 3}, slot="back", sort_order=6),
            ]
        )
        await session.commit()
        pet = await AiPetService(session).create_pet(
            owner=UserSnapshot(telegram_user_id=OWNER, username=None, first_name="O", last_name=None, is_bot=False),
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="C"),
            species_raw="кот", name_raw="Мурка", now=NOW,
        )
        await session.commit()
        yield session, pet.id
    await engine.dispose()


async def _give(session, pet_id: int, code: str, quantity: int = 1) -> None:
    session.add(AiPetInventoryModel(pet_id=pet_id, item_code=code, quantity=quantity, equipped=False, acquired_at=NOW))
    await session.flush()


async def test_catalog_lists_cosmetics_with_slots_and_skips_broken_rows(db) -> None:
    session, _ = db
    items = {item.code: item for item in await AiPetService(session).list_items()}
    assert items["bow"].slot == "neck" and items["dry_food"].slot is None
    assert "bad" not in items


async def test_cosmetics_cannot_be_applied_like_food(db) -> None:
    session, pet_id = db
    result = await AiPetService(session).use_item(
        pet_id=pet_id, chat_id=CHAT, actor=UserSnapshot(telegram_user_id=GUEST, username=None, first_name="G", last_name=None, is_bot=False),
        item_code="bow", kind=None, idempotency_key="x", economy_mode="global", today=NOW.date(), now=NOW,
    )
    assert result.status == "item_unavailable" and "рюкзак" in result.message


async def test_owner_uses_food_from_the_bag_without_paying(db) -> None:
    session, pet_id = db
    await _give(session, pet_id, "dry_food", 2)
    service = AiPetService(session)
    row = await session.get(AiPetModel, pet_id)
    row.satiety = 40
    await session.flush()

    async def use(actor: int, key: str, at: datetime = NOW):
        return await service.use_from_bag(
            pet_id=pet_id, chat_id=CHAT, owner_user_id=actor, item_code="dry_food", idempotency_key=key,
            today=at.date(), now=at,
        )

    assert (await use(GUEST, "g")).status == "blocked"  # only the owner uses the bag
    first = await use(OWNER, "o1")
    assert first.status == "ok" and first.applied["satiety"] == 15
    assert (await use(OWNER, "o1")).status == "duplicate"
    assert (await use(OWNER, "o2", NOW + timedelta(minutes=1))).status == "cooldown"
    assert (await use(OWNER, "o3", NOW + m.ITEM_COOLDOWNS["food"])).status == "ok"
    assert (await use(OWNER, "o4", NOW + 2 * m.ITEM_COOLDOWNS["food"])).status == "item_unavailable"
    owned = await session.get(AiPetInventoryModel, {"pet_id": pet_id, "item_code": "dry_food"})
    assert owned.quantity == 0
    assert await service.bag(pet_id=pet_id) == []


async def test_one_cosmetic_per_slot_and_outfit(db) -> None:
    session, pet_id = db
    for code in ("bow", "top_hat", "crown"):
        await _give(session, pet_id, code)
    service = AiPetService(session)
    await service.set_equipped(owner_user_id=OWNER, item_code="bow", equipped=True)
    await service.set_equipped(owner_user_id=OWNER, item_code="top_hat", equipped=True)
    assert sorted(await service.outfit(pet_id=pet_id)) == ["Бантик", "Цилиндр"]
    await service.set_equipped(owner_user_id=OWNER, item_code="crown", equipped=True)  # same slot as the hat
    assert sorted(await service.outfit(pet_id=pet_id)) == ["Бантик", "Корона"]
    await service.set_equipped(owner_user_id=OWNER, item_code="bow", equipped=False)
    assert await service.outfit(pet_id=pet_id) == ["Корона"]
    with pytest.raises(PetDomainError):
        await service.set_equipped(owner_user_id=OWNER, item_code="dry_food", equipped=True)
    with pytest.raises(PetDomainError):
        await service.set_equipped(owner_user_id=GUEST, item_code="bow", equipped=True)


def _item(code: str, kind: str, slot: str | None = None, price: int = 100) -> CatalogItem:
    return CatalogItem(code=code, title=code.title(), kind=kind, price=price, effects=m.ItemEffects(), min_level=1, slot=slot)


def test_bag_keyboard_and_card_outfit() -> None:
    entries = [
        BagEntry(item=_item("dry_food", "food"), quantity=3, equipped=False),
        BagEntry(item=_item("bow", "cosmetic", "neck"), quantity=1, equipped=True),
        BagEntry(item=_item("top_hat_with_a_very_long_code_x", "cosmetic", "head"), quantity=1, equipped=False),
    ]
    keyboard = ai_pets.bag_keyboard(2**40, entries)
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert data[0].startswith("aipet:use:") and data[1].startswith("aipet:off:") and data[2].startswith("aipet:wear:")
    assert all(len(item.encode()) <= 64 for item in data)
    pet = SimpleNamespace(
        name="Мурка", emoji="🐱", species_title="кошка", xp=0, mood=50, satiety=50, energy=50, traits=(), status="active"
    )
    card = ai_pets.render_card(pet, owner_label="o", viewer_affinity=None, outfit=["Бантик", "<b>"])
    assert "Наряд: Бантик, &lt;b&gt;" in card


def test_dialogue_knows_the_outfit() -> None:
    persona = d.PetPersona(
        name="Мурка", species_title="кошка", traits=(), character_custom=None, level=1, mood=50, satiety=50, energy=50,
        outfit=("Цилиндр",),
    )
    context = d.PetContext(persona=persona, speaker_name="Лиза", speaker_is_owner=False, speaker_attitude="нейтрален")
    assert "Наряд: Цилиндр" in d.build_pet_messages(context, user_text="hi")[0]["content"]


def test_bag_add_message_says_gift_for_guests() -> None:
    pet = SimpleNamespace(name="Мурка", owner_user_id=OWNER)
    result = SimpleNamespace(pet=pet, item=_item("bow", "cosmetic", "neck", price=150), new_balance=50)
    text = ai_pets.render_bag_add(result, actor=SimpleNamespace(id=GUEST), actor_link="Лиза")
    assert "дарит" in text and "Потрачено 150" in text and "/pet_bag" in text
    own = ai_pets.render_bag_add(result, actor=SimpleNamespace(id=OWNER), actor_link="Илья")
    assert "кладёт в рюкзак" in own


def test_bag_use_does_not_claim_a_second_payment() -> None:
    from selara.infrastructure.db.ai_pets import ActionResult

    pet = SimpleNamespace(name="Мурка", species_key="cat")
    result = ActionResult(status="ok", pet=pet, applied={"satiety": 15}, item=_item("dry_food", "food", price=40))
    assert "Потрачено" not in ai_pets.render_result(result, event_type="feed", actor_link="Илья", charged=False)
    assert "Потрачено 40" in ai_pets.render_result(result, event_type="feed", actor_link="Илья")
