from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message

from selara.infrastructure.stt import SttClient, SttClientError

router = Router(name="voice")
log = logging.getLogger(__name__)

_PENDING_TEXT = "🎙 Распознаю..."


@router.message(F.voice)
async def voice_message_handler(message: Message, bot: Bot, stt_client: SttClient) -> None:
    voice = message.voice
    if voice is None:
        return

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

    await status.edit_text(f"```\n{text}\n```", parse_mode="Markdown")
