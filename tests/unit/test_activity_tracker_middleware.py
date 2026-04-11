import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.presentation.middlewares.activity_tracker import (
    ActivityTrackerMiddleware,
    _is_membership_service_message,
    _is_profile_lookup_message,
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
    message.model_dump_json.return_value = json.dumps({"message_id": 777, "text": text}, ensure_ascii=False)
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
    )


@pytest.mark.asyncio
async def test_activity_tracker_enqueues_archive_payload_when_save_message_enabled() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value="handled")
    event = _event(text="/me")
    raw_payload = {"message_id": 777, "text": "/me", "chat": {"id": 101}}
    event.model_dump_json.return_value = json.dumps(raw_payload, ensure_ascii=False)
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
    )


@pytest.mark.asyncio
async def test_activity_tracker_enqueues_edited_message_as_archive_only() -> None:
    batcher = SimpleNamespace(enqueue_message=AsyncMock())
    middleware = ActivityTrackerMiddleware(batcher)
    handler = AsyncMock(return_value=None)
    event = _event(text="edited text")
    event.edit_date = datetime(2026, 3, 13, 12, 5, tzinfo=timezone.utc)
    raw_payload = {"message_id": 777, "text": "edited text", "edit_date": "2026-03-13T12:05:00Z"}
    event.model_dump_json.return_value = json.dumps(raw_payload, ensure_ascii=False)
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
