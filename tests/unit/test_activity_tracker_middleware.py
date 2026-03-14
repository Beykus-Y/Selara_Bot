from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from selara.presentation.middlewares.activity_tracker import ActivityTrackerMiddleware, _is_profile_lookup_message


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


def _event(*, text: str = "hello", chat_type: str = "group") -> Message:
    message = AsyncMock(spec=Message)
    message.text = text
    message.chat = SimpleNamespace(id=101, type=chat_type, title="Test Chat")
    message.from_user = SimpleNamespace(
        id=501,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
    )
    message.date = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)
    message.message_id = 777
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
