"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3: unified
failed-DM recovery. The old warning was a bare count with no way to act on
it ("Не удалось отправить ЛС для N игрок(ов)..."); the new one @mentions the
specific players affected and includes a deep-link button that opens the
right game context, reusing the existing ?start=game_{id} mechanism rather
than inventing a new one."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _game() -> GroupGame:
    return GroupGame(
        game_id="g1",
        kind="spy",
        chat_id=-100,
        chat_title="chat",
        owner_user_id=1,
        players={1: "owner", 2: "@blocked_user", 3: "Third Player"},
    )


def test_warning_text_mentions_each_failed_player_by_id() -> None:
    text = game_router._build_private_delivery_warning_text(_game(), [2, 3])
    assert 'tg://user?id=2' in text
    assert 'tg://user?id=3' in text
    assert "@blocked_user" in text
    assert "Third Player" in text


def test_warning_text_does_not_use_the_old_bare_count_phrasing() -> None:
    text = game_router._build_private_delivery_warning_text(_game(), [2])
    assert "игрок(ов)" not in text


@pytest.mark.asyncio
async def test_notify_sends_a_deep_link_button(monkeypatch) -> None:
    game = _game()
    bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="selara_test_bot")),
        send_message=AsyncMock(),
    )
    monkeypatch.setattr(game_router, "_BOT_USERNAME_CACHE", None)

    await game_router._notify_private_delivery_warning(bot, game, [2])

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == -100
    keyboard = kwargs["reply_markup"]
    urls = [b.url for row in keyboard.inline_keyboard for b in row if b.url]
    assert f"https://t.me/selara_test_bot?start=game_{game.game_id}" in urls


@pytest.mark.asyncio
async def test_notify_is_a_noop_for_an_empty_failure_list() -> None:
    game = _game()
    bot = SimpleNamespace(send_message=AsyncMock())
    await game_router._notify_private_delivery_warning(bot, game, [])
    bot.send_message.assert_not_awaited()
