"""?/?? gating: the cheap guards and the permission check run before the durable lease, and the turn commits inside it."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selara.presentation.handlers import llm_admin


def _message() -> MagicMock:
    message = MagicMock()
    message.chat = SimpleNamespace(id=-100123, type="supergroup", title="Test group")
    message.from_user = SimpleNamespace(id=111, username="admin", first_name="Admin", last_name=None, is_bot=False)
    message.text = "? какой сегодня день"
    message.reply = AsyncMock()
    return message


async def _call(message: MagicMock, *, db_session: MagicMock, llm_enabled: bool = True) -> None:
    await llm_admin._handle(
        message, MagicMock(), MagicMock(), SimpleNamespace(llm_enabled=llm_enabled), MagicMock(), db_session,
        with_context=False, session_factory=object(),
    )


def _recording_lease(events: list[str]):
    @asynccontextmanager
    async def lease(**_kwargs):
        events.append("lease")
        yield True
        events.append("released")

    return lease


@pytest.mark.asyncio
async def test_busy_lease_replies_and_does_not_run_the_turn(monkeypatch) -> None:
    @asynccontextmanager
    async def busy_lease(**_kwargs):
        yield False

    run_turn = AsyncMock()
    monkeypatch.setattr(llm_admin, "ai_turn_lease", busy_lease)
    message = _message()
    with patch.object(llm_admin, "has_permission", new=AsyncMock(return_value=(True, None, None))), patch.object(
        llm_admin, "_run_admin_turn", run_turn
    ):
        await _call(message, db_session=AsyncMock())

    message.reply.assert_awaited_once_with(llm_admin._ADMIN_TURN_BUSY_TEXT)
    run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_refused_admin_never_takes_the_lease(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(llm_admin, "ai_turn_lease", _recording_lease(events))
    message = _message()
    with patch.object(llm_admin, "has_permission", new=AsyncMock(return_value=(False, None, None))):
        await _call(message, db_session=AsyncMock())

    assert events == []
    message.reply.assert_awaited_once()
    assert "Недостаточно прав" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_disabled_assistant_takes_no_lease(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(llm_admin, "ai_turn_lease", _recording_lease(events))
    with patch.object(llm_admin, "has_permission", new=AsyncMock(return_value=(True, None, None))):
        await _call(_message(), db_session=AsyncMock(), llm_enabled=False)

    assert events == []


@pytest.mark.asyncio
async def test_transaction_ends_before_the_lease_and_the_turn_commits_before_release(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(llm_admin, "ai_turn_lease", _recording_lease(events))
    db_session = AsyncMock()
    db_session.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    run_turn = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("turn"))
    with patch.object(llm_admin, "has_permission", new=AsyncMock(return_value=(True, None, None))), patch.object(
        llm_admin, "_run_admin_turn", run_turn
    ):
        await _call(_message(), db_session=db_session)

    assert events == ["commit", "lease", "turn", "commit", "released"]
