from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers.voice import _split_transcription, voice_message_handler


def test_split_transcription_splits_long_unbroken_text_within_telegram_limit() -> None:
    chunks = _split_transcription("x" * 9001)

    assert "".join(chunks) == "x" * 9001
    assert all(len(chunk) <= 4000 for chunk in chunks)


@pytest.mark.asyncio
async def test_voice_message_handler_sends_long_transcription_in_plain_text_chunks() -> None:
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        voice=SimpleNamespace(file_id="voice-id"),
        reply=AsyncMock(return_value=status),
    )
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="voice.ogg")),
        download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"voice")),
    )
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="word " * 2000))

    await voice_message_handler(message, bot=bot, stt_client=stt_client)

    status.edit_text.assert_awaited_once()
    assert len(status.edit_text.await_args.args[0]) <= 4000
    assert "parse_mode" not in status.edit_text.await_args.kwargs
    assert message.reply.await_count > 1
    assert all(len(call.args[0]) <= 4000 for call in message.reply.await_args_list[1:])
