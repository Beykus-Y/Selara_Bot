from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.types import Message

from selara.core.config import Settings
from selara.infrastructure.stt import SttClient, SttClientError

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


@router.message(F.voice)
async def voice_message_handler(message: Message, bot: Bot, stt_client: SttClient, settings: Settings) -> None:
    voice = message.voice
    if voice is None:
        return

    if message.from_user is not None:
        key = (message.chat.id, message.from_user.id)
        now = time.monotonic()
        last = _last_request_at.get(key)
        if last is not None and (now - last) < settings.stt_cooldown_seconds:
            return
        _last_request_at[key] = now

    status = await message.reply(_PENDING_TEXT)

    try:
        file = await bot.get_file(voice.file_id)
        audio_bytes = await bot.download_file(file.file_path)  # type: ignore[arg-type]
        raw = audio_bytes.read() if hasattr(audio_bytes, "read") else bytes(audio_bytes)
    except Exception as exc:
        log.warning("voice: не удалось скачать файл: %s", exc)
        await status.edit_text("❌ Не удалось загрузить голосовое сообщение.")
        return

    try:
        text = await stt_client.transcribe_with_retry(raw, filename="voice.ogg")
    except SttClientError as exc:
        log.warning("voice: STT ошибка: %s", exc.message)
        await status.edit_text(f"❌ Не удалось распознать: {exc.message}")
        return

    chunks = _split_transcription(text)
    await status.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await message.reply(chunk)
