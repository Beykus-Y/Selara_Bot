from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from redis.exceptions import ConnectionError, TimeoutError

from selara.infrastructure.stt import cooldown


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_result,expected", [(True, True), (None, False)])
async def test_admission_uses_atomic_nx_with_expiring_millisecond_ttl(monkeypatch, redis_result, expected):
    redis = AsyncMock()
    redis.__aenter__.return_value = redis
    redis.set.return_value = redis_result
    factory = Mock(return_value=redis)
    monkeypatch.setattr(cooldown, "Redis", SimpleNamespace(from_url=factory))
    assert await cooldown.claim_stt_cooldown(
        redis_url="redis://test", chat_id=-100, user_id=7, cooldown_seconds=0.0015,
    ) is expected
    redis.set.assert_awaited_once_with("selara:stt:cooldown:-100:7", "1", nx=True, px=2)
    factory.assert_called_once_with("redis://test", socket_connect_timeout=2, socket_timeout=2)
    redis.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError("secret"), TimeoutError("secret"), OSError("secret")])
async def test_admission_fails_closed_on_storage_failure_without_logging_credentials(monkeypatch, caplog, error):
    redis = AsyncMock()
    redis.__aenter__.return_value = redis
    redis.set.side_effect = error
    monkeypatch.setattr(cooldown, "Redis", SimpleNamespace(from_url=Mock(return_value=redis)))
    assert not await cooldown.claim_stt_cooldown(
        redis_url="redis://:secret@test", chat_id=-100, user_id=7, cooldown_seconds=8,
    )
    assert "refusing request" in caplog.text
    assert "secret" not in caplog.text
    redis.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_zero_cooldown_explicitly_disables_admission_without_storage(monkeypatch):
    factory = Mock(side_effect=AssertionError("Redis must not be opened"))
    monkeypatch.setattr(cooldown, "Redis", SimpleNamespace(from_url=factory))
    assert await cooldown.claim_stt_cooldown(redis_url="redis://test", chat_id=1, user_id=1, cooldown_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("message_type", ["voice", "video_note"])
async def test_storage_denial_stops_handler_before_download_or_paid_call(monkeypatch, message_type):
    from selara.core.config import Settings
    from selara.presentation.handlers import voice

    admission = AsyncMock(return_value=False)
    monkeypatch.setattr(voice, "claim_stt_cooldown", admission)
    message = SimpleNamespace(
        **{message_type: SimpleNamespace(file_id="audio", file_size=10)},
        chat=SimpleNamespace(id=-100), from_user=SimpleNamespace(id=7), reply=AsyncMock(),
    )
    bot, client = AsyncMock(), AsyncMock()
    handler = voice.voice_message_handler if message_type == "voice" else voice.video_note_message_handler
    await handler(message, bot, client, Settings())
    admission.assert_awaited_once()
    bot.get_file.assert_not_awaited()
    bot.download_file.assert_not_awaited()
    client.transcribe_with_retry.assert_not_awaited()
    message.reply.assert_not_awaited()
