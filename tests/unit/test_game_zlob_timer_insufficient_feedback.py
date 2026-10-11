"""Zlobcards 75-second private timer: insufficient submissions must not be silent."""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

router = importlib.import_module("selara.presentation.handlers.game.router")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto", "error", "should_render"),
    [
        (True, "Нужно минимум два ответа для голосования", True),
        (False, "Нужно минимум два ответа для голосования", False),
        (True, "Сейчас не этап приватного выбора", False),
    ],
)
async def test_zlob_expired_private_timer_explains_missing_answers_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    auto: bool,
    error: str,
    should_render: bool,
) -> None:
    game = SimpleNamespace(
        kind="zlobcards", game_id="zlob-123", status="started",
        phase="private_answers", round_no=1,
    )
    store = SimpleNamespace(zlob_open_vote=AsyncMock(return_value=(game, error)))
    render = AsyncMock()
    schedule = Mock()
    monkeypatch.setattr(router, "GAME_STORE", store)
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", render)
    monkeypatch.setattr(router, "_schedule_phase_timer", schedule)

    bot, settings = Mock(), Mock()
    returned, actual_error = await router._open_zlob_vote_phase(
        bot, game.game_id, settings, force=True, triggered_by_auto=auto,
    )

    assert returned is game and actual_error == error
    store.zlob_open_vote.assert_awaited_once_with(
        game_id=game.game_id, force=True,
        expected_round_no=None, expected_phase_started_at=None,
    )
    schedule.assert_not_called()
    assert game.phase == "private_answers"
    if should_render:
        render.assert_awaited_once()
        kwargs = render.await_args.kwargs
        assert kwargs["note"].count("минимум два ответа") == 1
        assert "Рука в ЛС" in kwargs["note"]
        assert "Открыть голосование" in kwargs["note"]
        assert "Ведущему" in kwargs["note"]
    else:
        render.assert_not_awaited()


@pytest.mark.asyncio
async def test_zlob_auto_timer_with_enough_answers_still_hands_off_to_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = SimpleNamespace(
        kind="zlobcards", game_id="zlob-123", status="started",
        phase="public_vote", round_no=1,
    )
    monkeypatch.setattr(
        router, "GAME_STORE",
        SimpleNamespace(zlob_open_vote=AsyncMock(return_value=(game, None))),
    )
    render, schedule = AsyncMock(), Mock()
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", render)
    monkeypatch.setattr(router, "_schedule_phase_timer", schedule)

    bot, settings = Mock(), Mock()
    returned, error = await router._open_zlob_vote_phase(
        bot, game.game_id, settings, force=True, triggered_by_auto=True,
    )
    assert returned is game and error is None
    render.assert_awaited_once()
    schedule.assert_called_once_with(bot, game, settings)


def test_private_answer_grace_is_bounded_by_original_phase_start_not_restart() -> None:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    game = SimpleNamespace(phase_started_at=now - timedelta(seconds=100))
    assert router._zlob_private_grace_remaining(game, now=now) == 65
    assert router._zlob_private_grace_remaining(
        game, now=now + timedelta(seconds=65),
    ) == 0
    assert router._zlob_private_grace_remaining(
        game, now=now + timedelta(hours=2),
    ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("elapsed", "should_rearm"), [(75, True), (110, True), (165, False), (200, False)])
async def test_private_timer_shortfall_rearms_only_inside_fixed_grace(
    monkeypatch: pytest.MonkeyPatch, elapsed: int, should_rearm: bool,
) -> None:
    game = SimpleNamespace(
        kind="zlobcards", game_id="zlob-123", status="started",
        phase="private_answers", round_no=1,
        phase_started_at=datetime.now(timezone.utc) - timedelta(seconds=elapsed),
    )
    monkeypatch.setattr(
        router, "GAME_STORE",
        SimpleNamespace(zlob_open_vote=AsyncMock(
            return_value=(game, "Нужно минимум два ответа для голосования"),
        )),
    )
    render = AsyncMock()
    schedule = Mock()
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", render)
    monkeypatch.setattr(router, "_schedule_phase_timer_with_remaining", schedule)

    bot, settings = Mock(), Mock()
    returned, error = await router._open_zlob_vote_phase(
        bot, game.game_id, settings, force=True, triggered_by_auto=True,
    )

    assert returned is game and error == "Нужно минимум два ответа для голосования"
    render.assert_awaited_once()
    if should_rearm:
        schedule.assert_called_once()
        assert schedule.call_args.args[:3] == (bot, game, settings)
        assert 5 <= schedule.call_args.args[3] <= 90
        assert "дополнительное окно" in render.await_args.kwargs["note"]
    else:
        schedule.assert_not_called()
        assert "время истекло" in render.await_args.kwargs["note"]


@pytest.mark.asyncio
async def test_restore_private_timer_uses_remaining_fixed_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import selara.infrastructure.db.repositories as repositories  # noqa: PLC0415

    game = SimpleNamespace(
        kind="zlobcards", game_id="restored-game", chat_id=-100,
        status="started", phase="private_answers", round_no=1,
        phase_started_at=datetime.now(timezone.utc) - timedelta(seconds=110),
    )
    monkeypatch.setattr(
        router, "GAME_STORE",
        SimpleNamespace(list_active_games=AsyncMock(return_value=[game])),
    )

    class Repo:
        def __init__(self, session) -> None:
            pass
        async def get_chat_settings(self, *, chat_id: int):
            assert chat_id == -100
            return SimpleNamespace()

    class Factory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return SimpleNamespace()
        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(repositories, "SqlAlchemyActivityRepository", Repo)
    schedule = Mock()
    monkeypatch.setattr(router, "_schedule_phase_timer_with_remaining", schedule)
    monkeypatch.setattr(router, "_cancel_phase_timer", Mock())

    await router.restore_phase_timers(Mock(), Factory())

    schedule.assert_called_once()
    remaining = schedule.call_args.args[3]
    assert 5 <= remaining <= 60  # 165 seconds total, 110 already elapsed
