"""Games UX 2.0 core: launcher permissions, safe lifecycle, live navigation.

No real Telegram traffic or economic rewards: callbacks use an isolated
GameStore plus mocked board/notifications. See issue #194 GUX-01..03.
"""
from __future__ import annotations

import asyncio
import importlib
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatRoleDefinition
from selara.presentation.game_state import GameStore, GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class ActivityRepo:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed

    async def get_chat_display_name(self, *, chat_id: int, user_id: int):
        return None

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        if not self.allowed:
            return None
        return ChatRoleDefinition(
            chat_id=chat_id, role_code="game_master", title_ru="Game Master",
            rank=50, permissions=("manage_games",), is_system=False,
        )


class Query:
    def __init__(self, *, data: str, user_id: int = 1, message_id: int = 500):
        self.data = data
        self.from_user = SimpleNamespace(
            id=user_id, username=None, first_name="User", last_name=None, is_bot=False,
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="group", title="Group"),
            message_id=message_id,
        )
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def settings():
    config = Settings.model_validate({
        "BOT_TOKEN": "123456:TEST",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        "BOT_USERNAME": "selara_test_bot",
        "WEB_AUTH_SECRET": "secret",
    })
    return default_chat_settings(config)


def bot():
    return SimpleNamespace(
        send_message=AsyncMock(),
        edit_message_text=AsyncMock(),
        delete_message=AsyncMock(),
    )


async def create_started(store: GameStore, kind: str = "dice"):
    game, error = await store.create_lobby(
        kind=kind, chat_id=-100, chat_title="Group",
        owner_user_id=1, owner_label="Creator",
        reveal_eliminated_role=True,
    )
    assert game is not None and error is None
    players = (2, 3) if kind == "spy" else (2,)
    for player_id in players:
        joined, status = await store.join(
            game_id=game.game_id, user_id=player_id, user_label=str(player_id),
        )
        assert joined is not None and status == "joined"
    started, error = await store.start(game_id=game.game_id)
    assert started is not None and error is None
    return started


def prepare(monkeypatch, store: GameStore):
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit = AsyncMock()
    feed = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit)
    monkeypatch.setattr(game_router, "_send_game_feed_event", feed)
    monkeypatch.setattr(game_router, "_cancel_phase_timer", lambda game_id: None)
    return safe_edit, feed


async def callback(query, *, bot_obj=None, repo=None):
    return await game_router.game_callback(
        query, bot=bot_obj or bot(), chat_settings=settings(),
        activity_repo=repo or ActivityRepo(), economy_repo=SimpleNamespace(),
    )


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def test_shared_board_keeps_player_action_and_hides_raw_manager_mutations():
    for kind, phase, primary in [
        ("dice", "freeplay", "gdice:1234567890:roll"),
        ("spy", "freeplay", "gspy:1234567890:noop"),
    ]:
        game = GroupGame(
            game_id="1234567890", kind=kind, chat_id=-100,
            chat_title="Group", owner_user_id=1,
            players={1: "A", 2: "B"}, status="started", phase=phase,
        )
        kb = game_router._build_game_controls(game=game, bot_username="selara_test_bot")
        assert kb is not None
        items = callbacks(kb)
        assert primary in items
        assert f"game:manage:{game.game_id}" in items
        assert f"game:lrules:{game.game_id}" in items
        assert not any(key.startswith(("game:cancel:", "game:advance:", "game:reveal:")) for key in items)
        assert primary in callbacks(kb)[:-2]
        assert [button.text for button in kb.inline_keyboard[-1]] == ["❓ Как играть", "⚙️ Ведущему"]


def test_admin_control_callbacks_have_phase_version_and_fit_telegram_limit():
    game = GroupGame(
        game_id="1234567890", kind="mafia", chat_id=-100,
        chat_title="Group", owner_user_id=1, players={1: "A"},
        status="started", phase="day_execution_confirm", round_no=99,
    )
    markup = game_router._build_game_manager_controls(game)
    assert markup is not None
    items = callbacks(markup)
    assert "game:adv:1234567890:day_execution_confirm:99" in items
    confirm = game_router._build_lifecycle_confirmation_keyboard(
        game=game, action="stop", issued_at=1799999999,
    )
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(markup) + callbacks(confirm))


@pytest.mark.asyncio
async def test_game_manager_requires_manage_games_and_does_not_mutate(monkeypatch):
    store = GameStore()
    prepare(monkeypatch, store)
    game = await create_started(store)
    b = bot()
    denied = Query(data=f"game:manage:{game.game_id}", user_id=2)
    await callback(denied, bot_obj=b, repo=ActivityRepo(False))
    assert denied.answers[-1][0] == "Недостаточно прав для управления игрой."
    b.send_message.assert_not_awaited()
    allowed = Query(data=f"game:manage:{game.game_id}", user_id=1)
    await callback(allowed, bot_obj=b)
    b.send_message.assert_awaited_once()
    assert (await store.get_game(game.game_id)).status == "started"


@pytest.mark.asyncio
async def test_live_rules_do_not_replace_running_board(monkeypatch):
    store = GameStore()
    edit, _ = prepare(monkeypatch, store)
    game = await create_started(store)
    b = bot()
    q = Query(data=f"game:lrules:{game.game_id}", user_id=2)
    await callback(q, bot_obj=b, repo=ActivityRepo(False))
    b.send_message.assert_awaited_once()
    b.edit_message_text.assert_not_awaited()
    edit.assert_not_awaited()
    assert (await store.get_game(game.game_id)).status == "started"


@pytest.mark.asyncio
async def test_legacy_stop_requires_second_press_and_can_be_cancelled(monkeypatch):
    store = GameStore()
    edit, feed = prepare(monkeypatch, store)
    game = await create_started(store)
    b = bot()
    q = Query(data=f"game:cancel:{game.game_id}")
    await callback(q, bot_obj=b)
    assert (await store.get_game(game.game_id)).status == "started"
    confirm = callbacks(b.send_message.await_args.kwargs["reply_markup"])
    assert len(confirm) == 2
    decline = Query(data=confirm[1])
    await callback(decline, bot_obj=b)
    b.edit_message_text.assert_awaited_once()
    assert (await store.get_game(game.game_id)).status == "started"
    edit.assert_not_awaited()
    feed.assert_not_awaited()


@pytest.mark.asyncio
async def test_double_confirmation_finalizes_exactly_once(monkeypatch):
    store = GameStore()
    edit, feed = prepare(monkeypatch, store)
    game = await create_started(store)
    confirm = game_router._build_lifecycle_confirmation_keyboard(
        game=game, action="stop", issued_at=int(time()),
    )
    data = callbacks(confirm)[0]
    a, b = Query(data=data), Query(data=data)
    await asyncio.gather(callback(a), callback(b))
    assert (await store.get_game(game.game_id)).status == "finished"
    assert edit.await_count == 1
    assert feed.await_count == 1
    assert sorted([a.answers[-1][0], b.answers[-1][0]]) == [
        "Игра завершена",
        "Игра изменилась или подтверждение истекло. Вернитесь к доске через /gameboard.",
    ]


@pytest.mark.asyncio
async def test_stale_phase_and_expired_confirmation_are_rejected(monkeypatch):
    store = GameStore()
    edit, feed = prepare(monkeypatch, store)
    game = await create_started(store)
    valid = callbacks(game_router._build_lifecycle_confirmation_keyboard(
        game=game, action="stop", issued_at=int(time()),
    ))[0]
    old = callbacks(game_router._build_lifecycle_confirmation_keyboard(
        game=game, action="stop", issued_at=int(time()) - 181,
    ))[0]
    await callback(Query(data=old))
    game.phase = "changed_phase"
    await callback(Query(data=valid))
    assert (await store.get_game(game.game_id)).status == "started"
    edit.assert_not_awaited()
    feed.assert_not_awaited()


@pytest.mark.asyncio
async def test_spy_reveal_requires_confirmation_and_permission(monkeypatch):
    store = GameStore()
    edit, feed = prepare(monkeypatch, store)
    game = await create_started(store, kind="spy")
    b = bot()
    original = Query(data=f"game:reveal:{game.game_id}")
    await callback(original, bot_obj=b)
    assert (await store.get_game(game.game_id)).status == "started"
    yes = callbacks(b.send_message.await_args.kwargs["reply_markup"])[0]
    await callback(Query(data=yes, user_id=2), bot_obj=b, repo=ActivityRepo(False))
    assert (await store.get_game(game.game_id)).status == "started"
    await callback(Query(data=yes), bot_obj=b)
    assert (await store.get_game(game.game_id)).status == "finished"
    edit.assert_awaited_once()
    feed.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_advance_shows_fresh_controls_not_mutate(monkeypatch):
    store = GameStore()
    prepare(monkeypatch, store)
    game = await create_started(store)
    b = bot()
    q = Query(data=f"game:advance:{game.game_id}")
    await callback(q, bot_obj=b)
    b.send_message.assert_awaited_once()
    assert (await store.get_game(game.game_id)).status == "started"


@pytest.mark.asyncio
async def test_gameboard_recovery_is_available_without_manager_permissions(monkeypatch):
    store = GameStore()
    edit, _ = prepare(monkeypatch, store)
    game = await create_started(store)
    await store.set_message_id(game_id=game.game_id, message_id=888)

    msg = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group"),
        answer=AsyncMock(),
    )
    await game_router.game_board_command(msg, bot=bot(), chat_settings=settings())
    msg.answer.assert_awaited_once()
    assert msg.answer.await_args.kwargs["reply_to_message_id"] == 888
    assert "Нажмите на сообщение" in msg.answer.await_args.args[0]
    edit.assert_not_awaited()
    assert (await store.get_game(game.game_id)).status == "started"


@pytest.mark.asyncio
async def test_gameboard_recovery_reports_absent_game(monkeypatch):
    store = GameStore()
    prepare(monkeypatch, store)
    msg = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group"),
        answer=AsyncMock(),
    )
    await game_router.game_board_command(msg, bot=bot(), chat_settings=settings())
    assert "нет активной игры" in msg.answer.await_args.args[0]
