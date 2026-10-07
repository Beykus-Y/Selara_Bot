from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from selara.core.chat_settings import CHAT_SETTINGS_KEYS, default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers import voice
from selara.presentation.handlers.settings_common import (
    CFG_BOOL_KEYS, apply_setting_update, settings_to_dict,
)
from selara.web.presenters import build_settings_sections


def settings():
    return Settings(bot_token="123456:TEST", database_url="postgresql+asyncpg://user:pass@localhost/test")


def message(kind, chat_id):
    return SimpleNamespace(
        **{kind: SimpleNamespace(file_id="audio-id", file_size=1000, duration=10)},
        chat=SimpleNamespace(id=chat_id, type="supergroup"), from_user=SimpleNamespace(id=1),
        message_id=123, reply=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
    )


@pytest.mark.parametrize("kind", ["voice", "video_note"])
@pytest.mark.parametrize("daily_enabled", [False, True])
async def test_disabled_instant_stt_preserves_independent_daily_ingestion(monkeypatch, kind, daily_enabled):
    cooldown = AsyncMock(return_value=True)
    monkeypatch.setattr(voice, "claim_stt_cooldown", cooldown)
    bot = SimpleNamespace(get_file=AsyncMock(), download_file=AsyncMock())
    stt = SimpleNamespace(transcribe_with_retry=AsyncMock())
    queue = SimpleNamespace(enqueue=Mock())
    chat = replace(default_chat_settings(settings()), instant_stt_enabled=False, save_message=True,
                   daily_summary_include_voice=daily_enabled, daily_summary_include_video_notes=daily_enabled)
    event = message(kind, -100)
    handler = voice.voice_message_handler if kind == "voice" else voice.video_note_message_handler
    await handler(event, bot=bot, stt_client=stt, settings=settings(), chat_settings=chat,
                  daily_summary_stt_queue=queue)
    bot.get_file.assert_not_awaited()
    bot.download_file.assert_not_awaited()
    stt.transcribe_with_retry.assert_not_awaited()
    event.reply.assert_not_awaited()
    cooldown.assert_not_awaited()
    assert queue.enqueue.call_count == int(daily_enabled)


@pytest.mark.parametrize("kind", ["voice", "video_note"])
async def test_other_chat_keeps_instant_stt_enabled(monkeypatch, kind):
    monkeypatch.setattr(voice, "claim_stt_cooldown", AsyncMock(return_value=True))
    bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path="audio.ogg")),
                          download_file=AsyncMock(return_value=b"audio"))
    stt = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="Текст"))
    enabled = default_chat_settings(settings())
    assert enabled.instant_stt_enabled is True
    handler = voice.voice_message_handler if kind == "voice" else voice.video_note_message_handler
    await handler(message(kind, -100), bot=bot, stt_client=stt, settings=settings(),
                  chat_settings=replace(enabled, instant_stt_enabled=False))
    await handler(message(kind, -200), bot=bot, stt_client=stt, settings=settings(), chat_settings=enabled)
    bot.download_file.assert_awaited_once()
    stt.transcribe_with_retry.assert_awaited_once()


@pytest.mark.parametrize("kind", ["voice", "video_note"])
async def test_chat_settings_database_failure_cannot_reenable_paid_instant_stt(monkeypatch, kind):
    cooldown = AsyncMock(return_value=True)
    monkeypatch.setattr(voice, "claim_stt_cooldown", cooldown)
    bot = SimpleNamespace(get_file=AsyncMock(), download_file=AsyncMock())
    stt = SimpleNamespace(transcribe_with_retry=AsyncMock())
    handler = voice.voice_message_handler if kind == "voice" else voice.video_note_message_handler
    await handler(message(kind, -100), bot=bot, stt_client=stt, settings=settings(),
                  chat_settings=default_chat_settings(settings()), settings_source="default_after_db_error")
    cooldown.assert_not_awaited()
    bot.get_file.assert_not_awaited()
    stt.transcribe_with_retry.assert_not_awaited()


def test_setting_can_be_changed_and_reset_through_shared_chat_settings_surface():
    defaults = default_chat_settings(settings())
    baseline = settings_to_dict(defaults)
    assert "instant_stt_enabled" in CHAT_SETTINGS_KEYS and "instant_stt_enabled" in CFG_BOOL_KEYS
    changed, error = apply_setting_update(key="instant_stt_enabled", raw_value="false", current=baseline, defaults=baseline)
    assert error is None and changed["instant_stt_enabled"] is False
    reset, error = apply_setting_update(key="instant_stt_enabled", raw_value="default", current=changed, defaults=baseline)
    assert error is None and reset["instant_stt_enabled"] is True
    sections = build_settings_sections(current=replace(defaults, instant_stt_enabled=False), defaults=defaults, editable=True)
    item = next(row for section in sections for row in section["items"] if row["key"] == "instant_stt_enabled")
    assert item["input_kind"] == "toggle" and item["editable"] is True
    assert next(option for option in item["options"] if option["value"] == "false")["selected"] is True
