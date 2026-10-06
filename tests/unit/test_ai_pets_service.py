# Economy purchases and concurrency are covered in tests/integration/test_ai_pets_postgres.py
# (economy tables need PostgreSQL).
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.ai_pets import mechanics as m
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pets import AiPetService, PetDomainError
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.models import (
    AiPetEventModel,
    AiPetItemModel,
    AiPetModel,
    AiPetRelationshipModel,
    ChatModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 6)
CHAT = -1001
OWNER = 11
GUEST = 22


def _user(user_id: int) -> UserSnapshot:
    return UserSnapshot(telegram_user_id=user_id, username=None, first_name=f"U{user_id}", last_name=None, is_bot=False)


def _chat(chat_id: int = CHAT) -> ChatSnapshot:
    return ChatSnapshot(telegram_chat_id=chat_id, chat_type="supergroup", title="Chat")


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        activity = SqlAlchemyActivityRepository(db)
        for chat_id in (CHAT, -1002):
            settings = await activity.upsert_chat_settings(chat=_chat(chat_id), values={"pets_enabled": True})
            assert settings.pets_enabled is True
        for user_id in (OWNER, GUEST):
            db.add(UserModel(telegram_user_id=user_id, first_name=f"U{user_id}"))
        db.add_all(
            [
                AiPetItemModel(code="dry_food", title="Сухой корм", kind="food", price=40, effects={"satiety": 15, "xp": 2}, sort_order=1),
                AiPetItemModel(code="ball", title="Мячик", kind="toy", price=120, effects={"mood": 10, "xp": 5}, sort_order=2),
                AiPetItemModel(code="puzzle", title="Головоломка", kind="toy", price=400, effects={"mood": 15}, min_level=5, sort_order=3),
                AiPetItemModel(code="broken", title="Сломанный", kind="food", price=1, effects={"satiety": "a lot"}, sort_order=0),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


async def _grant_personal(db, user_id: int = OWNER, *, valid_until: datetime | None = None) -> None:
    db.add(
        UserEntitlementModel(
            user_id=user_id,
            product_key=SELARA_PERSONAL_PRODUCT_KEY,
            status="active",
            valid_from=NOW - timedelta(days=1),
            valid_until=valid_until or NOW + timedelta(days=29),
        )
    )
    await db.flush()


async def _create(db, name: str = "Мурка", owner: int = OWNER, chat_id: int = CHAT):
    return await AiPetService(db).create_pet(owner=_user(owner), chat=_chat(chat_id), species_raw="кот", name_raw=name, now=NOW)


async def test_creation_requires_an_active_personal_subscription(session) -> None:
    with pytest.raises(PetDomainError, match="Selara Personal"):
        await _create(session)
    await _grant_personal(session, valid_until=NOW - timedelta(seconds=1))
    with pytest.raises(PetDomainError, match="Selara Personal"):
        await _create(session)


async def test_create_pet_and_one_pet_per_owner_and_unique_name_per_chat(session) -> None:
    await _grant_personal(session)
    await _grant_personal(session, GUEST)
    pet = await _create(session)
    assert (pet.name, pet.species_key, pet.level, pet.current_chat_id, pet.home_chat_id) == ("Мурка", "cat", 1, CHAT, CHAT)
    with pytest.raises(PetDomainError, match="уже есть питомец"):
        await _create(session, name="Барсик")
    with pytest.raises(PetDomainError, match="таким именем"):
        await _create(session, name="мурка", owner=GUEST)
    assert (await _create(session, name="мурка", owner=GUEST, chat_id=-1002)).name == "мурка"


async def test_action_applies_effects_and_replay_is_a_noop(session) -> None:
    await _grant_personal(session)
    pet = await _create(session)
    service = AiPetService(session)
    first = await service.perform_action(
        pet_id=pet.id, chat_id=CHAT, actor_user_id=GUEST, action_key="pat", idempotency_key="k1", today=TODAY, now=NOW
    )
    assert first.status == "ok"
    assert first.applied["mood"] == m.ACTIONS["pat"].mood
    assert first.affinity == m.ACTIONS["pat"].affinity
    replay = await service.perform_action(
        pet_id=pet.id, chat_id=CHAT, actor_user_id=GUEST, action_key="pat", idempotency_key="k1", today=TODAY, now=NOW
    )
    assert replay.status == "duplicate"
    events = (await session.scalars(select(AiPetEventModel).where(AiPetEventModel.event_type == "pat"))).all()
    assert len(events) == 1
    relation = await session.get(AiPetRelationshipModel, {"pet_id": pet.id, "chat_id": CHAT, "user_id": GUEST})
    assert relation.interactions == 1


async def test_cooldown_is_per_person_and_action(session) -> None:
    await _grant_personal(session)
    pet = await _create(session)
    service = AiPetService(session)

    async def pat(actor: int, key: str, at: datetime):
        return await service.perform_action(
            pet_id=pet.id, chat_id=CHAT, actor_user_id=actor, action_key="pat", idempotency_key=key, today=TODAY, now=at
        )

    assert (await pat(GUEST, "a", NOW)).status == "ok"
    cooldown = await pat(GUEST, "b", NOW + timedelta(minutes=1))
    assert cooldown.status == "cooldown" and "9 мин" in cooldown.message
    assert (await pat(OWNER, "c", NOW + timedelta(minutes=1))).status == "ok"
    assert (await pat(GUEST, "d", NOW + m.ACTIONS["pat"].cooldown)).status == "ok"


async def test_expired_personal_keeps_pet_and_mechanics(session) -> None:
    await _grant_personal(session, valid_until=NOW + timedelta(minutes=5))
    pet = await _create(session)
    later = NOW + timedelta(days=3)
    result = await AiPetService(session).perform_action(
        pet_id=pet.id, chat_id=CHAT, actor_user_id=OWNER, action_key="pat", idempotency_key="late", today=TODAY, now=later
    )
    assert result.status == "ok"
    assert result.pet.status == "active"


async def test_pet_is_only_reachable_in_its_current_chat_and_while_awake(session) -> None:
    await _grant_personal(session)
    pet = await _create(session)
    service = AiPetService(session)
    elsewhere = await service.perform_action(
        pet_id=pet.id, chat_id=-1002, actor_user_id=GUEST, action_key="pat", idempotency_key="x1", today=TODAY, now=NOW
    )
    assert elsewhere.status == "unavailable"
    await service.set_sleep(chat_id=CHAT, name="МУРКА", actor_user_id=GUEST, asleep=True)
    asleep = await service.perform_action(
        pet_id=pet.id, chat_id=CHAT, actor_user_id=GUEST, action_key="pat", idempotency_key="x2", today=TODAY, now=NOW
    )
    assert asleep.status == "unavailable"
    assert (await service.set_sleep(chat_id=CHAT, name="Мурка", actor_user_id=GUEST, asleep=False)).status == "active"


async def test_lost_chat_sends_pet_home_or_to_sleep(session) -> None:
    await _grant_personal(session)
    pet = await _create(session)
    row = await session.get(AiPetModel, pet.id)
    row.home_chat_id = -1002
    row.current_chat_id = None  # what ON DELETE SET NULL leaves behind
    await session.flush()
    rehomed = await AiPetService(session).get_owner_pet(owner_user_id=OWNER, now=NOW)
    assert (rehomed.status, rehomed.current_chat_id) == ("active", -1002)

    row.current_chat_id = None
    row.home_chat_id = None
    await session.flush()
    orphan = await AiPetService(session).get_owner_pet(owner_user_id=OWNER, now=NOW)
    assert (orphan.status, orphan.dormant_reason) == ("dormant", "no_home")


async def test_release_frees_the_slot_and_the_name(session) -> None:
    await _grant_personal(session)
    pet = await _create(session)
    service = AiPetService(session)
    released = await service.release(owner_user_id=OWNER, chat_id=CHAT)
    assert released.status == "released"
    assert await service.get_owner_pet(owner_user_id=OWNER) is None
    again = await _create(session)
    assert again.id != pet.id


async def test_level_up_records_event_and_unlocks_items(session) -> None:
    await _grant_personal(session)
    pet = await _create(session)
    row = await session.get(AiPetModel, pet.id)
    row.xp = m.xp_to_next_level(1) - 1
    await session.flush()
    result = await AiPetService(session).perform_action(
        pet_id=pet.id, chat_id=CHAT, actor_user_id=GUEST, action_key="pat", idempotency_key="lvl", today=TODAY, now=NOW
    )
    assert result.leveled_up_to == 2
    assert (await session.scalars(select(AiPetEventModel).where(AiPetEventModel.event_type == "level_up"))).one()
    codes = [item.code for item in await AiPetService(session).list_items(level=5)]
    assert codes == ["dry_food", "ball", "puzzle"]


async def test_group_upgrade_moves_pets_relationships_and_events(session) -> None:
    await _grant_personal(session)
    await _grant_personal(session, GUEST)
    pet = await _create(session)
    clash = await _create(session, name="Рыжик", owner=GUEST)
    await AiPetService(session).perform_action(
        pet_id=pet.id, chat_id=CHAT, actor_user_id=GUEST, action_key="pat", idempotency_key="m1", today=TODAY, now=NOW
    )
    # The supergroup already has an active pet named like one of ours.
    other_owner = 33
    session.add(UserModel(telegram_user_id=other_owner, first_name="O"))
    session.add(ChatModel(telegram_chat_id=-1009, type="supergroup", title="New"))
    session.add(
        AiPetModel(
            owner_user_id=other_owner, home_chat_id=-1009, current_chat_id=-1009, species_key="dog", name="Рыжик",
            name_norm="рыжик", traits=[], status="active", last_tick_at=NOW,
        )
    )
    await session.flush()

    await migrate_chat_id(session, old_chat_id=CHAT, new_chat_id=-1009)
    moved = await session.get(AiPetModel, pet.id)
    await session.refresh(moved)
    assert (moved.current_chat_id, moved.home_chat_id, moved.status) == (-1009, -1009, "active")
    clashed = await session.get(AiPetModel, clash.id)
    await session.refresh(clashed)
    assert (clashed.status, clashed.dormant_reason, clashed.current_chat_id) == ("dormant", "name_conflict", -1009)
    relation = await session.get(AiPetRelationshipModel, {"pet_id": pet.id, "chat_id": -1009, "user_id": GUEST})
    assert relation is not None
    events = (await session.scalars(select(AiPetEventModel.chat_id).where(AiPetEventModel.pet_id == pet.id))).all()
    assert set(events) == {-1009}
