"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (4/7,
WhoAmI): board text splits the blended "Сейчас: <state + action>" lines
into separate "Сейчас" / "Что делать" for both whoami_ask and
whoami_answer phases. Guess discoverability (example phrasing, hint on
unrecognized input) was already fixed in Stage 3 and its follow-up -- not
touched here. No mechanic change (any player except the asker can still
answer first; guessing/questions remain free-text, matching Ilya's
"text is the point" carve-out)."""
from __future__ import annotations

import importlib

from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def test_whoami_ask_phase_separates_current_state_from_action() -> None:
    game = GroupGame(
        game_id="g1", kind="whoami", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "A", 2: "B", 3: "C"}, status="started", phase="whoami_ask",
        whoami_current_actor_user_id=1,
    )
    text = game_router._render_whoami_status(game)
    assert "<b>Сейчас:</b> ход текущего игрока." in text
    assert "<b>Что делать:</b> задайте вопрос" in text


def test_whoami_answer_phase_separates_current_state_from_action() -> None:
    game = GroupGame(
        game_id="g1", kind="whoami", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "A", 2: "B", 3: "C"}, status="started", phase="whoami_answer",
        whoami_current_actor_user_id=1, whoami_pending_question_text="Я живое существо?",
    )
    text = game_router._render_whoami_status(game)
    assert "<b>Сейчас:</b> ждём ответ стола." in text
    assert "<b>Что делать:</b>" in text
    assert "да / нет / не знаю / неважно" in text
