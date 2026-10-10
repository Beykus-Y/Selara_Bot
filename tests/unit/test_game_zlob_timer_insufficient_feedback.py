"""Zlobcards 75-second private timer: insufficient submissions must not be silent."""
from __future__ import annotations

import importlib
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
    store.zlob_open_vote.assert_awaited_once_with(game_id=game.game_id, force=True)
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
