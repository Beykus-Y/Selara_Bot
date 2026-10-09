"""GUX-07/08/09: explicit instructions and private-card usability regressions."""
from __future__ import annotations

import importlib

from selara.presentation.game_state import GroupGame

ui = importlib.import_module("selara.presentation.handlers.game.router")


def game(kind: str, phase: str = "private_answers", **extra) -> GroupGame:
    return GroupGame(
        game_id="g7", kind=kind, chat_id=-100, chat_title="Group",
        owner_user_id=1, players={1: "Alice", 2: "Bob", 3: "Cara"},
        status="started", phase=phase, **extra,
    )


def test_whoami_question_and_guess_are_discoverable_without_commands():
    g = game("whoami", "whoami_ask", whoami_current_actor_user_id=1)
    text = ui._render_whoami_status(g)
    assert "Я человек?" in text and "Я думаю, что я Шерлок Холмс" in text
    assert ui._extract_whoami_guess("Я думаю, что я Шерлок Холмс") == "Шерлок Холмс"
    assert "<b>Ходит:</b>" in text


def test_whoami_four_answer_types_are_explained_and_actor_remains_visible():
    g = game(
        "whoami", "whoami_answer", whoami_current_actor_user_id=1,
        whoami_pending_question_text="Я врач?",
    )
    text = ui._render_whoami_status(g)
    assert "Я врач?" in text
    assert all(term in text for term in ("Да", "нет", "не знаю", "неважно"))
    assert "ход переходит" in text


def test_bred_category_stage_identifies_selector_without_disclosing_answers():
    g = game(
        "bredovukha", "category_pick", bred_current_selector_user_id=2,
        bred_category_options=("Спорт", "Наука"),
    )
    text = ui._render_bred_question(g)
    assert "Bob" in text and "ЛС" in text
    assert "правильный ответ" not in text.lower()


def test_bred_private_submission_is_lie_not_truth_and_progress_is_readable():
    g = game(
        "bredovukha", "private_answers",
        bred_current_category="История",
        bred_question_prompt="Первым был ___",
        bred_lies={1: "Вариант А"},
    )
    text = ui._render_bred_question(g)
    assert "ЛОЖЬ" in text and "ЛС" in text
    assert "Сдано:</b> 1/3" in text
    assert "Bob" in text and "Cara" in text
    assert "Вариант А" not in text  # bluff remains secret


def test_bred_vote_stage_differentiates_truth_and_bluff():
    g = game(
        "bredovukha", "public_vote",
        bred_question_prompt="Первым был ___",
        bred_options=("Вариант А", "Вариант Б"),
        bred_votes={1: 0},
    )
    text = ui._render_bred_question(g)
    assert "ПРАВДУ" in text and "Прогресс:</b> 1/3" in text


def test_zlob_private_hand_shows_full_long_cards_and_needed_slots():
    long_card = "Очень длинная белая карта со смыслом и знаками < & >"
    g = game(
        "zlobcards", "private_answers",
        zlob_black_text="Выберите две карты ___ ___",
        zlob_black_slots=2,
        zlob_hands={1: (long_card, "Вторая карта", "Третья карта")},
    )
    text = ui._render_private_zlob_status_text(g, actor_user_id=1)
    assert "Нужно белых карт:</b> 2" in text
    assert "Очень длинная белая карта" in text
    assert "&lt; &amp; &gt;" in text
    markup = ui._build_private_zlob_submit_keyboard(g, actor_user_id=1)
    assert markup is not None
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert ("🃏 1 + 2", "gzlobp:g7:1:0-1") in buttons
    assert ("🃏 1 + 3", "gzlobp:g7:1:0-2") in buttons
    assert all(len(cb.encode()) <= 64 for _, cb in buttons)


def test_zlob_single_slot_preserves_single_card_submission():
    g = game(
        "zlobcards", "private_answers",
        zlob_black_text="___",
        zlob_black_slots=1,
        zlob_hands={1: ("First card", "Second card")},
    )
    markup = ui._build_private_zlob_submit_keyboard(g, actor_user_id=1)
    assert markup is not None
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "gzlobp:g7:1:0" in callbacks
    assert "gzlobp:g7:1:1" in callbacks
    assert all(":0-1" not in cb for cb in callbacks)


def test_zlob_observer_cannot_see_private_hand_or_buttons():
    g = game(
        "zlobcards", "private_answers",
        zlob_black_slots=2, zlob_hands={1: ("Secret One", "Secret Two")},
    )
    assert ui._build_private_zlob_submit_keyboard(g, actor_user_id=99) is None
    assert "Secret One" not in ui._render_private_zlob_status_text(g, actor_user_id=99)


def test_zlob_vote_stage_does_not_expose_card_owners():
    g = game(
        "zlobcards", "public_vote", zlob_black_text="___",
        zlob_options=("Funny One", "Funny Two"),
        zlob_option_owner_user_ids=(1, 2), zlob_votes={3: 0},
    )
    text = ui._render_zlob_round_status(g)
    assert "Прогресс:</b> 1/3" in text
    assert "Funny One" in text and "Funny Two" in text  # full options are visible, not their owners
    assert "Alice" not in text and "Bob" not in text
