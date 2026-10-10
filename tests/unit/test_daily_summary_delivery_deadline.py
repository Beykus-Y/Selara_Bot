from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation import daily_summary as module


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def commit(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize('trigger,status,age,advance,expected', [
    ('scheduled', 'generated', 6, 0, True),
    ('scheduled', 'generated', 6, 1, False),
    ('scheduled', 'send_failed', 6, 1, False),
    ('scheduled', 'send_failed', 5, 0, True),
    ('manual', 'generated', 7, 1, True),
])
async def test_delivery_checks_deadline_after_fencing_query(monkeypatch, trigger, status, age, advance, expected):
    due = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
    clock = [due + timedelta(hours=age)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(module, 'datetime', Clock)
    run = SimpleNamespace(chat_id=1, claimed_at=due, trigger=trigger, status=status,
        summary_date=due.date(), window_from=due-timedelta(days=1), window_to=due,
        generated_text='Summary', topics_json={})

    async def fence(**kwargs):
        clock[0] += timedelta(seconds=advance)
        return True

    repo = SimpleNamespace(get_daily_summary_run_by_id=AsyncMock(return_value=run),
        claim_daily_summary_delivery=AsyncMock(return_value=due),
        is_daily_summary_delivery_claim_current=AsyncMock(side_effect=fence),
        mark_daily_summary_run_sent=AsyncMock(return_value=True),
        mark_daily_summary_run_send_failed=AsyncMock())
    monkeypatch.setattr(module, 'SqlAlchemyActivityRepository', lambda session: repo)
    bot = SimpleNamespace(send_message=AsyncMock())
    result = await module._send_and_mark(bot=bot, session_factory=Session,
        chat_id=1, run_id=42, claimed_at=due)
    assert result is expected
    assert bot.send_message.await_count == int(expected)
    assert repo.mark_daily_summary_run_sent.await_count == int(expected)
    repo.mark_daily_summary_run_send_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_stops_second_text_chunk_when_deadline_passes(monkeypatch):
    due = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
    clock = [due + timedelta(hours=6)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(module, 'datetime', Clock)
    run = SimpleNamespace(chat_id=1, claimed_at=due, trigger='scheduled',
        summary_date=due.date(), window_from=due-timedelta(days=1), window_to=due,
        generated_text='Summary', topics_json={})
    repo = SimpleNamespace(get_daily_summary_run_by_id=AsyncMock(return_value=run),
        claim_daily_summary_delivery=AsyncMock(return_value=due),
        is_daily_summary_delivery_claim_current=AsyncMock(return_value=True),
        mark_daily_summary_run_sent=AsyncMock(), mark_daily_summary_run_send_failed=AsyncMock())
    monkeypatch.setattr(module, 'SqlAlchemyActivityRepository', lambda session: repo)
    monkeypatch.setattr(module, 'split_telegram_html', lambda text: ['first', 'second'])

    async def send(**kwargs):
        clock[0] += timedelta(seconds=1)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=send))
    assert await module._send_and_mark(bot=bot, session_factory=Session,
        chat_id=1, run_id=42, claimed_at=due) is False
    bot.send_message.assert_awaited_once()
    repo.mark_daily_summary_run_sent.assert_not_awaited()
    repo.mark_daily_summary_run_send_failed.assert_not_awaited()
