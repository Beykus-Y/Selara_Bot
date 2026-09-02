from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.types import Message

from selara.application.daily_summary.transcription import TranscriptionJob, is_transcription_enabled
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.infrastructure.stt import SttClient, SttClientError
from selara.infrastructure.stt.client import _MAX_FILE_SIZE
from selara.infrastructure.stt.daily_summary_queue import DailySummaryTranscriptionQueue

router = Router(name="voice")
log = logging.getLogger(__name__)

_PENDING_TEXT = "🎙 Распознаю..."

# #3: voice.py has no permission/group gate at all, so this in-process
# per-(chat, user) cooldown is the only thing standing between a careless
# or malicious user and unlimited paid Whisper calls. Known limitation:
# resets on restart and isn't shared across multiple bot processes -- a
# durable, per-chat-configurable version (mirroring economy_tap_cooldown_seconds)
# is a reasonable fast-follow if that ever matters at this bot's scale.
_last_request_at: dict[tuple[int, int], float] = {}


def _split_transcription(text: str, *, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, max_len + 1)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


async def _transcribe_and_reply(
    message: Message, bot: Bot, stt_client: SttClient, settings: Settings,
    *, file_id: str, filename: str, file_size: int | None,
) -> None:
    if message.from_user is not None:
        key = (message.chat.id, message.from_user.id)
        now = time.monotonic()
        last = _last_request_at.get(key)
        if last is not None and (now - last) < settings.stt_cooldown_seconds:
            return
        _last_request_at[key] = now

    # #12: Telegram reports file_size on the message itself -- check it
    # before downloading anything, instead of downloading a file that's
    # going to be rejected by _validate_audio's size check afterward anyway.
    if file_size is not None and file_size > _MAX_FILE_SIZE:
        mb = file_size / (1024 * 1024)
        await message.reply(f"❌ Файл слишком большой ({mb:.1f} МБ). Максимум — 25 МБ.")
        return

    status = await message.reply(_PENDING_TEXT)

    try:
        file = await bot.get_file(file_id)
        audio_bytes = await bot.download_file(file.file_path)  # type: ignore[arg-type]
        raw = audio_bytes.read() if hasattr(audio_bytes, "read") else bytes(audio_bytes)
    except Exception as exc:
        log.warning("voice: не удалось скачать файл: %s", exc)
        await status.edit_text("❌ Не удалось загрузить голосовое сообщение.")
        return

    try:
        text = await stt_client.transcribe_with_retry(raw, filename=filename)
    except SttClientError as exc:
        log.warning("voice: STT ошибка: %s", exc.message)
        await status.edit_text(f"❌ Не удалось распознать: {exc.message}")
        return

    chunks = _split_transcription(text)
    await status.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await message.reply(chunk)


def _maybe_enqueue_for_daily_summary(
    message: Message,
    *,
    chat_settings: ChatSettings | None,
    daily_summary_stt_queue: DailySummaryTranscriptionQueue | None,
    message_type: str,
    file_id: str,
    filename: str,
    duration: int | None,
) -> None:
    """Fire-and-forget enqueue for the daily summary's own transcription --
    entirely separate from the instant reply above: it never blocks it, never
    reuses its result, and a failure here is invisible to the user. See
    infrastructure/stt/daily_summary_queue.py for why this can't just transcribe
    inline (the archive row for this message doesn't exist yet at this point)."""
    if daily_summary_stt_queue is None or chat_settings is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not chat_settings.save_message or not is_transcription_enabled(chat_settings, message_type=message_type):
        return
    daily_summary_stt_queue.enqueue(
        TranscriptionJob(
            chat_id=message.chat.id,
            telegram_message_id=message.message_id,
            file_id=file_id,
            filename=filename,
            message_type=message_type,
            duration_seconds=float(duration or 0),
        )
    )


@router.message(F.voice)
async def voice_message_handler(
    message: Message,
    bot: Bot,
    stt_client: SttClient,
    settings: Settings,
    chat_settings: ChatSettings | None = None,
    daily_summary_stt_queue: DailySummaryTranscriptionQueue | None = None,
) -> None:
    voice = message.voice
    if voice is None:
        return
    _maybe_enqueue_for_daily_summary(
        message,
        chat_settings=chat_settings,
        daily_summary_stt_queue=daily_summary_stt_queue,
        message_type="voice",
        file_id=voice.file_id,
        filename="voice.ogg",
        duration=getattr(voice, "duration", None),
    )
    await _transcribe_and_reply(
        message, bot, stt_client, settings, file_id=voice.file_id, filename="voice.ogg", file_size=voice.file_size,
    )


@router.message(F.video_note)
async def video_note_message_handler(
    message: Message,
    bot: Bot,
    stt_client: SttClient,
    settings: Settings,
    chat_settings: ChatSettings | None = None,
    daily_summary_stt_queue: DailySummaryTranscriptionQueue | None = None,
) -> None:
    """#4: video circle messages ("кружки") carry an audio track in the same
    mp4 container Telegram voice notes don't use but Whisper already
    accepts (see SttClient._validate_audio's allowed formats) -- no
    separate audio-extraction step needed, just pass the file through."""
    video_note = message.video_note
    if video_note is None:
        return
    _maybe_enqueue_for_daily_summary(
        message,
        chat_settings=chat_settings,
        daily_summary_stt_queue=daily_summary_stt_queue,
        message_type="video_note",
        file_id=video_note.file_id,
        filename="video_note.mp4",
        duration=getattr(video_note, "duration", None),
    )
    await _transcribe_and_reply(
        message, bot, stt_client, settings,
        file_id=video_note.file_id, filename="video_note.mp4", file_size=video_note.file_size,
    )
