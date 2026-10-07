"""Unit tests for the durable AI turn lease without a database: busy, lost-lease and release-failure paths."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from selara.infrastructure.db import ai_turn_leases
from selara.infrastructure.db.ai_turn_leases import AiTurnLease, AiTurnLeaseLostError, ai_turn_lease
from selara.presentation.handlers import personal_ai

_KEY = "personal_ai:1"


def _lease(*, ttl_seconds: float = 60.0) -> AiTurnLease:
    return AiTurnLease(
        session_factory=object(),
        lease_key=_KEY,
        owner_token="t",
        ttl_seconds=ttl_seconds,
        acquired_at=asyncio.get_running_loop().time(),
    )


@pytest.mark.asyncio
async def test_busy_key_yields_none_and_neither_renews_nor_releases(monkeypatch) -> None:
    release = AsyncMock()
    monkeypatch.setattr(ai_turn_leases, "try_acquire_ai_turn_lease", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_turn_leases, "release_ai_turn_lease", release)

    async with ai_turn_lease(session_factory=object(), lease_key=_KEY, ttl_seconds=60) as acquired:
        assert acquired is None

    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_marks_the_lease_lost_once_the_renewal_is_refused(monkeypatch, caplog) -> None:
    renew = AsyncMock(return_value=False)
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", renew)
    caplog.set_level(logging.WARNING, logger=ai_turn_leases.__name__)
    lease = _lease(ttl_seconds=0.03)

    await asyncio.wait_for(lease._heartbeat(), timeout=2)

    renew.assert_awaited_once()
    assert lease.lost
    assert "AI turn lease lost" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_keeps_going_after_a_failed_renewal_within_the_ttl(monkeypatch) -> None:
    renew = AsyncMock(side_effect=[RuntimeError("db down"), True, False])
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", renew)
    lease = _lease(ttl_seconds=1.0)

    await asyncio.wait_for(lease._heartbeat(), timeout=5)

    assert renew.await_count == 3
    assert lease.lost


@pytest.mark.asyncio
async def test_renewals_failing_for_a_whole_ttl_mark_the_lease_lost(monkeypatch) -> None:
    renew = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", renew)
    lease = _lease(ttl_seconds=0.2)

    await asyncio.wait_for(lease._heartbeat(), timeout=5)

    assert lease.lost
    assert renew.await_count >= 2


@pytest.mark.asyncio
async def test_a_renewal_hanging_past_the_ttl_marks_the_lease_lost(monkeypatch) -> None:
    async def hang(**_kwargs) -> bool:
        await asyncio.sleep(60)
        return True

    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", hang)
    lease = _lease(ttl_seconds=0.2)

    await asyncio.wait_for(lease._heartbeat(), timeout=5)

    assert lease.lost


@pytest.mark.asyncio
async def test_a_lost_lease_stops_the_turn_before_its_next_step() -> None:
    lease = _lease()
    steps: list[int] = []

    async def turn() -> None:
        for step in range(100):
            await asyncio.sleep(0.01)
            steps.append(step)

    async def lose_the_key() -> None:
        await asyncio.sleep(0.05)
        lease._mark_lost("taken over")

    with pytest.raises(AiTurnLeaseLostError):
        await asyncio.gather(lease.run(turn()), lose_the_key())

    stopped_at = len(steps)
    await asyncio.sleep(0.05)
    assert 0 < stopped_at < 100
    assert len(steps) == stopped_at


@pytest.mark.asyncio
async def test_run_returns_the_turn_result_while_the_lease_is_held() -> None:
    assert await _lease().run(asyncio.sleep(0, result=42)) == 42


@pytest.mark.asyncio
async def test_cancelling_the_caller_is_not_reported_as_a_lost_lease() -> None:
    lease = _lease()

    async def turn() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(lease.run(turn()))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lease.lost


@pytest.mark.asyncio
async def test_confirm_raises_and_marks_the_lease_lost_when_the_renewal_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", AsyncMock(return_value=False))
    lease = _lease()

    with pytest.raises(AiTurnLeaseLostError):
        await lease.confirm()

    assert lease.lost


@pytest.mark.asyncio
async def test_confirm_passes_while_the_lease_is_still_held(monkeypatch) -> None:
    renew = AsyncMock(return_value=True)
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", renew)
    lease = _lease()

    await lease.confirm()

    renew.assert_awaited_once()
    assert not lease.lost


@pytest.mark.asyncio
async def test_lease_is_released_when_the_turn_raises(monkeypatch) -> None:
    factory = object()
    release = AsyncMock()
    monkeypatch.setattr(ai_turn_leases, "try_acquire_ai_turn_lease", AsyncMock(return_value="tok"))
    monkeypatch.setattr(ai_turn_leases, "release_ai_turn_lease", release)

    with pytest.raises(RuntimeError, match="provider failed"):
        async with ai_turn_lease(session_factory=factory, lease_key=_KEY, ttl_seconds=60):
            raise RuntimeError("provider failed")

    release.assert_awaited_once_with(session_factory=factory, lease_key=_KEY, owner_token="tok")


@pytest.mark.asyncio
async def test_failed_release_is_logged_and_does_not_fail_the_turn(monkeypatch, caplog) -> None:
    monkeypatch.setattr(ai_turn_leases, "try_acquire_ai_turn_lease", AsyncMock(return_value="tok"))
    monkeypatch.setattr(ai_turn_leases, "release_ai_turn_lease", AsyncMock(side_effect=RuntimeError("db down")))

    async with ai_turn_lease(session_factory=object(), lease_key=_KEY, ttl_seconds=60) as acquired:
        assert isinstance(acquired, AiTurnLease)

    assert "Failed to release AI turn lease" in caplog.text


@pytest.mark.asyncio
async def test_personal_turn_answers_busy_when_the_durable_lease_is_held(monkeypatch) -> None:
    @asynccontextmanager
    async def busy_lease(**_kwargs):
        yield None

    turn = AsyncMock()
    monkeypatch.setattr(personal_ai, "ai_turn_lease", busy_lease)
    monkeypatch.setattr(personal_ai, "_handle_personal_chat", turn)
    message = MagicMock()
    message.from_user = MagicMock(id=42)
    message.text = "привет"
    message.answer = AsyncMock()

    await personal_ai.personal_chat_handler(message, AsyncMock(), object(), object(), AsyncMock())

    message.answer.assert_awaited_once_with(personal_ai._BUSY_TEXT)
    turn.assert_not_awaited()
    assert 42 not in personal_ai._inflight_users


@pytest.mark.asyncio
async def test_personal_turn_that_loses_its_lease_says_so_and_rolls_back(monkeypatch) -> None:
    class _LostLease:
        async def run(self, turn):
            turn.close()
            raise AiTurnLeaseLostError(_KEY)

    @asynccontextmanager
    async def lost_lease(**_kwargs):
        yield _LostLease()

    turn = AsyncMock()
    monkeypatch.setattr(personal_ai, "ai_turn_lease", lost_lease)
    monkeypatch.setattr(personal_ai, "_handle_personal_chat", turn)
    message = MagicMock()
    message.from_user = MagicMock(id=42)
    message.text = "привет"
    message.answer = AsyncMock()
    db_session = AsyncMock()

    await personal_ai.personal_chat_handler(message, db_session, object(), object(), AsyncMock())

    message.answer.assert_awaited_once_with(personal_ai._LEASE_LOST_TEXT)
    db_session.rollback.assert_awaited_once()
    assert 42 not in personal_ai._inflight_users
