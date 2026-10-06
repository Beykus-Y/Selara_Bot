from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.ai_pets import dialogue as d
from selara.application.feature_access import AccessReason, AccessTier
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pet_dialogue import TALK_COOLDOWN, AiPetDialogueRepository
from selara.infrastructure.db.ai_pets import AiPetService
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    AiPetEventModel,
    AiPetMemoryModel,
    AiPetMessageModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers import ai_pet_talk

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
DAY_START = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
CHAT = -2001
OWNER = 11
GUESTS = (21, 22, 23)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await SqlAlchemyActivityRepository(session).upsert_chat_settings(
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Chat"), values={"pets_enabled": True}
        )
        session.add_all(UserModel(telegram_user_id=user_id, first_name="U") for user_id in (OWNER, *GUESTS))
        session.add(
            UserEntitlementModel(
                user_id=OWNER, product_key=SELARA_PERSONAL_PRODUCT_KEY, status="active",
                valid_from=NOW - timedelta(days=1), valid_until=datetime.now(timezone.utc) + timedelta(days=29),
            )
        )
        await session.commit()
        pet = await AiPetService(session).create_pet(
            owner=UserSnapshot(telegram_user_id=OWNER, username=None, first_name="O", last_name=None, is_bot=False),
            chat=ChatSnapshot(telegram_chat_id=CHAT, chat_type="supergroup", title="Chat"),
            species_raw="кот",
            name_raw="Мурка",
            now=NOW,
        )
        await session.commit()
        session.info["pet_id"] = pet.id
        yield session
    await engine.dispose()


async def _admit(repo, pet_id, author, key, *, at=NOW, guests=2, guest=1):
    return await repo.admit_talk(
        pet_id=pet_id, chat_id=CHAT, author_user_id=author, content="привет", idempotency_key=key,
        telegram_message_id=None, day_start=DAY_START, guests_limit=guests, guest_limit=guest, now=at,
    )


async def test_guests_share_is_capped_but_the_owner_is_not(db) -> None:
    repo, pet_id = AiPetDialogueRepository(db), db.info["pet_id"]
    assert (await _admit(repo, pet_id, GUESTS[0], "a")).status == "ok"
    assert (await _admit(repo, pet_id, GUESTS[0], "b", at=NOW + TALK_COOLDOWN)).status == "guest_exhausted"
    assert (await _admit(repo, pet_id, GUESTS[1], "c")).status == "ok"
    assert (await _admit(repo, pet_id, GUESTS[2], "d")).status == "guests_exhausted"
    for index in range(5):
        status = (await _admit(repo, pet_id, OWNER, f"o{index}", at=NOW + TALK_COOLDOWN * index)).status
        assert status == "ok"


async def test_failed_talks_do_not_count_and_yesterday_resets(db) -> None:
    repo, pet_id = AiPetDialogueRepository(db), db.info["pet_id"]
    first = await _admit(repo, pet_id, GUESTS[0], "a")
    await repo.set_status(message_id=first.message_id, status="failed")
    assert (await _admit(repo, pet_id, GUESTS[0], "b", at=NOW + TALK_COOLDOWN)).status == "ok"
    later = DAY_START + timedelta(days=1, hours=1)
    assert (
        await repo.admit_talk(
            pet_id=pet_id, chat_id=CHAT, author_user_id=GUESTS[0], content="x", idempotency_key="c",
            telegram_message_id=None, day_start=DAY_START + timedelta(days=1), guests_limit=2, guest_limit=1, now=later,
        )
    ).status == "ok"


async def test_cooldown_duplicates_and_other_chats(db) -> None:
    repo, pet_id = AiPetDialogueRepository(db), db.info["pet_id"]
    assert (await _admit(repo, pet_id, OWNER, "a")).status == "ok"
    assert (await _admit(repo, pet_id, OWNER, "a", at=NOW + TALK_COOLDOWN)).status == "duplicate"
    cooldown = await _admit(repo, pet_id, OWNER, "b", at=NOW + timedelta(seconds=3))
    assert cooldown.status == "cooldown" and cooldown.retry_after == TALK_COOLDOWN - timedelta(seconds=3)
    assert (
        await repo.admit_talk(
            pet_id=pet_id, chat_id=-999, author_user_id=OWNER, content="x", idempotency_key="z",
            telegram_message_id=None, day_start=DAY_START, guests_limit=2, guest_limit=1, now=NOW + timedelta(hours=1),
        )
    ).status == "unavailable"


async def test_notes_are_capped_and_forget_wipes_only_dialogue(db) -> None:
    repo, pet_id = AiPetDialogueRepository(db), db.info["pet_id"]
    for index in range(d.MAX_NOTES + 5):
        await repo.add_notes(pet_id=pet_id, chat_id=CHAT, notes=[f"заметка {index}"], now=NOW + timedelta(minutes=index))
    notes = await repo.notes(pet_id=pet_id, chat_id=CHAT)
    assert len(notes) == d.MAX_NOTES and notes[-1] == f"заметка {d.MAX_NOTES + 4}"
    await _admit(repo, pet_id, OWNER, "a")
    await AiPetService(db).perform_action(
        pet_id=pet_id, chat_id=CHAT, actor_user_id=GUESTS[0], action_key="pat", idempotency_key="pat1",
        today=NOW.date(), now=NOW,
    )
    assert await repo.forget(pet_id=pet_id, chat_id=CHAT) == (1, d.MAX_NOTES)
    assert await repo.notes(pet_id=pet_id, chat_id=CHAT) == []
    assert (await db.scalars(select(AiPetEventModel).where(AiPetEventModel.event_type == "pat"))).one()


async def test_weekly_aggregates_count_care_per_person(db) -> None:
    repo, pet_id = AiPetDialogueRepository(db), db.info["pet_id"]
    service = AiPetService(db)
    for index in range(3):
        await service.perform_action(
            pet_id=pet_id, chat_id=CHAT, actor_user_id=GUESTS[0], action_key="pat", idempotency_key=f"p{index}",
            today=NOW.date(), now=NOW + timedelta(hours=index),
        )
    await service.perform_action(
        pet_id=pet_id, chat_id=CHAT, actor_user_id=GUESTS[1], action_key="tease", idempotency_key="t",
        today=NOW.date(), now=NOW,
    )
    rows = await repo.weekly_aggregates(pet_id=pet_id, chat_id=CHAT, now=NOW + timedelta(hours=3))
    assert rows[0] == (GUESTS[0], "pat", 3) and (GUESTS[1], "tease", 1) in rows
    assert await repo.weekly_aggregates(pet_id=pet_id, chat_id=CHAT, now=NOW + timedelta(days=8)) == []


# ----- the whole talk flow with a fake quota and a fake model ----------------------


class _Llm:
    def __init__(self, reply: str = "Мяу, всё хорошо!") -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []
        self.summaries = 0

    async def chat_simple(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(value=self.reply)

    async def summarize(self, messages, **kwargs):
        self.summaries += 1
        return SimpleNamespace(value="Мне понравился разговор про рыбу")


class _Access:
    decision = SimpleNamespace(allowed=True, reused=False, invocation_id=None, reason=None, access_tier=AccessTier.PAID)
    reservations: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def reserve_feature_usage(self, **kwargs):
        _Access.reservations.append(kwargs)
        return _Access.decision


def _message(text: str, *, user_id: int = GUESTS[0], message_id: int = 500, reply_to_id: int | None = None):
    reply = None
    if reply_to_id is not None:
        reply = SimpleNamespace(message_id=reply_to_id, from_user=SimpleNamespace(id=1, is_bot=True))
    return SimpleNamespace(
        text=text,
        message_id=message_id,
        chat=SimpleNamespace(id=CHAT, type="supergroup", title="Chat"),
        from_user=SimpleNamespace(id=user_id, is_bot=False, username=None, first_name="Лиза", last_name=None),
        reply_to_message=reply,
        reply=AsyncMock(return_value=SimpleNamespace(message_id=9000 + message_id)),
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    _Access.reservations = []
    _Access.decision = SimpleNamespace(allowed=True, reused=False, invocation_id=None, reason=None, access_tier=AccessTier.PAID)
    monkeypatch.setattr(ai_pet_talk, "FeatureAccessService", _Access)
    monkeypatch.setattr(ai_pet_talk, "SqlAlchemyFeatureQuotaRepository", lambda *a, **k: None)
    monkeypatch.setattr(ai_pet_talk, "SqlAlchemyUserEntitlementResolver", lambda *a, **k: None)
    ai_pet_talk._names_cache.clear()
    return _Access


async def _talk(db, message, llm, settings=None):
    target = await ai_pet_talk.resolve_talk_target(
        message, chat_settings=SimpleNamespace(pets_enabled=True), db_session=db, economy_repo=None
    )
    assert target is not None
    await ai_pet_talk.handle_pet_talk(
        message, pet_id=target[0], talk_text=target[1], activity_repo=SimpleNamespace(get_chat_display_name=AsyncMock(return_value=None)),
        db_session=db, economy_repo=None, settings=settings or Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///"),
        session_factory=None, personal_config=None, llm_client=llm,
    )


async def test_talk_answers_charges_the_owner_and_can_be_continued_by_reply(db, patched) -> None:
    llm = _Llm()
    first = _message("Мурка, как дела?")
    await _talk(db, first, llm)

    reserved = patched.reservations[0]
    assert reserved["scope"].scope_id == OWNER and reserved["actor_user_id"] == GUESTS[0]
    assert "как дела?" == llm.calls[0][-1]["content"]
    first.reply.assert_awaited_once()
    assert "Мурка</b>: Мяу, всё хорошо!" in first.reply.await_args.args[0]
    rows = (await db.scalars(select(AiPetMessageModel).order_by(AiPetMessageModel.id))).all()
    assert [(row.role, row.status) for row in rows] == [("user", "ok"), ("assistant", "ok")]
    assert rows[1].telegram_message_id == 9500

    follow_up = _message("а ты рыбу любишь?", user_id=GUESTS[1], message_id=501, reply_to_id=9500)
    await _talk(db, follow_up, llm)
    assert "<chat_history>" in llm.calls[1][0]["content"] and "Мяу, всё хорошо!" in llm.calls[1][0]["content"]
    assert llm.calls[1][-1]["content"] == "а ты рыбу любишь?"


async def test_without_the_owners_personal_the_pet_cannot_talk(db, patched) -> None:
    row = (await db.scalars(select(UserEntitlementModel))).one()
    row.valid_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    llm = _Llm()
    message = _message("Мурка, привет")
    await _talk(db, message, llm)
    assert llm.calls == [] and patched.reservations == []
    assert "Selara Personal" in message.reply.await_args.args[0]
    assert (await db.scalars(select(AiPetMessageModel.status))).all() == ["failed"]


async def test_exhausted_pool_is_a_template_and_frees_the_guest_slot(db, patched) -> None:
    patched.decision = SimpleNamespace(
        allowed=False, reused=False, invocation_id=None, reason=AccessReason.QUOTA_EXHAUSTED, access_tier=AccessTier.PAID
    )
    llm = _Llm()
    message = _message("Мурка, привет")
    await _talk(db, message, llm)
    assert llm.calls == []
    assert "наговорился" in message.reply.await_args.args[0]
    assert (await db.scalars(select(AiPetMessageModel.status))).all() == ["failed"]


async def test_empty_model_answer_is_not_stored(db, patched) -> None:
    llm = _Llm(reply="")
    message = _message("Мурка, привет")
    await _talk(db, message, llm)
    assert "молчит" in message.reply.await_args.args[0]
    assert (await db.scalars(select(AiPetMessageModel.role))).all() == ["user"]


async def test_notes_are_extracted_on_schedule(db, patched, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_pet_talk.d, "EXTRACT_EVERY_TALKS", 1)
    llm = _Llm()
    await _talk(db, _message("Мурка, расскажи про рыбу"), llm)
    assert llm.summaries == 1
    notes = (await db.scalars(select(AiPetMemoryModel.content))).all()
    assert notes == ["Мне понравился разговор про рыбу"]


async def test_names_in_the_middle_or_disabled_chats_are_ignored(db, patched) -> None:
    settings_on, settings_off = SimpleNamespace(pets_enabled=True), SimpleNamespace(pets_enabled=False)
    middle = _message("я видел Мурку")
    assert await ai_pet_talk.resolve_talk_target(middle, chat_settings=settings_on, db_session=db, economy_repo=None) is None
    named = _message("Мурка, привет")
    assert await ai_pet_talk.resolve_talk_target(named, chat_settings=settings_off, db_session=db, economy_repo=None) is None
    command = _message("/pet")
    assert await ai_pet_talk.resolve_talk_target(command, chat_settings=settings_on, db_session=db, economy_repo=None) is None


async def test_talk_counter_survives_history_pruning(db) -> None:
    repo, pet_id = AiPetDialogueRepository(db), db.info["pet_id"]
    old = NOW - timedelta(days=5)
    for index in range(30):
        await repo.add_reply(pet_id=pet_id, chat_id=CHAT, content=f"r{index}", now=old + timedelta(minutes=index))
        await repo.record_talk(pet_id=pet_id, chat_id=CHAT, author_user_id=OWNER, idempotency_key=f"t{index}", now=old)
    for index in range(30):
        await repo.add_reply(pet_id=pet_id, chat_id=CHAT, content=f"n{index}", now=NOW + timedelta(minutes=index))
    await repo.prune_history(pet_id=pet_id, chat_id=CHAT, now=NOW + timedelta(hours=1))
    assert len(await repo.recent(pet_id=pet_id, chat_id=CHAT, limit=100)) == 40
    assert await repo.record_talk(pet_id=pet_id, chat_id=CHAT, author_user_id=OWNER, idempotency_key="t-last", now=NOW) == 31
