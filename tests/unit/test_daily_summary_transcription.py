from __future__ import annotations

from dataclasses import replace

from selara.application.daily_summary.transcription import (
    TranscriptionJob,
    build_job_from_raw_message,
    extract_media_ref,
    is_transcription_enabled,
    is_within_transcription_budget,
)
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings


def _settings() -> Settings:
    return Settings.model_validate(
        {"BOT_TOKEN": "123456:TEST", "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test"}
    )


def test_extract_media_ref_reads_voice_file_id_and_duration() -> None:
    raw = {"message_id": 1, "voice": {"file_id": "AAA", "duration": 12, "mime_type": "audio/ogg"}}
    result = extract_media_ref(message_type="voice", raw_message_json=raw)
    assert result == ("AAA", 12.0)


def test_extract_media_ref_reads_video_note_file_id_and_duration() -> None:
    raw = {"message_id": 2, "video_note": {"file_id": "BBB", "duration": 30}}
    result = extract_media_ref(message_type="video_note", raw_message_json=raw)
    assert result == ("BBB", 30.0)


def test_extract_media_ref_returns_none_for_unrelated_message_type() -> None:
    raw = {"message_id": 3, "text": "hello"}
    assert extract_media_ref(message_type="text", raw_message_json=raw) is None


def test_extract_media_ref_returns_none_when_media_block_missing() -> None:
    raw = {"message_id": 4}
    assert extract_media_ref(message_type="voice", raw_message_json=raw) is None


def test_extract_media_ref_returns_none_without_file_id() -> None:
    raw = {"message_id": 5, "voice": {"duration": 5}}
    assert extract_media_ref(message_type="voice", raw_message_json=raw) is None


def test_build_job_from_raw_message_voice() -> None:
    raw = {"voice": {"file_id": "AAA", "duration": 7}}
    job = build_job_from_raw_message(chat_id=-100, telegram_message_id=42, message_type="voice", raw_message_json=raw)
    assert job == TranscriptionJob(
        chat_id=-100, telegram_message_id=42, file_id="AAA", filename="voice.ogg",
        message_type="voice", duration_seconds=7.0,
    )


def test_build_job_from_raw_message_returns_none_when_unparseable() -> None:
    job = build_job_from_raw_message(chat_id=-100, telegram_message_id=42, message_type="voice", raw_message_json={})
    assert job is None


def test_is_transcription_enabled_checks_matching_setting_only() -> None:
    settings = replace(default_chat_settings(_settings()), daily_summary_include_voice=True, daily_summary_include_video_notes=False)
    assert is_transcription_enabled(settings, message_type="voice") is True
    assert is_transcription_enabled(settings, message_type="video_note") is False


def test_is_transcription_enabled_false_for_unknown_type() -> None:
    settings = replace(default_chat_settings(_settings()), daily_summary_include_voice=True)
    assert is_transcription_enabled(settings, message_type="photo") is False


def test_budget_allows_job_within_remaining_budget() -> None:
    assert is_within_transcription_budget(seconds_used_today=100, job_duration_seconds=50, max_seconds_per_day=200) is True


def test_budget_rejects_job_that_would_exceed_cap() -> None:
    assert is_within_transcription_budget(seconds_used_today=180, job_duration_seconds=50, max_seconds_per_day=200) is False


def test_budget_allows_exact_boundary() -> None:
    assert is_within_transcription_budget(seconds_used_today=150, job_duration_seconds=50, max_seconds_per_day=200) is True
