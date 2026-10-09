"""Safety and compatibility checks for round-bound game UX callbacks (GUX-07–09)."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GameStore, GroupGame

ui = importlib.import_module("selara.presentation.handlers.game.router")


def make(kind, phase, round_no=2):
    return GroupGame(
        game_id="g7abc", kind=kind, chat_id=-100, chat_title="Group",
        owner_user_id=1, players={1: "Alice", 2: "Bob", 3: "Cara"},
        status="started", phase=phase, round_no=round_no,
    )


def store_for(monkeypatch, game):
    store = GameStore()
    store._by_id[game.game_id] = game
    monkeypatch.setattr(ui, "GAME_STORE", store)
    monkeypatch.setattr(ui, "_refresh_game_player_label", AsyncMock(return_value="Bob"))
    monkeypatch.setattr(ui, "_safe_edit_or_send_game_board", AsyncMock())
    return store


class Query:
    def __init__(self, data, chat_id=-100):
        self.data = data
        self.from_user = SimpleNamespace(id=2, username="bob", first_name="Bob", last_name=None, is_bot=False)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type="supergroup"), message_id=1)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_whoami_old_question_cannot_answer_next_question_or_other_chat(monkeypatch):
    game = make("whoami", "whoami_answer")
    game.whoami_current_actor_user_id = 1
    game.whoami_pending_question_text = "Я герой?"
    store = store_for(monkeypatch, game)
    for rev, chat, needle in [(1, -100, "старый вопрос"), (0, -777, "другого чата")]:
        _, result, error = await store.whoami_answer_question(
            game_id=game.game_id, responder_user_id=2, answer_code="no",
            expected_question_version=rev, expected_chat_id=chat,
        )
        assert result is None and needle in error
    assert game.whoami_pending_question_text == "Я герой?"
    expected_version = int(game.phase_started_at.timestamp() * 1_000_000)
    _, result, error = await store.whoami_answer_question(
        game_id=game.game_id, responder_user_id=2, answer_code="no",
        expected_question_version=expected_version, expected_chat_id=-100,
    )
    assert error is None and result is not None
    assert len(game.whoami_history) == 1


def test_whoami_action_revision_follows_question_history():
    game = make("whoami", "whoami_answer")
    game.whoami_current_actor_user_id = 1
    game.whoami_pending_question_text = "Я живой?"
    markup = ui._build_whoami_answer_buttons(game)
    revision = int(game.phase_started_at.timestamp() * 1_000_000)
    assert f"gwho:g7abc:{revision}:yes" in callbacks(markup)
    assert f"gwho:g7abc:{revision}:unknown" in callbacks(markup)


@pytest.mark.asyncio
async def test_whoami_old_and_foreign_callbacks_do_not_change_game(monkeypatch):
    game = make("whoami", "whoami_answer")
    game.whoami_current_actor_user_id = 1
    game.whoami_pending_question_text = "Я космонавт?"
    store_for(monkeypatch, game)
    for data, chat, needle in [
        ("gwho:g7abc:no", -100, "устарел"),
        ("gwho:g7abc:0:no", -200, "другого чата"),
    ]:
        query = Query(data, chat)
        await ui.whoami_answer_callback(
            query, bot=SimpleNamespace(), chat_settings=SimpleNamespace(), activity_repo=SimpleNamespace(),
        )
        assert needle in query.answers[-1][0]
        assert game.whoami_pending_question_text == "Я космонавт?"


@pytest.mark.asyncio
async def test_bred_vote_guards_round_and_chat_before_recording(monkeypatch):
    game = make("bredovukha", "public_vote", round_no=3)
    game.bred_options = ("Ответ", "Ложь")
    store = store_for(monkeypatch, game)
    for rev, chat, needle in [(2, -100, "предыдущего раунда"), (3, -777, "другого чата")]:
        _, result, error = await store.bred_register_vote(
            game_id=game.game_id, voter_user_id=2, option_index=0,
            expected_round_no=rev, expected_chat_id=chat,
        )
        assert result is None and needle in error
    assert game.bred_votes == {}
    assert "gbred:g7abc:3:0" in callbacks(ui._build_bred_vote_buttons(game))


def test_bred_replies_only_capture_current_bluff_prompt_and_plain_text():
    game = make("bredovukha", "private_answers")
    assert ui._should_handle_bred_private_answer(game, text="Ложь")
    assert not ui._should_handle_bred_private_answer(game, text="Ложь", replied_prompt="Personal AI: hello!")
    prompt = "Бредовуха. Контекст блефа: g7abc/2"
    assert ui._should_handle_bred_private_answer(game, text="Ложь", replied_prompt=prompt)
    assert not ui._should_handle_bred_private_answer(
        game, text="Ложь", replied_prompt="Контекст блефа: g7abc/1"
    )


@pytest.mark.asyncio
async def test_zlob_hand_and_vote_guards_do_not_mutate_stale_round(monkeypatch):
    game = make("zlobcards", "private_answers", round_no=4)
    game.zlob_black_slots = 2
    game.zlob_black_text = "__ и __"
    game.zlob_hands = {2: ("Карта А", "Карта Б", "Карта В")}
    store = store_for(monkeypatch, game)
    _, result, error = await store.zlob_submit_cards(
        game_id=game.game_id, user_id=2, card_indexes=(0, 1),
        expected_round_no=3, expected_chat_id=-100,
    )
    assert result is None and "предыдущего раунда" in error
    assert game.zlob_submissions == {}
    buttons = callbacks(ui._build_private_zlob_submit_keyboard(game, actor_user_id=2))
    assert "gzlobp:g7abc:4:0-1" in buttons
    assert len(buttons) == 4
    game.phase = "public_vote"
    game.zlob_options = ("Вариант", "Другой")
    game.zlob_option_owner_user_ids = (1, 2)
    _, result, error = await store.zlob_register_vote(
        game_id=game.game_id, voter_user_id=2, option_index=0,
        expected_round_no=3, expected_chat_id=-100,
    )
    assert result is None and "предыдущего раунда" in error
    assert game.zlob_votes == {}
    assert "gzlobv:g7abc:4:0" in callbacks(ui._build_zlob_vote_buttons(game))


def test_anonymous_bred_and_zlob_options_are_fully_readable_and_escaped():
    bred = make("bredovukha", "public_vote")
    bred.bred_question_prompt = "Тест ____"
    bred.bred_options = ("Пишем <правду>", "Ложь & неправда")
    text = ui._render_bred_question(bred)
    assert "Варианты полностью" in text
    assert "&lt;правду&gt;" in text and "&amp;" in text

    zlob = make("zlobcards", "public_vote")
    zlob.zlob_black_text = "__"
    zlob.zlob_options = ("Ответ <A>", "Ответ & B")
    zlob.zlob_option_owner_user_ids = (1, 2)
    text = ui._render_zlob_round_status(zlob)
    assert "Все варианты" in text and "&lt;A&gt;" in text
    assert "Alice" not in text and "Bob" not in text


@pytest.mark.asyncio
async def test_bred_category_picker_does_not_reuse_previous_round_button(monkeypatch):
    game = make("bredovukha", "category_pick", round_no=5)
    game.bred_current_selector_user_id = 2
    game.bred_category_options = ("История", "Наука")
    store = store_for(monkeypatch, game)
    markup = ui._build_bred_category_buttons(game)
    assert "gbredcat:g7abc:5:0" in callbacks(markup)
    _, category, error = await store.bred_choose_category(
        game_id=game.game_id, actor_user_id=2, option_index=0,
        expected_round_no=4, expected_chat_id=-100,
    )
    assert category is None and "предыдущего раунда" in error
    assert game.phase == "category_pick" and not game.bred_current_category
