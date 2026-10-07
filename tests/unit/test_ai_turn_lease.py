"""Unit tests for the durable AI turn lease without a database: busy, lost-lease and release-failure paths."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from selara.infrastructure.db import ai_turn_leases
from selara.infrastructure.db.ai_turn_leases import ai_turn_lease
from selara.presentation.handlers import personal_ai

_KEY = "personal_ai:1"


@pytest.mark.asyncio
async def test_busy_key_yields_false_and_neither_renews_nor_releases(monkeypatch) -> None:
    release = AsyncMock()
    monkeypatch.setattr(ai_turn_leases, "try_acquire_ai_turn_lease", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_turn_leases, "release_ai_turn_lease", release)

    async with ai_turn_lease(session_factory=object(), lease_key=_KEY, ttl_seconds=60) as acquired:
        assert acquired is False

    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_stops_once_the_lease_is_lost(monkeypatch, caplog) -> None:
    renew = AsyncMock(return_value=False)
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", renew)
    caplog.set_level(logging.WARNING, logger=ai_turn_leases.__name__)

    await asyncio.wait_for(
        ai_turn_leases._keep_renewed(session_factory=object(), lease_key=_KEY, owner_token="t", ttl_seconds=0.03),
        timeout=2,
    )

    renew.assert_awaited_once()
    assert "lease was lost" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_keeps_going_after_a_failed_renewal(monkeypatch) -> None:
    renew = AsyncMock(side_effect=[RuntimeError("db down"), True, False])
    monkeypatch.setattr(ai_turn_leases, "renew_ai_turn_lease", renew)

    await asyncio.wait_for(
        ai_turn_leases._keep_renewed(session_factory=object(), lease_key=_KEY, owner_token="t", ttl_seconds=0.03),
        timeout=2,
    )

    assert renew.await_count == 3


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
        assert acquired is True

    assert "Failed to release AI turn lease" in caplog.text


@pytest.mark.asyncio
async def test_personal_turn_answers_busy_when_the_durable_lease_is_held(monkeypatch) -> None:
    @asynccontextmanager
    async def busy_lease(**_kwargs):
        yield False

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
