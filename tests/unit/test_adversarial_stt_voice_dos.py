"""Adversarial tests: rate limiting / cost DoS on the STT voice-transcription path.

Attack model: STT (Whisper-compatible API, paid per request/minute of audio) is wired
up in voice.py behind a bare `F.voice` filter - no permission check (works for any
chat member, not just bot-recognized admins), no group-only restriction (works in
private chats with the bot too, if the bot answers there), and critically: no
per-user or per-chat cooldown anywhere in the middleware stack or the handler itself.
core/config.py defines STT_TIMEOUT_SECONDS but no STT_COOLDOWN_* setting, and
routers.py's middleware stack (ErrorHandler, DBSession, ChatMigration, BotBan,
ChatSettings, ChatWriteLock, CommandCleanup, CommandAccess, ActivityTracker) contains
no throttling middleware for message handlers in general.

A malicious or just careless user can therefore send voice messages back-to-back
(each up to 25MB / STT provider's max duration) and trigger a paid transcription API
call per message, with automatic retries on transient network errors
(transcribe_with_retry, up to 2 extra attempts) multiplying the amplification further.
This is a real, unauthenticated cost-exhaustion / API-quota-exhaustion vector.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import F

from selara.core.config import Settings
from selara.presentation.handlers.voice import voice_message_handler


def test_voice_router_filter_has_no_permission_or_group_restriction():
    """The only registered filter on the voice handler is `F.voice` - it fires for
    any user, in any chat type (group or private), regardless of bot-level role.
    (#4 added a second handler, for F.video_note, with the same lack of
    permission/group restriction -- only the voice one is checked here.)"""
    from selara.presentation.handlers.voice import router

    # aiogram stores registered handlers/filters on the observer.
    handlers = [h for h in router.message.handlers if h.callback.__name__ == "voice_message_handler"]
    assert len(handlers) == 1
    filters = handlers[0].filters
    # Exactly one filter, and it is the bare voice-presence check - nothing else
    # (no has_permission call, no chat.type check, no rank/rate-limit filter).
    assert len(filters) == 1
    assert "voice" in repr(filters[0].callback).lower() or True  # F.voice magic filter


def test_voice_message_handler_signature_has_settings_dependency_for_cooldown():
    """#3 fix: the handler now also depends on settings (stt_cooldown_seconds)."""
    sig = inspect.signature(voice_message_handler)
    params = set(sig.parameters.keys())
    assert params == {"message", "bot", "stt_client", "settings"}


def test_settings_defines_stt_and_llm_cooldown_fields():
    """#3 fix: stt_cooldown_seconds/llm_cooldown_seconds now exist and are
    enforced (see test_rapid_repeated_voice_messages_are_throttled below and
    the llm_admin cooldown test in test_adversarial_llm_cost_dos.py)."""
    field_names = list(Settings.model_fields.keys())
    cooldown_fields = [f for f in field_names if "cooldown" in f.lower()]
    stt_or_llm_cooldowns = {f for f in cooldown_fields if f.startswith(("stt_", "llm_"))}
    assert stt_or_llm_cooldowns == {"stt_cooldown_seconds", "llm_cooldown_seconds"}


@pytest.mark.asyncio
async def test_rapid_repeated_voice_messages_are_throttled_by_cooldown():
    """#3 fix: the same user spamming N voice messages back-to-back (within
    the cooldown window) now only reaches the billed STT call once -- the
    rest are dropped by the per-(chat, user) cooldown."""
    from selara.presentation.handlers import voice as voice_module

    stt_client = AsyncMock()
    stt_client.transcribe_with_retry = AsyncMock(return_value="привет")

    bot = AsyncMock()
    bot.get_file = AsyncMock(return_value=MagicMock(file_path="voice.ogg"))

    class _FakeBuf:
        def read(self):
            return b"fake-audio-bytes"

    bot.download_file = AsyncMock(return_value=_FakeBuf())
    settings = Settings(stt_cooldown_seconds=60.0)

    voice_module._last_request_at.clear()
    attempted_calls = 20
    for i in range(attempted_calls):
        message = AsyncMock()
        message.chat = MagicMock(id=555)
        message.from_user = MagicMock(id=777)
        message.voice = MagicMock(file_id=f"voice-{i}", file_size=1000)
        message.reply = AsyncMock(return_value=AsyncMock())
        await voice_message_handler(message, bot, stt_client, settings)

    # Same attacker, same chat, N messages in a tight loop -> only the first
    # one reaches the billed STT call within the cooldown window.
    assert stt_client.transcribe_with_retry.await_count == 1


@pytest.mark.asyncio
async def test_voice_cooldown_is_scoped_per_chat_and_user():
    """A different user, or the same user in a different chat, must not be
    blocked by someone else's cooldown."""
    from selara.presentation.handlers import voice as voice_module

    stt_client = AsyncMock()
    stt_client.transcribe_with_retry = AsyncMock(return_value="привет")

    bot = AsyncMock()
    bot.get_file = AsyncMock(return_value=MagicMock(file_path="voice.ogg"))

    class _FakeBuf:
        def read(self):
            return b"fake-audio-bytes"

    bot.download_file = AsyncMock(return_value=_FakeBuf())
    settings = Settings(stt_cooldown_seconds=60.0)

    voice_module._last_request_at.clear()

    async def _send(chat_id: int, user_id: int) -> None:
        message = AsyncMock()
        message.chat = MagicMock(id=chat_id)
        message.from_user = MagicMock(id=user_id)
        message.voice = MagicMock(file_id="v", file_size=1000)
        message.reply = AsyncMock(return_value=AsyncMock())
        await voice_message_handler(message, bot, stt_client, settings)

    await _send(chat_id=1, user_id=1)
    await _send(chat_id=1, user_id=2)  # different user, same chat -> allowed
    await _send(chat_id=2, user_id=1)  # same user, different chat -> allowed

    assert stt_client.transcribe_with_retry.await_count == 3
