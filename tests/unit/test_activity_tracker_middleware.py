import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.client.default import Default
from aiogram.types import Chat, Message, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import AdminBroadcastTarget, ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.middlewares.activity_tracker import (
    ActivityTrackerMiddleware,
    _is_membership_service_message,
    _is_profile_lookup_message,
    _serialize_message,
)


def _msg(text: str):
    return SimpleNamespace(text=text)


def test_profile_lookup_detects_me_command() -> None:
    assert _is_profile_lookup_message(_msg("/me"))
    assert _is_profile_lookup_message(_msg("/me@selara_bot"))


def test_profile_lookup_detects_who_am_i_text() -> None:
    assert _is_profile_lookup_message(_msg("кто я"))
    assert _is_profile_lookup_message(_msg("Кто   Я?!"))


def test_profile_lookup_detects_who_are_you_text() -> None:
    assert _is_profile_lookup_message(_msg("кто ты"))
    assert _is_profile_lookup_message(_msg("кто ты @alice"))


def test_profile_lookup_ignores_regular_messages() -> None:
    assert not _is_profile_lookup_message(_msg("/help"))
    assert not _is_profile_lookup_message(_msg("кто я такой"))
    assert not _is_profile_lookup_message(_msg("кто ты такой"))


def test_membership_service_message_detects_join_and_leave() -> None:
    join_event = SimpleNamespace(new_chat_members=[SimpleNamespace(id=1)], left_chat_member=None)
    leave_event = SimpleNamespace(new_chat_members=[], left_chat_member=SimpleNamespace(id=1))
    regular_event = SimpleNamespace(new_chat_members=[], left_chat_member=None)

    assert _is_membership_service_message(join_event) is True
    assert _is_membership_service_message(leave_event) is True
    assert _is_membership_service_message(regular_event) is False


def test_serialize_message_preserves_aiogram_default_as_json_marker() -> None:
    message = Message(
        message_id=777,
        date=datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc),
        chat=Chat(id=101, type="group", title="Test Chat"),
        unresolved_parse_mode=Default("parse_mode"),
    )

    payload = _serialize_message(message)

    assert payload["unresolved_parse_mode"] == {"__aiogram_default__": "parse_mode"}


def _event(*, text: str = "hello", chat_type: str = "group") -> Message:
    message = AsyncMock(spec=Message)
    message.text = text
    message.caption = None
    message.chat = SimpleNamespace(id=101, type=chat_type, title="Test Chat")
    message.from_user = SimpleNamespace(
        id=501,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
    )
    message.date = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)
    message.edit_date = None
    message.message_id = 777
    message.content_type = "text"
    message.model_dump.return_value = {"message_id": 777, "text": text}
    message.new_chat_members = []
    message.left_chat_member = None
    return message


@pytest.mark.asyncio
async def test_activity_tracker_enqueues_after_successful_handler() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value="handled")
    event = _event()

    result = await middleware(
        handler,
        event,
        {"settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"})},
    )

    assert result == "handled"
    batcher.enqueue_message.assert_awaited_once_with(
        chat_id=101,
        chat_type="group",
        chat_title="Test Chat",
        user_id=501,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
        event_at=event.date,
        telegram_message_id=777,
        count_as_activity=True,
        snapshot_kind=None,
        snapshot_at=None,
        sent_at=None,
        edited_at=None,
        message_type=None,
        text=None,
        caption=None,
        raw_message_json=None,
        snapshot_hash=None,
        reply_to_telegram_message_id=None,
    )


@pytest.mark.asyncio
async def test_activity_tracker_archive_payload_captures_reply_to_message_id() -> None:
    # daily summary reply-thread reconstruction depends on this being captured at
    # archive time -- it is not derivable later without re-parsing raw_message_json.
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value="handled")
    event = _event(text="согласен")
    event.reply_to_message = SimpleNamespace(message_id=555)
    raw_payload = {"message_id": 777, "text": "согласен"}
    event.model_dump.return_value = raw_payload

    await middleware(
        handler,
        event,
        {
            "settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"}),
            "chat_settings": SimpleNamespace(save_message=True),
        },
    )

    _, kwargs = batcher.enqueue_message.await_args
    assert kwargs["reply_to_telegram_message_id"] == 555


@pytest.mark.asyncio
async def test_activity_tracker_enqueues_archive_payload_when_save_message_enabled() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value="handled")
    event = _event(text="/me")
    raw_payload = {"message_id": 777, "text": "/me", "chat": {"id": 101}}
    event.model_dump.return_value = raw_payload
    expected_hash = hashlib.sha256(
        json.dumps(raw_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()

    result = await middleware(
        handler,
        event,
        {
            "settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"}),
            "chat_settings": SimpleNamespace(save_message=True),
        },
    )

    assert result == "handled"
    batcher.enqueue_message.assert_awaited_once_with(
        chat_id=101,
        chat_type="group",
        chat_title="Test Chat",
        user_id=501,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
        event_at=event.date,
        telegram_message_id=777,
        count_as_activity=False,
        snapshot_kind="created",
        snapshot_at=event.date,
        sent_at=event.date,
        edited_at=None,
        message_type="text",
        text="/me",
        caption=None,
        raw_message_json=raw_payload,
        snapshot_hash=expected_hash,
        reply_to_telegram_message_id=None,
    )


@pytest.mark.asyncio
async def test_activity_tracker_enqueues_edited_message_as_archive_only() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value=None)
    event = _event(text="edited text")
    event.edit_date = datetime(2026, 3, 13, 12, 5, tzinfo=timezone.utc)
    raw_payload = {"message_id": 777, "text": "edited text", "edit_date": "2026-03-13T12:05:00Z"}
    event.model_dump.return_value = raw_payload
    expected_hash = hashlib.sha256(
        json.dumps(raw_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()

    await middleware(
        handler,
        event,
        {
            "settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"}),
            "chat_settings": SimpleNamespace(save_message=True),
        },
    )

    batcher.enqueue_message.assert_awaited_once_with(
        chat_id=101,
        chat_type="group",
        chat_title="Test Chat",
        user_id=501,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
        event_at=event.date,
        telegram_message_id=777,
        count_as_activity=False,
        snapshot_kind="edited",
        snapshot_at=event.edit_date,
        sent_at=event.date,
        edited_at=event.edit_date,
        message_type="text",
        text="edited text",
        caption=None,
        raw_message_json=raw_payload,
        snapshot_hash=expected_hash,
        reply_to_telegram_message_id=None,
    )


@pytest.mark.asyncio
async def test_activity_tracker_skips_profile_lookup_messages() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value=None)

    await middleware(
        handler,
        _event(text="/me"),
        {"settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"})},
    )

    batcher.enqueue_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_tracker_skips_membership_service_messages() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value=None)
    event = _event(text=None)
    event.left_chat_member = SimpleNamespace(id=501)

    await middleware(
        handler,
        event,
        {"settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"})},
    )

    batcher.enqueue_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_tracker_does_not_enqueue_when_handler_raises() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)

    async def _raise_handler(_event, _data):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(
            _raise_handler,
            _event(),
            {"settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"})},
        )

    batcher.enqueue_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_tracker_records_reply_to_admin_broadcast() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    activity_repo = SimpleNamespace(record_admin_broadcast_reply=AsyncMock(return_value=True))
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value="handled")
    event = _event(text="Спасибо вам тоже")
    event.reply_to_message = SimpleNamespace(message_id=333)

    result = await middleware(
        handler,
        event,
        {
            "settings": SimpleNamespace(supported_chat_types={"private", "group", "supergroup"}),
            "activity_repo": activity_repo,
        },
    )

    assert result == "handled"
    activity_repo.record_admin_broadcast_reply.assert_awaited_once_with(
        chat=ChatSnapshot(telegram_chat_id=101, chat_type="group", title="Test Chat"),
        user=UserSnapshot(
            telegram_user_id=501,
            username="alice",
            first_name="Alice",
            last_name="Doe",
            is_bot=False,
        ),
        reply_to_message_id=333,
        telegram_message_id=777,
        message_type="text",
        text="Спасибо вам тоже",
        caption=None,
        raw_message_json={"message_id": 777, "text": "Спасибо вам тоже"},
        sent_at=event.date,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")
async def test_activity_tracker_persists_reply_for_existing_broadcast_delivery() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sent_at = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)
    chat_snapshot = ChatSnapshot(telegram_chat_id=101, chat_type="group", title="Test Chat")
    user_snapshot = UserSnapshot(
        telegram_user_id=501,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
    )

    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await repo.upsert_activity(chat=chat_snapshot, user=user_snapshot, event_at=sent_at)
            broadcast = await repo.create_admin_broadcast(
                body="Broadcast",
                active_since_days=30,
                created_by_user_id=77,
            )
            deliveries = await repo.create_admin_broadcast_deliveries(
                broadcast_id=broadcast.id,
                targets=[
                    AdminBroadcastTarget(
                        chat_id=101,
                        chat_type="group",
                        chat_title="Test Chat",
                        last_activity_at=sent_at,
                    )
                ],
            )
            await repo.mark_admin_broadcast_delivery_sent(
                delivery_id=deliveries[0].id,
                telegram_message_id=333,
                sent_at=sent_at,
            )

            chat = Chat(id=101, type="group", title="Test Chat")
            event = Message(
                message_id=777,
                date=sent_at,
                chat=chat,
                from_user=User(
                    id=501,
                    is_bot=False,
                    first_name="Alice",
                    last_name="Doe",
                    username="alice",
                ),
                text="Reply",
                reply_to_message=Message(
                    message_id=333,
                    date=sent_at,
                    chat=chat,
                    text="Broadcast",
                ),
                unresolved_parse_mode=Default("parse_mode"),
            )
            middleware = ActivityTrackerMiddleware(SimpleNamespace(enqueue_message=AsyncMock()))

            result = await middleware(
                AsyncMock(return_value="handled"),
                event,
                {
                    "settings": SimpleNamespace(supported_chat_types={"group"}),
                    "activity_repo": repo,
                },
            )

            replies = await repo.list_admin_broadcast_replies(broadcast_id=broadcast.id)
            assert result == "handled"
            assert len(replies) == 1
            assert replies[0].delivery_id == deliveries[0].id
            assert replies[0].telegram_message_id == 777
            assert replies[0].text == "Reply"
    finally:
        await engine.dispose()
