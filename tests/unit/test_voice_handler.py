from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.config import Settings
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
        chat=SimpleNamespace(id=1),
        from_user=SimpleNamespace(id=1),
    )
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="voice.ogg")),
        download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"voice")),
    )
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="word " * 2000))

    from selara.presentation.handlers import voice as voice_module
    voice_module._last_request_at.clear()

    await voice_message_handler(message, bot=bot, stt_client=stt_client, settings=Settings())

    status.edit_text.assert_awaited_once()
    assert len(status.edit_text.await_args.args[0]) <= 4000
    assert "parse_mode" not in status.edit_text.await_args.kwargs
    assert message.reply.await_count > 1
    assert all(len(call.args[0]) <= 4000 for call in message.reply.await_args_list[1:])


# --- #4: video circle messages ("кружки") were never transcribed ---


@pytest.mark.asyncio
async def test_video_note_handler_transcribes_and_replies() -> None:
    from selara.presentation.handlers.voice import video_note_message_handler
    from selara.presentation.handlers import voice as voice_module
    voice_module._last_request_at.clear()

    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        video_note=SimpleNamespace(file_id="video-note-id"),
        reply=AsyncMock(return_value=status),
        chat=SimpleNamespace(id=42),
        from_user=SimpleNamespace(id=7),
    )
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="video_note.mp4")),
        download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"video-bytes")),
    )
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="привет из кружка"))

    await video_note_message_handler(message, bot=bot, stt_client=stt_client, settings=Settings())

    stt_client.transcribe_with_retry.assert_awaited_once()
    call_kwargs = stt_client.transcribe_with_retry.await_args.kwargs
    assert call_kwargs["filename"].endswith(".mp4")
    status.edit_text.assert_awaited_once_with("привет из кружка")


@pytest.mark.asyncio
async def test_video_note_handler_shares_the_stt_cooldown_with_voice() -> None:
    from selara.presentation.handlers.voice import video_note_message_handler
    from selara.presentation.handlers import voice as voice_module
    voice_module._last_request_at.clear()

    status = SimpleNamespace(edit_text=AsyncMock())
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="video_note.mp4")),
        download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"video-bytes")),
    )
    stt_client = SimpleNamespace(transcribe_with_retry=AsyncMock(return_value="text"))
    settings = Settings(stt_cooldown_seconds=60.0)

    def _message():
        return SimpleNamespace(
            video_note=SimpleNamespace(file_id="v"),
            reply=AsyncMock(return_value=status),
            chat=SimpleNamespace(id=42),
            from_user=SimpleNamespace(id=7),
        )

    await video_note_message_handler(_message(), bot=bot, stt_client=stt_client, settings=settings)
    await video_note_message_handler(_message(), bot=bot, stt_client=stt_client, settings=settings)

    assert stt_client.transcribe_with_retry.await_count == 1
