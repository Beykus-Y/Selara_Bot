"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (3/7,
Bredovukha): board text splits the previously-blended "Сейчас: <state and
action mixed>" line into separate "Сейчас" / "Что делать" lines for all 3
phases (category_pick / private_answers / public_vote), and the now-
redundant action-instruction lines inside the question/vote blocks were
trimmed since the board text already states them. The "Сдано N/M" / "Ждём"
waiting lists predate this stage and are unchanged. No mechanic change --
DM recovery for the free-text lie submission was already fixed in Stage 3
(deep-link payload + _show_role_for_user branch)."""
from __future__ import annotations

import importlib

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.game_state import GroupGame

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


def _bred_game(**overrides) -> GroupGame:
    defaults = dict(
        game_id="g1", kind="bredovukha", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara"}, status="started", bred_rounds=5,
    )
    defaults.update(overrides)
    return GroupGame(**defaults)


def test_category_pick_phase_separates_state_from_action() -> None:
    text = game_router._render_game_text(_bred_game(phase="category_pick", bred_current_selector_user_id=1), _chat_settings())
    assert "<b>Сейчас:</b> выбор темы раунда." in text
    assert "<b>Что делать:</b>" in text


def test_private_answers_phase_separates_state_from_action_and_has_no_duplicate_instruction() -> None:
    game = _bred_game(phase="private_answers", bred_question_prompt="Пропуск ____ тест", bred_current_category="Наука")
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> сбор ответов в ЛС." in text
    assert "<b>Что делать:</b> придумайте правдоподобную ложь" in text
    # The action instruction now lives only in the board's "Что делать" line,
    # not duplicated inside the question block underneath it.
    assert text.count("придумайте правдоподобную ложь") == 1


def test_public_vote_phase_separates_state_from_action() -> None:
    game = _bred_game(phase="public_vote", bred_question_prompt="Пропуск ____ тест", bred_current_category="Наука", bred_options=("а", "б"))
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> голосование за самый правдоподобный вариант." in text
    assert "<b>Что делать:</b> голосуйте кнопкой" in text


def test_waiting_lists_are_unchanged() -> None:
    game = _bred_game(phase="private_answers", bred_question_prompt="Пропуск ____ тест", bred_current_category="Наука", bred_lies={1: "ответ"})
    text = game_router._render_bred_question(game)
    assert "Сдано:</b> 1/3" in text
    assert "Ждём:" in text
    assert "Bob" in text and "Cara" in text
