from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from selara.core.chat_settings import ChatSettings

_FILENAME_BY_TYPE = {"voice": "voice.ogg", "video_note": "video_note.mp4"}


@dataclass(frozen=True)
class TranscriptionJob:
    chat_id: int
    telegram_message_id: int
    file_id: str
    filename: str
    message_type: str  # "voice" | "video_note"
    duration_seconds: float


def extract_media_ref(*, message_type: str, raw_message_json: dict[str, Any]) -> tuple[str, float] | None:
    """Pull (file_id, duration_seconds) out of a message's raw serialized JSON.

    Telegram reports duration on the message itself -- reading it here means the
    STT queue can enforce the per-chat transcription budget BEFORE downloading or
    transcribing anything, not after paying for it.
    """
    media = raw_message_json.get(message_type)
    if not isinstance(media, dict):
        return None
    file_id = media.get("file_id")
    if not file_id:
        return None
    return str(file_id), float(media.get("duration") or 0)


def build_job_from_raw_message(
    *,
    chat_id: int,
    telegram_message_id: int,
    message_type: str,
    raw_message_json: dict[str, Any],
) -> TranscriptionJob | None:
    filename = _FILENAME_BY_TYPE.get(message_type)
    if filename is None:
        return None
    ref = extract_media_ref(message_type=message_type, raw_message_json=raw_message_json)
    if ref is None:
        return None
    file_id, duration_seconds = ref
    return TranscriptionJob(
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        file_id=file_id,
        filename=filename,
        message_type=message_type,
        duration_seconds=duration_seconds,
    )


def is_transcription_enabled(chat_settings: ChatSettings, *, message_type: str) -> bool:
    if message_type == "voice":
        return bool(chat_settings.daily_summary_include_voice)
    if message_type == "video_note":
        return bool(chat_settings.daily_summary_include_video_notes)
    return False


def is_within_transcription_budget(
    *,
    seconds_used_today: float,
    job_duration_seconds: float,
    max_seconds_per_day: int,
) -> bool:
    return (seconds_used_today + job_duration_seconds) <= max_seconds_per_day
