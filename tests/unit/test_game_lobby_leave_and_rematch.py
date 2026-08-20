"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 2 (Spy as
reference game): a lobby "leave" action (previously missing entirely) and a
"rematch" action on a finished game (new lobby, same kind, settings copied
from the finished game, players NOT auto-carried over -- per Ilya's exact
spec)."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


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


@pytest.mark.asyncio
async def test_leave_removes_a_joined_player() -> None:
    store = GameStore()
    game = await _spy_lobby(store)
    await store.join(game_id=game.game_id, user_id=2, user_label="u2")

    updated, status = await store.leave(game_id=game.game_id, user_id=2)
    assert status == "left"
    assert updated is not None
    assert 2 not in updated.players


@pytest.mark.asyncio
async def test_leave_rejects_a_player_who_never_joined() -> None:
    store = GameStore()
    game = await _spy_lobby(store)

    updated, status = await store.leave(game_id=game.game_id, user_id=999)
    assert status == "not_joined"
    assert updated is not None
    assert 999 not in updated.players


@pytest.mark.asyncio
async def test_leave_rejects_the_lobby_owner() -> None:
    store = GameStore()
    game = await _spy_lobby(store, owner_user_id=1)

    updated, status = await store.leave(game_id=game.game_id, user_id=1)
    assert status == "owner_cannot_leave"
    assert updated is not None
    assert 1 in updated.players


@pytest.mark.asyncio
async def test_leave_rejects_once_the_game_has_started() -> None:
    store = GameStore()
    game = await _spy_lobby(store)
    await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    await store.join(game_id=game.game_id, user_id=3, user_label="u3")
    started, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started is not None

    updated, status = await store.leave(game_id=game.game_id, user_id=2)
    assert status == "not_lobby"
    assert updated is not None
    assert 2 in updated.players


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


@pytest.mark.asyncio
async def test_leave_callback_edits_board_and_acks(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)

    game = await _spy_lobby(store)
    await store.join(game_id=game.game_id, user_id=2, user_label="u2")

    query = FakeQuery(user_id=2, data=f"game:leave:{game.game_id}")
    await game_router.game_callback(
        query,
        bot=SimpleNamespace(),
        chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(),
        economy_repo=SimpleNamespace(),
    )

    safe_edit_mock.assert_awaited_once()
    assert query.answers[-1] == ("Вы покинули игру", False)
    refreshed = await store.get_game(game.game_id)
    assert 2 not in refreshed.players


@pytest.mark.asyncio
async def test_leave_callback_blocks_the_owner(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)

    game = await _spy_lobby(store, owner_user_id=7)

    query = FakeQuery(user_id=7, data=f"game:leave:{game.game_id}")
    await game_router.game_callback(
        query,
        bot=SimpleNamespace(),
        chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(),
        economy_repo=SimpleNamespace(),
    )

    safe_edit_mock.assert_not_awaited()
    assert query.answers[-1][1] is True  # show_alert
    assert "не может выйти" in query.answers[-1][0]


@pytest.mark.asyncio
async def test_rematch_creates_a_new_lobby_without_carrying_over_players(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)

    game = await _spy_lobby(store, owner_user_id=1)
    await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    await store.join(game_id=game.game_id, user_id=3, user_label="u3")
    started, error = await store.start(game_id=game.game_id)
    assert error is None
    finished = await store.finish(game_id=started.game_id, winner_text="done")
    assert finished is not None
    assert finished.status == "finished"

    query = FakeQuery(user_id=1, data=f"game:rematch:{finished.game_id}")
    await game_router.game_callback(
        query,
        bot=SimpleNamespace(),
        chat_settings=_chat_settings(),
        activity_repo=FakeActivityRepo(),
        economy_repo=SimpleNamespace(),
    )

    assert query.answers[-1] == ("Новая игра создана", False)
    safe_edit_mock.assert_awaited_once()
    new_game = safe_edit_mock.await_args.args[1]
    assert new_game.game_id != finished.game_id
    assert new_game.kind == "spy"
    assert new_game.status == "lobby"
    assert set(new_game.players) == {1}  # only the rematch-tapper, not 2/3 from before


@pytest.mark.asyncio
async def test_rematch_copies_bredovukha_round_count() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bredovukha",
        chat_id=-200,
        chat_title="chat",
        owner_user_id=1,
        owner_label="owner",
        reveal_eliminated_role=True,
    )
    assert error is None
    for user_id in (2, 3):
        await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
    tuned, tune_error = await store.set_bred_rounds(game_id=game.game_id, rounds=7)
    assert tune_error is None
    assert tuned.bred_rounds == 7

    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    finished = await store.finish(game_id=started.game_id, winner_text="done")
    assert finished is not None

    new_game, new_error = await store.create_lobby(
        kind="bredovukha",
        chat_id=-200,
        chat_title="chat",
        owner_user_id=1,
        owner_label="owner",
        reveal_eliminated_role=True,
    )
    assert new_error is None
    carried, carry_error = await store.set_bred_rounds(game_id=new_game.game_id, rounds=finished.bred_rounds)
    assert carry_error is None
    assert carried.bred_rounds == 7


def test_lobby_keyboard_has_a_leave_button() -> None:
    from selara.presentation.game_state import GroupGame

    game = GroupGame(
        game_id="g1",
        kind="spy",
        chat_id=-100,
        chat_title="chat",
        owner_user_id=1,
        players={1: "owner", 2: "u2"},
        status="lobby",
    )
    markup = game_router._build_game_controls(game=game, bot_username="selara_test_bot")
    assert markup is not None
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "game:leave:g1" in callbacks


def test_finished_board_has_a_rematch_button_and_no_other_action_buttons() -> None:
    from selara.presentation.game_state import GroupGame

    game = GroupGame(
        game_id="g2",
        kind="spy",
        chat_id=-100,
        chat_title="chat",
        owner_user_id=1,
        players={1: "owner", 2: "u2"},
        status="finished",
    )
    markup = game_router._build_game_controls(game=game, bot_username="selara_test_bot")
    assert markup is not None
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["game:rematch:g2"]
