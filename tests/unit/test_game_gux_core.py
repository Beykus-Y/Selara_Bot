"""GUX-01..03: rematch access, atomic lifecycle, confirmation and shared-board UX."""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatRoleDefinition
from selara.presentation.game_state import GameStore, GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def settings():
    return default_chat_settings(Settings.model_validate({
        "BOT_TOKEN": "123456:TEST",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        "BOT_USERNAME": "selara_test_bot",
        "WEB_AUTH_SECRET": "secret",
    }))


class Repo:
    def __init__(self, *, allowed: bool = True):
        self.allowed = allowed

    async def get_chat_display_name(self, *, chat_id, user_id):
        return None

    async def get_effective_role_definition(self, *, chat_id, user_id):
        if not self.allowed:
            return None
        return ChatRoleDefinition(
            chat_id=chat_id, role_code="game_master", title_ru="Game Master",
            rank=50, permissions=("manage_games",), is_system=False,
        )


class Query:
    def __init__(self, data, *, actor=1, chat=-100, message_id=42):
        self.data = data
        self.from_user = SimpleNamespace(
            id=actor, username="test", first_name="Tester",
            last_name=None, is_bot=False,
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat, type="group", title="Game Chat"),
            message_id=message_id,
        )
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


async def started_store(kind="spy"):
    store = GameStore()
    game, error = await store.create_lobby(
        kind=kind, chat_id=-100, chat_title="Game Chat",
        owner_user_id=1, owner_label="Owner", reveal_eliminated_role=True,
    )
    assert error is None
    for user_id in ((2, 3) if kind == "spy" else (2,)):
        await store.join(game_id=game.game_id, user_id=user_id, user_label=f"User {user_id}")
    started, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started is not None
    await store.set_message_id(game_id=game.game_id, message_id=42)
    return store, started


def bot():
    return SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        edit_message_reply_markup=AsyncMock(),
    )


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_rematch_requires_manage_games_even_for_original_owner(monkeypatch):
    store, game = await started_store()
    finished = await store.finish(game_id=game.game_id, winner_text="done")
    assert finished is not None
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    q = Query(f"game:rematch:{game.game_id}")
    await game_router.game_callback(
        q, bot=bot(), chat_settings=settings(),
        activity_repo=Repo(allowed=False), economy_repo=SimpleNamespace(),
    )
    assert q.answers[-1][1] is True
    assert "Недостаточно прав" in q.answers[-1][0]
    assert await store.list_active_games() == []
    assert finished.rematch_game_id is None


@pytest.mark.asyncio
async def test_rematch_atomic_against_duplicate_clicks_and_retired_source():
    store, game = await started_store("dice")
    finished = await store.finish(game_id=game.game_id, winner_text="done")
    assert finished is not None

    async def create(chat_id=-100):
        return await store.create_lobby(
            kind="dice", chat_id=chat_id, chat_title="Game Chat",
            owner_user_id=1, owner_label="Owner", reveal_eliminated_role=True,
            rematch_from_game_id=game.game_id,
        )

    results = await asyncio.gather(create(), create())
    assert sum(item is not None for item, _ in results) == 1
    assert finished.rematch_game_id is not None
    new_game = next(item for item, _ in results if item is not None)
    assert set(new_game.players) == {1}
    assert new_game.status == "lobby"
    await store.finish(game_id=new_game.game_id)
    repeated, error = await create()
    assert repeated is None
    assert "уже был создан" in error
    cross_chat, error = await create(-999)
    assert cross_chat is None
    assert error is not None


def test_started_common_board_omits_moderator_actions_but_keeps_dice_roll():
    spy = GroupGame(
        game_id="spy1", kind="spy", chat_id=-100, chat_title="chat",
        owner_user_id=1, players={1: "Owner", 2: "User"}, status="started",
        phase="freeplay",
    )
    shared = callbacks(game_router._build_game_controls(game=spy, bot_username="selara_test_bot"))
    moderator = callbacks(game_router._build_game_admin_controls(spy))
    assert "game:cancel:spy1" not in shared
    assert "game:reveal:spy1" not in shared
    assert "game:lrules:spy1" in shared
    assert "game:cancel:spy1" in moderator
    assert "game:reveal:spy1" in moderator
    dice = GroupGame(
        game_id="dice1", kind="dice", chat_id=-100, chat_title="chat",
        owner_user_id=1, players={1: "Owner", 2: "User"},
        status="started", phase="freeplay",
    )
    assert "gdice:dice1:roll" in callbacks(
        game_router._build_game_controls(game=dice, bot_username="selara_test_bot")
    )
    assert "gdice:dice1:roll" not in callbacks(game_router._build_game_admin_controls(dice))


@pytest.mark.asyncio
async def test_legacy_cancel_must_confirm_and_duplicate_confirmation_is_safe(monkeypatch):
    store, game = await started_store()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", AsyncMock())
    feed = AsyncMock()
    monkeypatch.setattr(game_router, "_send_game_feed_event", feed)
    b = bot()
    q = Query(f"game:cancel:{game.game_id}")
    await game_router.game_callback(
        q, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert (await store.get_game(game.game_id)).status == "started"
    assert b.send_message.await_count == 1
    markup = b.send_message.await_args.kwargs["reply_markup"]
    yes = next(x for x in callbacks(markup) if x.endswith(":yes"))
    confirm = Query(yes, message_id=99)
    await game_router.game_confirm_callback(
        confirm, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert (await store.get_game(game.game_id)).status == "finished"
    assert feed.await_count == 1
    duplicate = Query(yes, message_id=99)
    await game_router.game_confirm_callback(
        duplicate, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert "устарело" in duplicate.answers[-1][0]
    assert feed.await_count == 1


@pytest.mark.asyncio
async def test_confirmation_rejects_other_actor_and_revoked_permission(monkeypatch):
    store, game = await started_store()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    b = bot()
    await game_router.game_callback(
        Query(f"game:reveal:{game.game_id}"), bot=b,
        chat_settings=settings(), activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    yes = next(x for x in callbacks(b.send_message.await_args.kwargs["reply_markup"]) if x.endswith(":yes"))
    other = Query(yes, actor=2, message_id=99)
    await game_router.game_confirm_callback(
        other, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert other.answers[-1][1] is True
    assert (await store.get_game(game.game_id)).status == "started"
    denied = Query(yes, message_id=99)
    await game_router.game_confirm_callback(
        denied, bot=b, chat_settings=settings(),
        activity_repo=Repo(allowed=False), economy_repo=SimpleNamespace(),
    )
    assert "Права" in denied.answers[-1][0]
    assert (await store.get_game(game.game_id)).status == "started"


@pytest.mark.asyncio
async def test_confirmation_phase_change_and_cancel_do_not_finish_game(monkeypatch):
    store, game = await started_store()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    b = bot()
    await game_router.game_callback(
        Query(f"game:cancel:{game.game_id}"), bot=b,
        chat_settings=settings(), activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    markup = b.send_message.await_args.kwargs["reply_markup"]
    yes = next(x for x in callbacks(markup) if x.endswith(":yes"))
    no = next(x for x in callbacks(markup) if x.endswith(":no"))
    cancelled = Query(no, message_id=99)
    await game_router.game_confirm_callback(
        cancelled, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert (await store.get_game(game.game_id)).status == "started"
    already = Query(yes, message_id=99)
    await game_router.game_confirm_callback(
        already, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert "устарело" in already.answers[-1][0]
    await game_router.game_callback(
        Query(f"game:cancel:{game.game_id}"), bot=b,
        chat_settings=settings(), activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    newer_yes = next(x for x in callbacks(b.send_message.await_args.kwargs["reply_markup"]) if x.endswith(":yes"))
    game.round_no += 1  # simulated timer/phase change before confirmation
    stale = Query(newer_yes, message_id=99)
    await game_router.game_confirm_callback(
        stale, bot=b, chat_settings=settings(),
        activity_repo=Repo(), economy_repo=SimpleNamespace(),
    )
    assert "Фаза уже изменилась" in stale.answers[-1][0]
    assert (await store.get_game(game.game_id)).status == "started"


@pytest.mark.asyncio
async def test_active_rules_are_sent_separately_without_editing_live_board(monkeypatch):
    store, game = await started_store()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    board = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", board)
    b = bot()
    await game_router.game_callback(
        Query(f"game:lrules:{game.game_id}"), bot=b,
        chat_settings=settings(), activity_repo=Repo(allowed=False),
        economy_repo=SimpleNamespace(),
    )
    b.send_message.assert_awaited_once()
    assert "как играть" in b.send_message.await_args.kwargs["text"].lower()
    board.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_lobby_cancel_is_idempotent_without_prompt(monkeypatch):
    store = GameStore()
    lobby, _ = await store.create_lobby(
        kind="spy", chat_id=-100, chat_title="Game Chat",
        owner_user_id=1, owner_label="Owner", reveal_eliminated_role=True,
    )
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", AsyncMock())
    b = bot()
    q = Query(f"game:cancel:{lobby.game_id}")
    for _ in range(2):
        await game_router.game_callback(
            q, bot=b, chat_settings=settings(),
            activity_repo=Repo(), economy_repo=SimpleNamespace(),
        )
    assert (await store.get_game(lobby.game_id)).status == "finished"
    b.send_message.assert_not_awaited()


def test_quiz_admin_advance_button_is_question_scoped():
    game = GroupGame(
        game_id="quiz1", kind="quiz", chat_id=-100, chat_title="chat",
        owner_user_id=1, players={1: "Owner", 2: "User"},
        status="started", phase="freeplay", round_no=1,
        quiz_current_question_index=0,
    )
    first = callbacks(game_router._build_game_admin_controls(game))
    game.quiz_current_question_index = 1
    second = callbacks(game_router._build_game_admin_controls(game))
    assert "game:advance:quiz1:freeplay.0:1" in first
    assert "game:advance:quiz1:freeplay.1:1" in second
    assert first != second


@pytest.mark.asyncio
async def test_quiz_stop_token_cannot_finish_later_question_in_same_round():
    store, game = await started_store("quiz")
    original_index = game.quiz_current_question_index
    assert original_index is not None
    game.quiz_current_question_index = original_index + 1
    result, status = await store.finish_if_current(
        game_id=game.game_id, expected_phase=game.phase,
        expected_round=game.round_no,
        expected_quiz_question_index=original_index,
        winner_text="cancelled",
    )
    assert status == "stale"
    assert result is game
    assert (await store.get_game(game.game_id)).status == "started"
