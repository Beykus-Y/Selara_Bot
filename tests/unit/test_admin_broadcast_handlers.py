from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, SuccessfulPayment, Update, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.config import Settings
from selara.infrastructure.db.models import AutoConfigSessionModel
from selara.infrastructure.db.telegram_stars import PaymentRefundClaim, PaymentResult
from selara.presentation.handlers import autoconfig, premium, stats

from selara.presentation.handlers.admin_broadcasts import (
    admin_broadcast_native_reaction,
    admin_broadcast_native_reaction_count,
    admin_broadcast_reaction_callback,
    decode_broadcast_reaction_callback,
)
from selara.presentation.routers import build_router


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abr:42:r1", (42, "r1")),
        ("abr:999999:r6", (999999, "r6")),
        ("abr:0:r1", None),
        ("abr:42:r7", None),
        ("abr:abc:r1", None),
        ("other:42:r1", None),
        (None, None),
    ],
)
def test_callback_payload_decoder_is_strict(value: str | None, expected: tuple[int, str] | None) -> None:
    assert decode_broadcast_reaction_callback(value) == expected


@pytest.mark.asyncio
async def test_inline_callback_records_user_and_answers_quickly() -> None:
    repo = SimpleNamespace(toggle_admin_broadcast_inline_reaction=AsyncMock(return_value="selected"))
    query = SimpleNamespace(
        data="abr:42:r2",
        from_user=SimpleNamespace(
            id=700,
            username="reader",
            first_name="Reader",
            last_name=None,
            is_bot=False,
        ),
        message=SimpleNamespace(
            date=datetime(2026, 8, 14, tzinfo=UTC),
            message_id=9001,
            chat=SimpleNamespace(id=-1007001),
        ),
        answer=AsyncMock(),
    )

    await admin_broadcast_reaction_callback(query, repo)

    call = repo.toggle_admin_broadcast_inline_reaction.await_args.kwargs
    assert call["delivery_id"] == 42
    assert call["option_key"] == "r2"
    assert call["chat_id"] == -1007001
    assert call["telegram_message_id"] == 9001
    assert call["user"].telegram_user_id == 700
    query.answer.assert_awaited_once_with("Реакция сохранена.")


@pytest.mark.asyncio
async def test_built_router_subscribes_to_reactions_and_prioritizes_payment_updates(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AutoConfigSessionModel.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buyer_id = 123
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            AutoConfigSessionModel(
                id="pending-autocfg",
                user_id=buyer_id,
                chat_id=None,
                state="active",
                revision=1,
                candidates=[],
                baseline={},
                draft={},
                touched=[],
                history=[],
                turns=0,
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()
    stats._set_pending_iris_import(
        importer_user_id=buyer_id,
        session=stats._PendingIrisImportSession(
            source_chat_id=-100,
            source_chat_type="supergroup",
            source_chat_title="Test group",
            target_user_id=456,
            target_username="target",
            target_label="Target",
            target_first_name="Target",
            target_last_name=None,
            target_chat_display_name="Target",
            actor_user_id=buyer_id,
            step="profile",
            expires_at=now + timedelta(minutes=10),
        ),
    )
    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(
            return_value=PaymentResult(
                "applied",
                chat_id=-100,
                valid_until=now + timedelta(days=30),
            )
        ),
        get_chat_title=AsyncMock(return_value="Test group"),
        claim_rejected_payment_refund=AsyncMock(return_value=PaymentRefundClaim("not_found")),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)

    async def fake_answer(_message, *_args, **_kwargs):
        return None

    monkeypatch.setattr(Message, "answer", fake_answer)
    router = build_router(session_factory, activity_batcher=SimpleNamespace())
    used = set(router.resolve_used_update_types())
    assert "message_reaction" in used
    assert "message_reaction_count" in used
    assert router.sub_routers[0].name == "selara_ai_payments"
    assert router.sub_routers[1].name == "application"

    bot = Bot(token="123:TEST")
    settings = Settings(
        bot_token="123:TEST",
        database_url="sqlite+aiosqlite:///:memory:",
        bot_timezone="UTC",
        admin_user_id=buyer_id,
    )
    message = Message(
        message_id=1,
        date=now,
        chat=Chat(id=buyer_id, type="private"),
        from_user=User(id=buyer_id, is_bot=False, first_name="Buyer"),
        successful_payment=SuccessfulPayment(
            currency="XTR",
            total_amount=137,
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-test",
            provider_payment_charge_id="",
        ),
    )
    async with session_factory() as session:
        assert await autoconfig.DraftMessageFilter()(message, db_session=session)
    assert await stats.PendingIrisImportFilter()(message)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        await dispatcher.feed_update(
            bot,
            Update(update_id=1, message=message),
            session_factory=session_factory,
            settings=settings,
        )
        repository.process_successful_payment.assert_awaited_once()
        assert stats._get_pending_iris_import(buyer_id) is not None
        async with session_factory() as session:
            assert await autoconfig.AutoConfigRepository(session).get(buyer_id) is not None

        refund_command = Message(
            message_id=2,
            date=now,
            chat=Chat(id=buyer_id, type="private"),
            from_user=User(id=buyer_id, is_bot=False, first_name="Buyer"),
            text="/stars_refund 17",
        )
        await dispatcher.feed_update(
            bot,
            Update(update_id=2, message=refund_command),
            session_factory=session_factory,
            settings=settings,
        )
        repository.claim_rejected_payment_refund.assert_awaited_once_with(
            payment_id=17,
            requested_by_user_id=buyer_id,
        )
    finally:
        stats._clear_pending_iris_import(buyer_id)
        await bot.session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_reaction_handler_keeps_standard_custom_and_paid_reactions() -> None:
    repo = SimpleNamespace(replace_admin_broadcast_native_reactions=AsyncMock(return_value=True))
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type="supergroup", title="Reactions"),
        user=SimpleNamespace(
            id=701,
            username="reactor",
            first_name="Reactor",
            last_name=None,
            is_bot=False,
        ),
        actor_chat=None,
        message_id=9002,
        date=datetime(2026, 8, 14, tzinfo=UTC),
        new_reaction=[
            SimpleNamespace(type="emoji", emoji="❤"),
            SimpleNamespace(type="custom_emoji", custom_emoji_id="5368324170671202286"),
            SimpleNamespace(type="paid"),
        ],
    )

    await admin_broadcast_native_reaction(update, repo)

    call = repo.replace_admin_broadcast_native_reactions.await_args.kwargs
    assert call["user"].telegram_user_id == 701
    assert call["actor_chat_id"] is None
    reactions = call["reactions"]
    assert {
        (item.reaction_type, item.value, item.display)
        for item in reactions
    } == {
        ("emoji", "❤", "❤"),
        ("custom_emoji", "5368324170671202286", "✨"),
        ("paid", "paid", "⭐"),
    }


@pytest.mark.asyncio
async def test_native_reaction_handler_accepts_chat_actor_without_user() -> None:
    repo = SimpleNamespace(replace_admin_broadcast_native_reactions=AsyncMock(return_value=True))
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type="supergroup", title="Reactions"),
        user=None,
        actor_chat=SimpleNamespace(id=-1007999),
        message_id=9002,
        date=datetime(2026, 8, 14, tzinfo=UTC),
        new_reaction=[SimpleNamespace(type="emoji", emoji="🔥")],
    )

    await admin_broadcast_native_reaction(update, repo)

    call = repo.replace_admin_broadcast_native_reactions.await_args.kwargs
    assert call["user"] is None
    assert call["actor_chat_id"] == -1007999
    assert {(item.reaction_type, item.value) for item in call["reactions"]} == {
        ("emoji", "🔥")
    }


@pytest.mark.asyncio
async def test_native_reaction_count_handler_keeps_all_reaction_types() -> None:
    repo = SimpleNamespace(replace_admin_broadcast_reaction_counts=AsyncMock(return_value=True))
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001),
        message_id=9002,
        date=datetime(2026, 8, 14, tzinfo=UTC),
        reactions=[
            SimpleNamespace(type=SimpleNamespace(type="emoji", emoji="❤"), total_count=3),
            SimpleNamespace(
                type=SimpleNamespace(type="custom_emoji", custom_emoji_id="5368324170671202286"),
                total_count=2,
            ),
            SimpleNamespace(type=SimpleNamespace(type="paid"), total_count=1),
        ],
    )

    await admin_broadcast_native_reaction_count(update, repo)

    totals = repo.replace_admin_broadcast_reaction_counts.await_args.kwargs["reactions"]
    assert {
        (item.reaction.reaction_type, item.reaction.value, item.reaction.display, item.count)
        for item in totals
    } == {
        ("emoji", "❤", "❤", 3),
        ("custom_emoji", "5368324170671202286", "✨", 2),
        ("paid", "paid", "⭐", 1),
    }
