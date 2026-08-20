"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (Quiz):
board text explicitly separates "Сейчас" / "Что делать" (the "Ответили N/M"
+ "Ждём: ..." waiting list already existed before this stage -- Quiz was
already close to the target pattern). The answer-confirmation toast now
echoes back which option was picked (audit finding: previously a player
couldn't verify their own choice after tapping). No mechanic change."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.game_state import GroupGame, QuizQuestion

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


def _quiz_game() -> GroupGame:
    question = QuizQuestion(prompt="2+2?", options=("3", "4", "5", "6"), answer_index=1)
    return GroupGame(
        game_id="g1", kind="quiz", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob"}, status="started", phase="freeplay",
        quiz_questions=(question,), quiz_current_question_index=0,
    )


def test_board_text_separates_current_state_from_action() -> None:
    text = game_router._render_game_text(_quiz_game(), _chat_settings())
    assert "<b>Сейчас:</b>" in text
    assert "<b>Что делать:</b>" in text


def test_question_block_already_names_who_is_still_answering() -> None:
    # Pre-existing pattern -- confirming it survives the board-text edit.
    text = game_router._render_quiz_question(_quiz_game())
    assert "Ответили:</b> 0/2" in text
    assert "Ждём:" in text


class FakeQuery:
    def __init__(self, *, user_id: int, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="u", first_name="U", last_name=None, is_bot=False)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=-100, type="group", title="chat"), message_id=1)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


@pytest.mark.asyncio
async def test_answer_toast_echoes_the_chosen_letter(monkeypatch) -> None:
    from selara.presentation.game_state import GameStore

    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    safe_edit_mock = AsyncMock()
    monkeypatch.setattr(game_router, "_safe_edit_or_send_game_board", safe_edit_mock)

    game, error = await store.create_lobby(
        kind="quiz", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None

    query = FakeQuery(user_id=1, data=f"gquiz:{started.game_id}:1")
    await game_router.quiz_answer_callback(query, bot=SimpleNamespace(), chat_settings=_chat_settings(), economy_repo=SimpleNamespace())

    assert query.answers[-1] == ("Ответ принят: B", False)
