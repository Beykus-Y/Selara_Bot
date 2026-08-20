"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3,
correction #5: classify Mafia's feed-event messages as edit-existing-board /
keep-as-new-message / don't-send-at-all, per Ilya's rule -- not the
"collapsible board history" idea (explicitly deferred), just applying the
three existing primitives correctly.

Classification (see docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3 for the
full table): "day vote opened" announcement was pure current-state noise --
the board edit already shows voting is open with a countdown, and each
alive player already gets a private DM prompt to vote
(_notify_mafia_day_vote_private). The separate group-wide feed message
added no information the board+DMs didn't already carry, so it's dropped
(not sent) rather than kept or folded into an edit. Every other mafia feed
event (night outcome, execution-confirm opened, game-over results) records
a genuine discrete outcome and is intentionally left as a kept message --
not touched by this change.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _chat_settings():
    from selara.core.chat_settings import default_chat_settings
    from selara.core.config import Settings

    settings = Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
        }
    )
    return default_chat_settings(settings)


@pytest.mark.asyncio
async def test_day_vote_opened_no_longer_sends_a_redundant_feed_event(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    monkeypatch.setattr(game_router, "_notify_mafia_day_vote_private", AsyncMock())
    send_feed_event_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_send_game_feed_event", send_feed_event_mock)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)
    monkeypatch.setattr(game_router, "_schedule_phase_timer", lambda *a, **kw: None)

    game, error = await store.create_lobby(
        kind="mafia", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    for uid in (2, 3, 4):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    game_after_night, _, night_error = await store.mafia_resolve_night(game_id=started.game_id)
    assert night_error is None
    assert game_after_night.phase == "day_discussion"

    await game_router._open_mafia_day_vote(
        bot=SimpleNamespace(), game_id=game_after_night.game_id,
        chat_settings=_chat_settings(), triggered_by_timer=False,
    )

    # The board still gets edited (current state) and players still get
    # their private vote prompt -- only the redundant group-wide
    # announcement message is gone.
    safe_edit_mock.assert_awaited_once()
    send_feed_event_mock.assert_not_awaited()
