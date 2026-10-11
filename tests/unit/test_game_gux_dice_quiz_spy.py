"""Focused GUX-04/05/06 regressions: public dice, quiz and Spy UX.

These tests deliberately do not modify RNG, score formulas, roles or payouts.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.game_state import GameStore, GroupGame, QuizQuestion

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class Query:
    def __init__(self, data: str, *, user_id: int = 1, chat_id: int = -100):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="player", first_name="P", last_name=None, is_bot=False)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, title="Group", type="group"),
            message_id=42,
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


async def create_started(store, kind: str):
    game, error = await store.create_lobby(
        kind=kind, chat_id=-100, chat_title="Group", owner_user_id=1,
        owner_label="Owner", reveal_eliminated_role=True,
    )
    assert error is None and game is not None
    for player in range(2, 4 if kind == "spy" else 3):
        joined, status = await store.join(game_id=game.game_id, user_id=player, user_label=f"Player {player}")
        assert joined is not None and status == "joined"
    started, error = await store.start(game_id=game.game_id)
    assert started is not None and error is None
    return started


def attach(monkeypatch, store):
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    edit = AsyncMock()
    feed = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", edit)
    monkeypatch.setattr(game_router, "_send_game_feed_event", feed)
    return edit, feed


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


@pytest.mark.asyncio
async def test_dice_callback_wrong_chat_cannot_roll_or_consume_rng(monkeypatch):
    store = GameStore()
    edit, feed = attach(monkeypatch, store)
    game = await create_started(store, "dice")
    query = Query(f"gdice:{game.game_id}:roll", chat_id=-777)
    await game_router.dice_roll_callback(query, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "другого чата" in query.answers[-1][0]
    assert (await store.get_game(game.game_id)).dice_scores == {}
    edit.assert_not_awaited()
    feed.assert_not_awaited()


@pytest.mark.asyncio
async def test_dice_roll_is_one_shot_and_final_scores_survive(monkeypatch):
    store = GameStore()
    game = await create_started(store, "dice")
    monkeypatch.setattr("selara.presentation.game_state.random.randint", lambda a, b: 6)
    first, outcome, error = await store.dice_register_roll(game_id=game.game_id, user_id=1, expected_chat_id=-100)
    assert error is None and outcome is not None and outcome.roll_value == 6
    before = dict(first.dice_scores)
    _, repeat, error = await store.dice_register_roll(game_id=game.game_id, user_id=1, expected_chat_id=-100)
    assert repeat is None and "уже бросили" in error
    assert game.dice_scores == before
    finished, final, error = await store.dice_register_roll(game_id=game.game_id, user_id=2, expected_chat_id=-100)
    assert error is None and final is not None and final.finished
    assert finished.status == "finished"
    text = game_router._render_game_text(finished, settings())
    assert "<b>Итоговые броски:</b>" in text
    assert "Owner" in text and "Player 2" in text
    assert "Ждём бросок" not in text


@pytest.mark.asyncio
async def test_dice_store_chat_check_is_inside_mutation_lock():
    store = GameStore()
    game = await create_started(store, "dice")
    _, result, error = await store.dice_register_roll(
        game_id=game.game_id, user_id=1, expected_chat_id=-777,
    )
    assert result is None and error == "Эта кнопка из другого чата"
    assert game.dice_scores == {}


def test_quiz_buttons_bind_question_version_and_keep_full_options_readable():
    long_a = "Вариант с очень длинным объяснением A & <дополнение>"
    long_b = "Вариант с очень длинным объяснением B"
    question = QuizQuestion(prompt="Что выбрать?", options=(long_a, long_b), answer_index=0)
    game = GroupGame(
        game_id="1234567890", kind="quiz", chat_id=-100, chat_title="Group",
        owner_user_id=1, players={1: "Owner", 2: "Player"},
        status="started", phase="freeplay",
        quiz_questions=(question, question),
        quiz_current_question_index=1,
    )
    keys = game_router._build_quiz_answer_buttons(game)
    assert keys is not None
    payloads = callbacks(keys)
    assert "gquiz:1234567890:1:0" in payloads
    assert "gquiz:1234567890:1:1" in payloads
    assert all(len(p.encode("utf-8")) <= 64 for p in payloads)
    labels = [button.text for row in keys.inline_keyboard for button in row]
    assert labels[0].startswith("A. ") and labels[1].startswith("B. ")
    text = game_router._render_quiz_question(game)
    assert "A. Вариант с очень длинным объяснением A &amp; &lt;дополнение&gt;" in text
    assert long_b in text


@pytest.mark.asyncio
async def test_quiz_old_keyboard_and_foreign_chat_never_change_answers(monkeypatch):
    store = GameStore()
    edit, _ = attach(monkeypatch, store)
    game = await create_started(store, "quiz")
    idx = game.quiz_current_question_index
    assert idx is not None
    old = Query(f"gquiz:{game.game_id}:0")
    await game_router.quiz_answer_callback(old, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "устарела" in old.answers[-1][0]
    foreign = Query(f"gquiz:{game.game_id}:{idx}:0", chat_id=-777)
    await game_router.quiz_answer_callback(foreign, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "другого чата" in foreign.answers[-1][0]
    assert game.quiz_answers == {}
    edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_quiz_stale_previous_question_rejected_atomically(monkeypatch):
    store = GameStore()
    edit, _ = attach(monkeypatch, store)
    game = await create_started(store, "quiz")
    original_question = game.quiz_current_question_index
    assert original_question is not None
    advanced, outcome, error = await store.quiz_resolve_round(game_id=game.game_id, force=True)
    assert error is None and outcome is not None and advanced.status == "started"
    stale = Query(f"gquiz:{game.game_id}:{original_question}:0")
    await game_router.quiz_answer_callback(stale, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "закрыт" in stale.answers[-1][0]
    _, result, error = await store.quiz_submit_answer(
        game_id=game.game_id, user_id=1, option_index=0,
        expected_chat_id=-100, expected_question_index=original_question,
    )
    assert result is None and "закрыт" in error
    assert game.quiz_answers == {}
    edit.assert_not_awaited()


def test_quiz_finished_board_keeps_scoreboard():
    question = QuizQuestion(prompt="Тест?", options=("Да", "Нет"), answer_index=0)
    game = GroupGame(
        game_id="gq", kind="quiz", chat_id=-100, chat_title="Group",
        owner_user_id=1, players={1: "Owner", 2: "Player"},
        status="finished", phase="finished", quiz_questions=(question,),
        quiz_scores={1: 3, 2: 1}, winner_text="Победитель: Owner",
    )
    text = game_router._render_game_text(game, settings())
    assert "<b>Счёт:</b>" in text and "Owner" in text and "Player" in text
    assert "Победитель: Owner" in text


@pytest.mark.asyncio
async def test_spy_my_vote_is_private_alert_and_no_secret_leaks(monkeypatch):
    store = GameStore()
    edit, feed = attach(monkeypatch, store)
    game = await create_started(store, "spy")
    query = Query(f"gspy:{game.game_id}:mine", user_id=1)
    await game_router.spy_vote_callback(query, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "ещё не голосовали" in query.answers[-1][0]
    _, resolution, _, error = await store.spy_register_vote(
        game_id=game.game_id, voter_user_id=1, target_user_id=2, expected_chat_id=-100,
    )
    assert error is None and resolution is None
    again = Query(f"gspy:{game.game_id}:mine", user_id=1)
    await game_router.spy_vote_callback(again, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "Player 2" in again.answers[-1][0]
    assert game.spy_location not in again.answers[-1][0]
    assert again.answers[-1][1] is True
    assert game.status == "started"
    edit.assert_not_awaited()
    feed.assert_not_awaited()


@pytest.mark.asyncio
async def test_spy_other_chat_cannot_mutate_or_expose_vote(monkeypatch):
    store = GameStore()
    edit, _ = attach(monkeypatch, store)
    game = await create_started(store, "spy")
    bad = Query(f"gspy:{game.game_id}:2", chat_id=-777)
    await game_router.spy_vote_callback(bad, bot=SimpleNamespace(), chat_settings=settings(), economy_repo=SimpleNamespace())
    assert "другого чата" in bad.answers[-1][0]
    assert game.spy_votes == {}
    assert edit.await_count == 0


def test_spy_keyboard_has_vote_self_service_but_no_manager_controls():
    game = GroupGame(
        game_id="1234567890", kind="spy", chat_id=-100, chat_title="Group",
        owner_user_id=1, players={1: "Owner", 2: "Second", 3: "Third"},
        status="started", phase="freeplay",
    )
    keyboard = game_router._build_spy_vote_buttons(game)
    assert keyboard is not None
    actions = callbacks(keyboard)
    assert "gspy:1234567890:mine" in actions
    assert "gspy:1234567890:noop" in actions
    assert not any(action.startswith(("game:cancel", "game:reveal")) for action in actions)
