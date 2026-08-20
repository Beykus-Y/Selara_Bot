"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (6/7,
Bunker -- most information-dense board, so extra care with DM-recovery and
board framing). No mechanic change (still sequential-turn field reveal,
private elimination voting).

- Board text splits the blended "Сейчас: <state>" lines into separate
  "Сейчас" / "Что делать" for bunker_reveal and bunker_vote, and adds
  explicit "остальные пока не ходят" framing for idle players during
  another player's reveal turn -- the original audit's specific finding
  for this game (non-active players had no indication why nothing was
  happening for them).
- DM-recovery gap found and fixed: the very first reveal-turn's DM push
  (right after game start) and the "reveal advanced to the next player"
  DM push both used to silently discard the failure (game start) or bury
  it in a plain board-note line with no button/mention (mid-game advance)
  -- neither matched the unified button+@mention pattern from Stage 3.
  _notify_bunker_reveal_turn now returns the failed user_id list (was a
  bare bool) so both call sites can route through the same
  _notify_private_delivery_warning helper used everywhere else."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.game_state import GameStore, GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _chat_settings():
    settings = Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
        }
    )
    return default_chat_settings(settings)


def _bunker_game(**overrides) -> GroupGame:
    defaults = dict(
        game_id="g1", kind="bunker", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara"}, status="started",
        bunker_seats=2, alive_player_ids={1, 2, 3},
    )
    defaults.update(overrides)
    return GroupGame(**defaults)


def test_reveal_phase_separates_state_and_names_idle_players() -> None:
    game = _bunker_game(phase="bunker_reveal", bunker_current_actor_user_id=2)
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> раскрытие характеристик, ход Bob." in text
    assert "<b>Что делать:</b> ждём выбор Bob в ЛС; остальные пока не ходят." in text


def test_vote_phase_separates_state_from_action() -> None:
    game = _bunker_game(phase="bunker_vote")
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> голосование на выбывание." in text
    assert "<b>Что делать:</b> голосуйте в ЛС" in text


@pytest.mark.asyncio
async def test_notify_bunker_reveal_turn_returns_failed_actor_id() -> None:
    game = _bunker_game(phase="bunker_reveal", bunker_current_actor_user_id=2)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramForbiddenError(method=SimpleNamespace(), message="blocked")))
    failed = await game_router._notify_bunker_reveal_turn(bot, game)
    assert failed == [2]


@pytest.mark.asyncio
async def test_notify_bunker_reveal_turn_returns_empty_list_on_success() -> None:
    game = _bunker_game(phase="bunker_reveal", bunker_current_actor_user_id=2, bunker_cards={})
    bot = SimpleNamespace(send_message=AsyncMock())
    failed = await game_router._notify_bunker_reveal_turn(bot, game)
    assert failed == []


@pytest.mark.asyncio
async def test_mid_game_reveal_advance_dm_failure_triggers_group_warning(monkeypatch) -> None:
    # Isolates the fix from the game-start role-push warning (which already
    # covers the very first reveal turn -- _send_role_to_user's bunker
    # branch sends the same actor a reveal-ready card at game start, noted
    # as a found-but-deferred redundant-double-send in the commit, not
    # fixed here to keep this stage low-risk). This test instead exercises
    # a later reveal-turn advance, where _notify_bunker_reveal_turn is the
    # *only* DM attempt for the newly-active actor.
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    monkeypatch.setattr(game_router, "_BOT_USERNAME_CACHE", None)

    game, error = await store.create_lobby(
        kind="bunker", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    for uid in range(2, 7):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    first_actor = started.bunker_current_actor_user_id
    assert first_actor is not None
    await store.set_message_id(game_id=started.game_id, message_id=555)

    class FakeBot:
        def __init__(self, *, exempt_user_id: int):
            self.group_messages: list[str] = []
            self.exempt_user_id = exempt_user_id

        async def get_me(self):
            return SimpleNamespace(username="selara_test_bot")

        async def send_message(self, chat_id, text, **kwargs):
            # Block every private DM except the actor who's revealing right
            # now (their own turn-confirmation DM isn't the thing under
            # test) -- whoever becomes the *next* actor is guaranteed to be
            # someone else, so this deterministically blocks that DM
            # regardless of the game's internal turn-order rule.
            if chat_id > 0 and chat_id != self.exempt_user_id:
                raise TelegramForbiddenError(method=SimpleNamespace(), message="blocked")
            if chat_id < 0:
                self.group_messages.append(text)
            return SimpleNamespace(message_id=1)

        async def edit_message_text(self, chat_id, message_id, text, **kwargs):
            pass

    field_key = next(iter(game_router.BUNKER_CARD_FIELDS))

    class FakeQuery:
        def __init__(self, *, user_id: int, data: str) -> None:
            self.data = data
            self.from_user = SimpleNamespace(id=user_id, username="u", first_name="U", last_name=None, is_bot=False)
            self.message = SimpleNamespace(chat=SimpleNamespace(id=-100, type="group", title="chat"), message_id=1)
            self.answers: list = []

        async def answer(self, text=None, show_alert=False):
            self.answers.append((text, show_alert))

    bot = FakeBot(exempt_user_id=first_actor)
    query = FakeQuery(user_id=first_actor, data=f"gbkr:{started.game_id}:{field_key}")
    await game_router.bunker_reveal_callback(query, bot=bot, chat_settings=_chat_settings())

    refreshed = await store.get_game(started.game_id)
    if refreshed.phase == "bunker_reveal" and refreshed.bunker_current_actor_user_id is not None:
        # Distinctive to the new unified warning (button + "нажмите
        # «Начать»" phrasing) -- the old inline board-note fallback used
        # different wording ("следующий игрок не получил карточку хода"),
        # so this can't pass by matching the pre-fix text instead.
        assert any("нажмите «Начать»" in text for text in bot.group_messages), bot.group_messages
