"""Regression for restart-time game phase scheduling (#194).

Exercise the public restore entry point without starting background sleeps or
requiring a real Telegram bot/database. Production device acceptance is separate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib

import pytest

from selara.infrastructure.db import repositories

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _game(kind: str, phase: str, elapsed_seconds: float, *, chat_id: int = -100):
    return SimpleNamespace(
        kind=kind, phase=phase, status="started", game_id=f"{kind}-{phase}",
        chat_id=chat_id, phase_started_at=datetime.now(timezone.utc) - timedelta(seconds=elapsed_seconds),
        round_no=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,phase,elapsed,seconds",
    [
        ("mafia", "night", 30, 120),
        ("mafia", "day_discussion", 25, 90),
        ("mafia", "day_vote", 20, 70),
        ("mafia", "day_execution_confirm", 20, 70),
        ("zlobcards", "private_answers", 20, None),
        ("zlobcards", "public_vote", 20, None),
    ],
)
async def test_restart_restores_only_remaining_phase_seconds(
    monkeypatch, kind, phase, elapsed, seconds,
):
    game = _game(kind, phase, elapsed)
    settings = SimpleNamespace(mafia_night_seconds=120, mafia_day_seconds=90, mafia_vote_seconds=70)
    repo = SimpleNamespace(get_chat_settings=AsyncMock(return_value=settings))
    monkeypatch.setattr(repositories, "SqlAlchemyActivityRepository", lambda session: repo)
    monkeypatch.setattr(game_router.GAME_STORE, "list_active_games", AsyncMock(return_value=[game]))
    scheduled = []
    monkeypatch.setattr(game_router, "_cancel_phase_timer", lambda game_id: None)
    monkeypatch.setattr(
        game_router, "_schedule_phase_timer_with_remaining",
        lambda bot, current, cfg, remaining: scheduled.append((current, cfg, remaining)),
    )

    bot = SimpleNamespace()
    await game_router.restore_phase_timers(bot, lambda: _Session())

    assert len(scheduled) == 1
    current, cfg, remaining = scheduled[0]
    assert current is game and cfg is settings
    if kind == "zlobcards":
        duration = (game_router._ZLOBCARDS_PRIVATE_SECONDS
                    if phase == "private_answers" else game_router._ZLOBCARDS_VOTE_SECONDS)
    else:
        duration = seconds
    assert abs(remaining - max(5.0, max(5, duration) - elapsed)) < 2
    repo.get_chat_settings.assert_awaited_once_with(chat_id=-100)


@pytest.mark.asyncio
async def test_expired_zlobcards_private_phase_never_gets_a_fresh_full_window(monkeypatch):
    full = max(5, game_router._ZLOBCARDS_PRIVATE_SECONDS)
    grace = game_router._ZLOBCARDS_RECOVERY_GRACE_SECONDS
    game = _game("zlobcards", "private_answers", full + grace + 40)
    settings = SimpleNamespace()
    repo = SimpleNamespace(get_chat_settings=AsyncMock(return_value=settings))
    monkeypatch.setattr(repositories, "SqlAlchemyActivityRepository", lambda session: repo)
    monkeypatch.setattr(game_router.GAME_STORE, "list_active_games", AsyncMock(return_value=[game]))
    scheduled = []
    monkeypatch.setattr(game_router, "_cancel_phase_timer", lambda game_id: None)
    monkeypatch.setattr(
        game_router, "_schedule_phase_timer_with_remaining",
        lambda bot, current, cfg, remaining: scheduled.append(remaining),
    )

    await game_router.restore_phase_timers(SimpleNamespace(), lambda: _Session())

    # Already beyond both the main timer and the bounded recovery window:
    # schedule a single quick check, never another complete private phase.
    assert len(scheduled) == 1
    assert scheduled[0] == 5.0


@pytest.mark.asyncio
async def test_restart_ignores_finished_games_and_kinds_without_phase_timers(monkeypatch):
    finished = _game("mafia", "night", 20)
    finished.status = "finished"
    dice = _game("dice", "freeplay", 20)
    monkeypatch.setattr(
        game_router.GAME_STORE, "list_active_games",
        AsyncMock(return_value=[finished, dice]),
    )
    scheduled = []
    monkeypatch.setattr(
        game_router, "_schedule_phase_timer_with_remaining",
        lambda *args: scheduled.append(args),
    )

    await game_router.restore_phase_timers(SimpleNamespace(), lambda: _Session())

    assert not scheduled
