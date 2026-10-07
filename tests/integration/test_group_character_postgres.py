"""Group character on PostgreSQL: member-mode quotas under concurrency, admission and chat migration."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import AccessReason, AccessTier, FeatureAccessService, GroupMemberQuotaLimits
from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY
from selara.core.config import Settings
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_ai_character_repository import ChatAiCharacterRepository
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.models import (
    AiFeatureQuotaUsageModel,
    AiTurnLeaseModel,
    ChatAiCallNameModel,
    ChatAiCharacterModel,
    ChatEntitlementModel,
    ChatMemberAiMessageModel,
    ChatModel,
    UserModel,
)
from selara.infrastructure.db.telegram_stars import SqlAlchemyChatEntitlementResolver
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.handlers import group_character

pytestmark = [pytest.mark.integration]

CHAT = -100900
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
LIMITS = GroupMemberQuotaLimits(free_daily=6, free_per_actor=2, paid_daily=20, paid_per_actor=4)
MEMBERS = tuple(range(900, 910))


@pytest.fixture
async def factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    # The busy note is rate-limited per process: each test starts with a fresh window.
    group_character._busy_reply_sent_at.clear()
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(ChatModel(telegram_chat_id=CHAT, type="supergroup", title="Selara"))
        db.add_all(UserModel(telegram_user_id=user_id, first_name="U") for user_id in MEMBERS)
        await db.commit()
    yield sessions
    await engine.dispose()


def _service(sessions) -> FeatureAccessService:
    return FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(sessions),
        entitlement_resolver=SqlAlchemyChatEntitlementResolver(sessions, group_member_limits=LIMITS),
        group_member_limits=LIMITS,
    )


async def _reserve(service, *, actor: int, message_id: int, chat_id: int = CHAT):
    return await service.reserve_feature_usage(
        feature=AiFeature.GROUP_MEMBER,
        chat_id=chat_id,
        actor_user_id=actor,
        trigger="telegram_message",
        timezone_name="UTC",
        idempotency_key=f"group_member:{chat_id}:{message_id}",
        source_message_id=message_id,
        now=NOW,
    )


async def test_concurrent_members_never_exceed_chat_or_member_limits(factory) -> None:
    service = _service(factory)
    # Every member tries four times at once: per member 2, for the chat 6 in total.
    attempts = [(member, index) for member in MEMBERS for index in range(4)]
    decisions = await asyncio.gather(
        *(_reserve(service, actor=member, message_id=member * 10 + index) for member, index in attempts)
    )
    granted = [d for d in decisions if d.allowed]
    assert len(granted) == LIMITS.free_daily
    async with factory() as db:
        per_actor = (
            await db.execute(
                select(AiFeatureQuotaUsageModel.actor_user_id, func.count())
                .where(AiFeatureQuotaUsageModel.status == "consumed")
                .group_by(AiFeatureQuotaUsageModel.actor_user_id)
            )
        ).all()
    assert all(count <= LIMITS.free_per_actor for _, count in per_actor)
    reasons = {d.reason for d in decisions if not d.allowed}
    assert reasons <= {AccessReason.QUOTA_EXHAUSTED, AccessReason.ACTOR_QUOTA_EXHAUSTED}


async def test_member_share_is_reported_and_selara_ai_raises_it(factory) -> None:
    service = _service(factory)
    member = MEMBERS[0]
    assert (await _reserve(service, actor=member, message_id=1)).allowed
    assert (await _reserve(service, actor=member, message_id=2)).allowed
    denied = await _reserve(service, actor=member, message_id=3)
    assert denied.reason == AccessReason.ACTOR_QUOTA_EXHAUSTED
    assert (denied.quota_used, denied.quota_limit) == (2, LIMITS.free_per_actor)
    # Another member still has room in the chat's pool.
    assert (await _reserve(service, actor=MEMBERS[1], message_id=4)).allowed

    async with factory() as db:
        db.add(
            ChatEntitlementModel(
                chat_id=CHAT, product_key=SELARA_AI_PRODUCT_KEY, status="active",
                valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=29),
            )
        )
        await db.commit()
    paid = await _reserve(service, actor=member, message_id=5)
    assert paid.allowed and paid.access_tier == AccessTier.PAID and paid.quota_limit == LIMITS.paid_daily


async def test_admission_serializes_a_members_cooldown(factory) -> None:
    async with factory() as db:
        db.add(ChatAiCharacterModel(chat_id=CHAT, member_mode_enabled=True))
        await db.commit()

    async def admit(message_id: int):
        async with factory() as db:
            repo = ChatAiCharacterRepository(db)
            result = await repo.admit_turn(
                chat_id=CHAT, author_user_id=MEMBERS[0], content="?", idempotency_key=f"m:{message_id}",
                telegram_message_id=message_id, cooldown=timedelta(seconds=30), now=datetime.now(timezone.utc),
            )
            await repo.commit()
            return result.status

    statuses = await asyncio.gather(*(admit(i) for i in range(5)))
    assert statuses.count("ok") == 1 and statuses.count("cooldown") == 4
    assert await admit(0) == "duplicate"


async def test_one_primary_name_per_chat_is_enforced_by_the_database(factory) -> None:
    async with factory() as db:
        db.add(ChatAiCallNameModel(chat_id=CHAT, name_display="Селя", name_norm="селя", is_primary=True))
        await db.commit()
        db.add(ChatAiCallNameModel(chat_id=CHAT, name_display="Селара", name_norm="селара", is_primary=True))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_chat_migration_collision_policy(factory) -> None:
    old_id, new_id = CHAT, -1000100900
    service = _service(factory)
    async with factory() as db:
        db.add(ChatModel(telegram_chat_id=new_id, type="supergroup", title="new"))
        await db.flush()
        db.add_all(
            [
                ChatAiCallNameModel(chat_id=old_id, name_display="Селя", name_norm="селя", is_primary=True),
                ChatAiCallNameModel(chat_id=old_id, name_display="Селарка", name_norm="селарка"),
                ChatAiCallNameModel(chat_id=new_id, name_display="СЕЛЯ", name_norm="селя"),
                ChatAiCharacterModel(
                    chat_id=old_id, character_preset="sarcastic", member_mode_enabled=True,
                    member_history_access=False, updated_at=NOW - timedelta(hours=2),
                ),
                ChatAiCharacterModel(
                    chat_id=new_id, character_preset="strict", member_mode_enabled=True,
                    member_history_access=True, updated_at=NOW,
                ),
                ChatMemberAiMessageModel(chat_id=old_id, role="user", content="привет"),
            ]
        )
        await db.commit()

    # Usage of the old id and the new id in the current period adds up after the move.
    assert (await _reserve(service, actor=MEMBERS[0], message_id=1, chat_id=old_id)).allowed
    assert (await _reserve(service, actor=MEMBERS[1], message_id=2, chat_id=old_id)).allowed
    assert (await _reserve(service, actor=MEMBERS[2], message_id=3, chat_id=new_id)).allowed

    async with factory() as db:
        await migrate_chat_id(db, old_chat_id=old_id, new_chat_id=new_id)
        await db.commit()

    async with factory() as db:
        names = (
            await db.execute(
                select(ChatAiCallNameModel.name_display, ChatAiCallNameModel.is_primary)
                .where(ChatAiCallNameModel.chat_id == new_id)
            )
        ).all()
        # The new chat had no primary name, so the old primary stays primary; the duplicate keeps the new row.
        assert set(names) == {("СЕЛЯ", True), ("Селарка", False)}
        character = await db.get(ChatAiCharacterModel, new_id)
        assert character.character_preset == "strict"
        assert character.member_history_access is False
        assert await db.get(ChatAiCharacterModel, old_id) is None
        assert (await db.scalar(select(func.count()).select_from(ChatMemberAiMessageModel).where(
            ChatMemberAiMessageModel.chat_id == new_id
        ))) == 1

    used = (await service.get_usage_summary(
        feature=AiFeature.GROUP_MEMBER, chat_id=new_id, trigger="telegram_message", timezone_name="UTC", now=NOW,
    )).quota_used
    assert used == 3


class _HeldLlm:
    """The first model call waits until ``release`` is set, so the chat's turn stays in flight while the test sends more."""

    accounting_service = None

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat_with_tools(self, messages, tools, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        message = SimpleNamespace(content=f"ответ {self.calls}", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def _group_message(text: str, *, message_id: int, user_id: int, chat_id: int = CHAT) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id, type="supergroup", title="Selara"),
        from_user=SimpleNamespace(id=user_id, is_bot=False, username=None, first_name="Вася", last_name=None),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(message_id=message_id + 9000)),
    )


async def _call_group(sessions, message: SimpleNamespace, llm: _HeldLlm, text: str) -> None:
    settings = Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///", llm_cooldown_seconds=0)
    async with sessions() as db:
        await group_character.handle_group_call(
            message,
            text=text,
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            activity_repo=SimpleNamespace(get_chat_display_name=AsyncMock(return_value=None)),
            db_session=db,
            settings=settings,
            session_factory=sessions,
            llm_client=llm,
        )


async def test_a_second_call_in_the_chat_does_not_run_beside_the_running_turn(factory) -> None:
    async with factory() as db:
        db.add(ChatAiCharacterModel(chat_id=CHAT, member_mode_enabled=True))
        db.add(ChatAiCallNameModel(chat_id=CHAT, name_display="Селя", name_norm="селя", is_primary=True))
        await db.commit()
    llm = _HeldLlm()
    first = _group_message("первый вопрос", message_id=1, user_id=MEMBERS[0])
    turn = asyncio.create_task(_call_group(factory, first, llm, "первый вопрос"))
    await asyncio.wait_for(llm.started.wait(), timeout=10)
    try:
        # Another member asks while the first answer is in flight: it gets a note and no model call beside the turn.
        second = _group_message("второй вопрос", message_id=2, user_id=MEMBERS[1])
        await asyncio.wait_for(_call_group(factory, second, llm, "второй вопрос"), timeout=10)
        assert llm.calls == 1
        second.reply.assert_awaited_once_with(group_character._BUSY_TEXT)
    finally:
        llm.release.set()
        await asyncio.wait_for(turn, timeout=10)
    first.reply.assert_awaited_once()
    async with factory() as db:
        rows = (await db.scalars(select(ChatMemberAiMessageModel).order_by(ChatMemberAiMessageModel.id))).all()
    # Only the first question was admitted, and its answer is saved for the next turn to read.
    assert [(row.role, row.status) for row in rows] == [("user", "ok"), ("assistant", "ok")]


async def test_turns_in_different_chats_do_not_block_each_other(factory) -> None:
    other = CHAT - 1
    async with factory() as db:
        db.add(ChatModel(telegram_chat_id=other, type="supergroup", title="Other"))
        await db.flush()
        db.add_all(ChatAiCharacterModel(chat_id=chat_id, member_mode_enabled=True) for chat_id in (CHAT, other))
        db.add_all(
            ChatAiCallNameModel(chat_id=chat_id, name_display="Селя", name_norm="селя", is_primary=True)
            for chat_id in (CHAT, other)
        )
        await db.commit()
    llm = _HeldLlm()
    turn = asyncio.create_task(
        _call_group(factory, _group_message("первый вопрос", message_id=1, user_id=MEMBERS[0]), llm, "первый вопрос")
    )
    await asyncio.wait_for(llm.started.wait(), timeout=10)
    try:
        elsewhere = _group_message("вопрос в другом чате", message_id=3, user_id=MEMBERS[2], chat_id=other)
        await asyncio.wait_for(_call_group(factory, elsewhere, llm, "вопрос в другом чате"), timeout=10)
        assert llm.calls == 2
        elsewhere.reply.assert_awaited_once()
        assert elsewhere.reply.await_args.args[0] != group_character._BUSY_TEXT
    finally:
        llm.release.set()
        await asyncio.wait_for(turn, timeout=10)


async def test_a_turn_whose_lease_was_taken_over_saves_and_sends_nothing(factory) -> None:
    async with factory() as db:
        db.add(ChatAiCharacterModel(chat_id=CHAT, member_mode_enabled=True))
        db.add(ChatAiCallNameModel(chat_id=CHAT, name_display="Селя", name_norm="селя", is_primary=True))
        await db.commit()
    llm = _HeldLlm()
    message = _group_message("вопрос", message_id=1, user_id=MEMBERS[0])
    turn = asyncio.create_task(_call_group(factory, message, llm, "вопрос"))
    await asyncio.wait_for(llm.started.wait(), timeout=10)
    try:
        # Another instance takes the key over while this turn waits for its model.
        async with factory() as db:
            await db.execute(
                update(AiTurnLeaseModel)
                .where(AiTurnLeaseModel.lease_key == f"group_member_turn:{CHAT}")
                .values(owner_token="other-instance", lease_expires_at=func.now() + timedelta(seconds=60))
            )
            await db.commit()
    finally:
        llm.release.set()
        await asyncio.wait_for(turn, timeout=10)
    message.reply.assert_awaited_once_with(group_character._LEASE_LOST_TEXT)
    async with factory() as db:
        rows = (
            await db.scalars(select(ChatMemberAiMessageModel).where(ChatMemberAiMessageModel.chat_id == CHAT))
        ).all()
    assert [row.role for row in rows if row.status == "ok"] == []


async def test_a_lease_left_by_a_dead_instance_does_not_block_the_chat(factory) -> None:
    async with factory() as db:
        db.add(ChatAiCharacterModel(chat_id=CHAT, member_mode_enabled=True))
        db.add(ChatAiCallNameModel(chat_id=CHAT, name_display="Селя", name_norm="селя", is_primary=True))
        db.add(
            AiTurnLeaseModel(
                lease_key=f"group_member_turn:{CHAT}",
                owner_token="dead-instance",
                lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        await db.commit()
    llm = _HeldLlm()
    llm.release.set()
    message = _group_message("вопрос", message_id=1, user_id=MEMBERS[0])
    await asyncio.wait_for(_call_group(factory, message, llm, "вопрос"), timeout=10)
    assert llm.calls == 1
    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] != group_character._BUSY_TEXT
