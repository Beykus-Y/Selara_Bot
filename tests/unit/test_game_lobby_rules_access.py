"""Regression test for a gap Ilya caught in Stage 1/2 review: "Как играть"
was only reachable from the catalog detail card, but per the agreed UX it
must be available at least until the game starts -- i.e. from the lobby too,
not just before a lobby exists."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class FakeQuery:
    def __init__(self, *, user_id: int, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="tester", first_name="T", last_name=None, is_bot=False)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="group", title="chat"),
            message_id=42,
        )
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


class FakeActivityRepo:
    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        return None


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


async def _spy_lobby(store: GameStore, *, owner_user_id: int = 1):
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=-100,
        chat_title="chat",
        owner_user_id=owner_user_id,
        owner_label="owner",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    return game


def test_lobby_keyboard_has_a_rules_button() -> None:
    markup = game_router._build_game_controls(
        game=game_router.GroupGame(
            game_id="g1", kind="spy", chat_id=-100, chat_title="chat", owner_user_id=1,
            players={1: "owner"}, status="lobby",
        ),
        bot_username="selara_test_bot",
    )
    assert markup is not None
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "game:lrules:g1" in callbacks


@pytest.mark.asyncio
async def test_lobby_rules_callback_shows_rules_and_back_returns_to_lobby(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)

    game = await _spy_lobby(store)

    query = FakeQuery(user_id=1, data=f"game:lrules:{game.game_id}")
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    await game_router.game_callback(
        query, bot=bot, chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(), economy_repo=SimpleNamespace(),
    )
    assert bot.edit_message_text.await_count == 1
    edit_kwargs = bot.edit_message_text.await_args.kwargs
    assert "как играть" in edit_kwargs["text"].lower()
    back_callbacks = [b.callback_data for row in edit_kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"game:lback:{game.game_id}" in back_callbacks

    query2 = FakeQuery(user_id=1, data=f"game:lback:{game.game_id}")
    await game_router.game_callback(
        query2, bot=bot, chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(), economy_repo=SimpleNamespace(),
    )
    safe_edit_mock.assert_awaited_once()
