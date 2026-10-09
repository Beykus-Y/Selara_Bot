"""GUX-09: an early last submission must not strand Zlobcards without vote timer."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

router = importlib.import_module("selara.presentation.handlers.game.router")


@pytest.mark.asyncio
@pytest.mark.parametrize("opened", [True, False])
async def test_last_private_submission_hands_timer_to_public_vote(monkeypatch, opened: bool) -> None:
    game = SimpleNamespace(
        game_id="zlob-early", chat_id=-100, status="started",
        phase="public_vote" if opened else "private_answers",
        round_no=2, zlob_submissions={2: ("Card",)},
    )
    store = SimpleNamespace(
        get_game=AsyncMock(return_value=game),
        zlob_submit_cards=AsyncMock(return_value=(
            game,
            SimpleNamespace(
                vote_opened=opened, submitted_count=2, total_players=3,
                previous_submission=None,
            ),
            None,
        )),
    )
    query = SimpleNamespace(
        data="gzlobp:zlob-early:2:0",
        from_user=SimpleNamespace(
            id=2, username="u", first_name="U", last_name=None,
        ),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup")),
        answer=AsyncMock(),
    )
    monkeypatch.setattr(router, "GAME_STORE", store)
    monkeypatch.setattr(router, "_refresh_game_player_label", AsyncMock())
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", AsyncMock())
    monkeypatch.setattr(router, "_safe_callback_answer", AsyncMock())
    schedule = Mock()
    monkeypatch.setattr(router, "_schedule_phase_timer", schedule)

    bot, chat_settings = Mock(), Mock()
    await router.zlob_private_submit_callback(query, bot, chat_settings, Mock())

    if opened:
        schedule.assert_called_once_with(bot, game, chat_settings)
    else:
        schedule.assert_not_called()
    assert store.zlob_submit_cards.await_args.kwargs["expected_round_no"] == 2
    assert store.zlob_submit_cards.await_args.kwargs["expected_chat_id"] == -100
