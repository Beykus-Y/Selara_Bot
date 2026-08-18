from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara import main as main_module


@pytest.mark.asyncio
async def test_warmup_notifies_admin_once_when_cache_becomes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """User request (2026-08-19): notify the admin when the background
    warmup finishes, not just silently transition — previously there was no
    way to know it had completed without reading server logs directly."""
    readiness_calls = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(main_module, "is_gacha_animation_cache_ready", readiness_calls)
    monkeypatch.setattr(main_module, "warm_up_gacha_animation_cache", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock())
    settings = SimpleNamespace(admin_user_id=99)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    session_factory = lambda: _AsyncCtx(session)
    monkeypatch.setattr(main_module, "SqlAlchemyActivityRepository", lambda session: SimpleNamespace())

    await main_module._run_gacha_animation_warmup(settings, bot, session_factory)

    bot.send_message.assert_awaited_once()
    assert "заверш" in bot.send_message.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_warmup_does_not_notify_when_already_ready_before_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    """No spurious 'warmup complete' notification if the cache was already
    warm before this run even started (e.g. bot restart after a completed
    warmup) — only a real False->True transition is newsworthy."""
    monkeypatch.setattr(main_module, "is_gacha_animation_cache_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(main_module, "warm_up_gacha_animation_cache", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock())
    settings = SimpleNamespace(admin_user_id=99)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    session_factory = lambda: _AsyncCtx(session)
    monkeypatch.setattr(main_module, "SqlAlchemyActivityRepository", lambda session: SimpleNamespace())

    await main_module._run_gacha_animation_warmup(settings, bot, session_factory)

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_warmup_does_not_notify_when_still_not_ready_after_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single warmup pass may not finish the whole catalog (e.g. hitting
    per-card timeouts) — don't claim completion prematurely."""
    monkeypatch.setattr(main_module, "is_gacha_animation_cache_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(main_module, "warm_up_gacha_animation_cache", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock())
    settings = SimpleNamespace(admin_user_id=99)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    session_factory = lambda: _AsyncCtx(session)
    monkeypatch.setattr(main_module, "SqlAlchemyActivityRepository", lambda session: SimpleNamespace())

    await main_module._run_gacha_animation_warmup(settings, bot, session_factory)

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_warmup_completion_notify_failure_does_not_crash_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "is_gacha_animation_cache_ready", AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(main_module, "warm_up_gacha_animation_cache", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("telegram down")))
    settings = SimpleNamespace(admin_user_id=99)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    session_factory = lambda: _AsyncCtx(session)
    monkeypatch.setattr(main_module, "SqlAlchemyActivityRepository", lambda session: SimpleNamespace())

    await main_module._run_gacha_animation_warmup(settings, bot, session_factory)  # must not raise


class _AsyncCtx:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info) -> None:
        return None
