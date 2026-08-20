from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.types import Message

from selara.core.config import Settings
from selara.infrastructure.stt import SttClient, SttClientError
from selara.infrastructure.stt.client import _MAX_FILE_SIZE

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


@router.message(F.voice)
async def voice_message_handler(message: Message, bot: Bot, stt_client: SttClient, settings: Settings) -> None:
    voice = message.voice
    if voice is None:
        return
    await _transcribe_and_reply(
        message, bot, stt_client, settings, file_id=voice.file_id, filename="voice.ogg", file_size=voice.file_size,
    )


@router.message(F.video_note)
async def video_note_message_handler(message: Message, bot: Bot, stt_client: SttClient, settings: Settings) -> None:
    """#4: video circle messages ("кружки") carry an audio track in the same
    mp4 container Telegram voice notes don't use but Whisper already
    accepts (see SttClient._validate_audio's allowed formats) -- no
    separate audio-extraction step needed, just pass the file through."""
    video_note = message.video_note
    if video_note is None:
        return
    await _transcribe_and_reply(
        message, bot, stt_client, settings,
        file_id=video_note.file_id, filename="video_note.mp4", file_size=video_note.file_size,
    )
