"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3: Mafia's
night-phase DM failures used to be completely silent (bare `continue` on
TelegramForbiddenError, no board warning) -- every other secret-role game
already showed "ЛС недоступно: N" somewhere. This pins that a night-phase DM
failure now triggers the same standalone warning message used for the
initial role-push failure."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError

from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class FakeBot:
    def __init__(self, *, blocked_user_ids: set[int]) -> None:
        # Deliberately only blocks the *night-action* DM, not the role-card
        # DM, for the same user -- isolates the night-phase fix from the
        # already-existing (and already-tested) role-push warning, which
        # would otherwise make this test pass for the wrong reason.
        self.blocked_user_ids = blocked_user_ids
        self.group_messages: list[str] = []

    async def get_me(self):
        return SimpleNamespace(username="selara_test_bot")

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.blocked_user_ids and "<b>Ночь" in text:
            raise TelegramForbiddenError(method=SimpleNamespace(), message="bot was blocked")
        if chat_id < 0:  # group chat
            self.group_messages.append(text)
        return SimpleNamespace(message_id=1)

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        return None


class FakeQuery:
    def __init__(self, *, user_id: int, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="u", first_name="U", last_name=None, is_bot=False)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=-100, type="group", title="chat"), message_id=1)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


class FakeActivityRepo:
    async def get_chat_display_name(self, *, chat_id, user_id):
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


@pytest.mark.asyncio
async def test_night_dm_failure_triggers_a_group_warning(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    # Role assignment (and therefore who actually has a night action) is
    # random -- force every alive player to get a night-action keyboard so
    # the blocked user is deterministically attempted, regardless of role.
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    monkeypatch.setattr(
        game_router,
        "_build_private_night_action_keyboard",
        lambda game, *, actor_user_id: InlineKeyboardBuilder().as_markup(),
    )

    game, error = await store.create_lobby(
        kind="mafia", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    for uid in (2, 3, 4):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")

    # user_id 2 has blocked the bot -- can't receive DMs at all.
    bot = FakeBot(blocked_user_ids={2})
    query = FakeQuery(user_id=1, data=f"game:start:{game.game_id}")
    await game_router.game_callback(
        query, bot=bot, chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(), economy_repo=SimpleNamespace(),
    )

    assert any("ЛС недоступно" in text for text in bot.group_messages), bot.group_messages


@pytest.mark.asyncio
async def test_no_warning_when_all_night_dms_succeed(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)

    game, error = await store.create_lobby(
        kind="mafia", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    for uid in (2, 3, 4):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")

    bot = FakeBot(blocked_user_ids=set())
    query = FakeQuery(user_id=1, data=f"game:start:{game.game_id}")
    await game_router.game_callback(
        query, bot=bot, chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(), economy_repo=SimpleNamespace(),
    )

    assert not any("ЛС недоступно" in text for text in bot.group_messages)
