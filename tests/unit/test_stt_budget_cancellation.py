import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.daily_summary.transcription import TranscriptionJob
from selara.infrastructure.stt import daily_summary_queue as module


@pytest.mark.asyncio
async def test_cancellation_still_stops_worker_when_reservation_cleanup_fails(monkeypatch):
    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(commit=AsyncMock())

    activity = SimpleNamespace(get_chat_settings=AsyncMock(return_value=SimpleNamespace(daily_summary_include_voice=True)))
    budget = SimpleNamespace(reserve=AsyncMock(return_value="owned-token"),
                             release=AsyncMock(side_effect=RuntimeError("database unavailable")))
    monkeypatch.setattr(module, "SqlAlchemyActivityRepository", lambda session: activity)
    monkeypatch.setattr(module, "SttBudgetRepository", lambda session: budget)
    queue = module.DailySummaryTranscriptionQueue(
        bot=SimpleNamespace(get_file=AsyncMock(side_effect=asyncio.CancelledError())),
        stt_client=SimpleNamespace(), session_factory=sessions,
        settings=SimpleNamespace(daily_summary_max_transcription_seconds_per_chat_per_day=100),
    )
    monkeypatch.setattr(queue, "_claim_with_retry", AsyncMock(return_value=1))
    with pytest.raises(asyncio.CancelledError):
        await queue._process_job(TranscriptionJob(-100, 1, "file", "voice.ogg", "voice", 10))
    budget.release.assert_awaited_once_with(token="owned-token")
