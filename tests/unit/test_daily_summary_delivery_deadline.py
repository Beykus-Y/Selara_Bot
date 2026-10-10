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
@pytest.mark.parametrize('trigger,status,age_minutes,advance_seconds,expected', [
    # Scheduled digests are deliverable up to 90 minutes after the planned window_to.
    ('scheduled', 'generated', 89, 0, True),
    ('scheduled', 'generated', 89, 120, False),  # fenced query pushes it to 91 minutes
    ('scheduled', 'send_failed', 89, 120, False),
    ('scheduled', 'send_failed', 88, 0, True),
    ('manual', 'generated', 120, 60, True),  # manual runs have no scheduled deadline
])
async def test_delivery_checks_deadline_after_fencing_query(
    monkeypatch, trigger, status, age_minutes, advance_seconds, expected,
):
    due = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
    clock = [due + timedelta(minutes=age_minutes)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(module, 'datetime', Clock)
    run = SimpleNamespace(chat_id=1, claimed_at=due, trigger=trigger, status=status,
        summary_date=due.date(), window_from=due-timedelta(days=1), window_to=due,
        generated_text='Summary', topics_json={})

    async def fence(**kwargs):
        clock[0] += timedelta(seconds=advance_seconds)
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
    clock = [due + timedelta(minutes=90)]  # exactly at the bound: still deliverable

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


def _scheduled_run(due):
    return SimpleNamespace(chat_id=1, claimed_at=due, trigger='scheduled', status='generated',
        summary_date=due.date(), window_from=due-timedelta(days=1), window_to=due,
        generated_text='Summary', topics_json={})


@pytest.mark.asyncio
@pytest.mark.parametrize('minutes_late,delivered', [(89, True), (91, False)])
async def test_scheduled_delivery_window_is_ninety_minutes_from_planned_window_to(
    monkeypatch, caplog, minutes_late, delivered,
):
    import logging

    due = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
    clock = [due + timedelta(minutes=minutes_late)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(module, 'datetime', Clock)
    run = _scheduled_run(due)
    repo = SimpleNamespace(get_daily_summary_run_by_id=AsyncMock(return_value=run),
        claim_daily_summary_delivery=AsyncMock(return_value=due),
        is_daily_summary_delivery_claim_current=AsyncMock(return_value=True),
        mark_daily_summary_run_sent=AsyncMock(return_value=True),
        mark_daily_summary_run_send_failed=AsyncMock())
    monkeypatch.setattr(module, 'SqlAlchemyActivityRepository', lambda session: repo)
    bot = SimpleNamespace(send_message=AsyncMock())

    with caplog.at_level(logging.INFO, logger=module.__name__):
        result = await module._send_and_mark(bot=bot, session_factory=Session,
            chat_id=1, run_id=42, claimed_at=due)

    assert result is delivered
    assert bot.send_message.await_count == int(delivered)
    repo.mark_daily_summary_run_send_failed.assert_not_awaited()
    if delivered:
        assert not [r for r in caplog.records if r.getMessage() == 'scheduled_skipped_stale_delivery']
    else:
        skipped = [r for r in caplog.records if r.getMessage() == 'scheduled_skipped_stale_delivery']
        assert len(skipped) == 1
        assert skipped[0].reason == 'outside_delivery_recovery_window'
        assert skipped[0].scheduled_at == due.isoformat()
