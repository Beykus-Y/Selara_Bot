"""Tests that voice.py's daily-summary enqueue hook is correctly wired and fully
decoupled from the instant transcribe-and-reply feature (docs/DAILY_SUMMARY_TODO.md).
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from selara.application.daily_summary.transcription import TranscriptionJob
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers import voice as voice_module
from selara.presentation.handlers.voice import video_note_message_handler, voice_message_handler


def _settings() -> Settings:
    return Settings.model_validate(
        {"BOT_TOKEN": "123456:TEST", "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test"}
    )


def _chat_settings(**overrides):
    return replace(default_chat_settings(_settings()), save_message=True, **overrides)


def _voice_message(*, chat_type: str = "supergroup", duration: int = 12):
    status = SimpleNamespace(edit_text=AsyncMock())
    return SimpleNamespace(
        voice=SimpleNamespace(file_id="voice-id", file_size=1000, duration=duration),
        reply=AsyncMock(return_value=status),
        chat=SimpleNamespace(id=-100, type=chat_type),
        from_user=SimpleNamespace(id=1),
        message_id=555,
    )


def _fake_bot():
    return SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="voice.ogg")),
        download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"voice")),
    )


@pytest.fixture(autouse=True)
def _clear_cooldown():
    voice_module._last_request_at.clear()
    yield
    voice_module._last_request_at.clear()


@pytest.mark.asyncio
async def test_voice_handler_enqueues_daily_summary_job_when_enabled() -> None:
    message = _voice_message(duration=12)
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))
    queue = SimpleNamespace(enqueue=Mock())

    await voice_message_handler(
        message,
        bot=bot,
        stt_client=stt_client,
        settings=_settings(),
        chat_settings=_chat_settings(daily_summary_include_voice=True),
        daily_summary_stt_queue=queue,
    )

    queue.enqueue.assert_called_once()
    job = queue.enqueue.call_args.args[0]
    assert job == TranscriptionJob(
        chat_id=-100, telegram_message_id=555, file_id="voice-id",
        filename="voice.ogg", message_type="voice", duration_seconds=12.0,
    )


@pytest.mark.asyncio
async def test_voice_handler_does_not_enqueue_when_toggle_disabled() -> None:
    message = _voice_message()
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))
    queue = SimpleNamespace(enqueue=Mock())

    await voice_message_handler(
        message, bot=bot, stt_client=stt_client, settings=_settings(),
        chat_settings=_chat_settings(daily_summary_include_voice=False),
        daily_summary_stt_queue=queue,
    )

    queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_voice_handler_does_not_enqueue_when_save_message_disabled() -> None:
    message = _voice_message()
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))
    queue = SimpleNamespace(enqueue=Mock())

    settings = replace(_chat_settings(daily_summary_include_voice=True), save_message=False)

    await voice_message_handler(
        message, bot=bot, stt_client=stt_client, settings=_settings(),
        chat_settings=settings, daily_summary_stt_queue=queue,
    )

    queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_voice_handler_does_not_enqueue_in_private_chat() -> None:
    message = _voice_message(chat_type="private")
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))
    queue = SimpleNamespace(enqueue=Mock())

    await voice_message_handler(
        message, bot=bot, stt_client=stt_client, settings=_settings(),
        chat_settings=_chat_settings(daily_summary_include_voice=True),
        daily_summary_stt_queue=queue,
    )

    queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_voice_handler_still_replies_to_user_when_daily_summary_disabled() -> None:
    # the instant-reply feature must keep working identically regardless of the
    # daily summary queue's presence/absence/toggle state
    message = _voice_message()
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))

    await voice_message_handler(
        message, bot=bot, stt_client=stt_client, settings=_settings(),
        chat_settings=None, daily_summary_stt_queue=None,
    )

    message.reply.assert_awaited()


@pytest.mark.asyncio
async def test_video_note_handler_enqueues_with_video_note_type() -> None:
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        video_note=SimpleNamespace(file_id="vn-id", file_size=1000, duration=30),
        reply=AsyncMock(return_value=status),
        chat=SimpleNamespace(id=-200, type="supergroup"),
        from_user=SimpleNamespace(id=9),
        message_id=777,
    )
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))
    queue = SimpleNamespace(enqueue=Mock())

    await video_note_message_handler(
        message, bot=bot, stt_client=stt_client, settings=_settings(),
        chat_settings=_chat_settings(daily_summary_include_video_notes=True),
        daily_summary_stt_queue=queue,
    )

    job = queue.enqueue.call_args.args[0]
    assert job.message_type == "video_note"
    assert job.duration_seconds == 30.0
    assert job.chat_id == -200
    assert job.telegram_message_id == 777


@pytest.mark.asyncio
async def test_video_note_handler_does_not_enqueue_for_voice_toggle_only() -> None:
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        video_note=SimpleNamespace(file_id="vn-id", file_size=1000, duration=30),
        reply=AsyncMock(return_value=status),
        chat=SimpleNamespace(id=-200, type="supergroup"),
        from_user=SimpleNamespace(id=9),
        message_id=778,
    )
    bot = _fake_bot()
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет"))
    queue = SimpleNamespace(enqueue=Mock())

    await video_note_message_handler(
        message, bot=bot, stt_client=stt_client, settings=_settings(),
        chat_settings=_chat_settings(daily_summary_include_voice=True, daily_summary_include_video_notes=False),
        daily_summary_stt_queue=queue,
    )

    queue.enqueue.assert_not_called()
