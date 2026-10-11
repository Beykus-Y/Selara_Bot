"""Post-change GUX-00 presentation snapshots; these are not live-client QA."""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.game_state import (
    BunkerCard, GAME_DEFINITIONS, GAME_LAUNCHABLE_KINDS, GamePhase, GroupGame, QuizQuestion,
)

router = importlib.import_module("selara.presentation.handlers.game.router")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "game_phase_render"
ACTIVE_PHASES = {
    "dice": ("freeplay",),
    "spy": ("freeplay",),
    "quiz": ("freeplay",),
    "whoami": ("whoami_ask", "whoami_answer"),
    "bredovukha": ("category_pick", "private_answers", "public_vote"),
    "zlobcards": ("private_answers", "public_vote"),
    "bunker": ("bunker_reveal", "bunker_vote"),
    "mafia": ("night", "day_discussion", "day_vote", "day_execution_confirm"),
}
CASES = [(kind, phase) for kind in GAME_LAUNCHABLE_KINDS
         for phase in ("lobby", *ACTIVE_PHASES[kind], "finished")]
SECRET = "SECRET_ONLY_IN_DM"


def sample_game(kind: str, phase: str) -> GroupGame:
    """Fixed IDs, labels, secrets and progress; no RNG, clock or store involved."""
    count = GAME_DEFINITIONS[kind].min_players
    players = {i: ("Аня <&> 🦊" if i == 1 else f"Игрок {i}") for i in range(1, count + 1)}
    game = GroupGame(
        game_id="fixture", kind=kind, chat_id=-100, chat_title="Тест <&>", owner_user_id=1,
        players=players, alive_player_ids=set(players), phase=phase,
        status="lobby" if phase == "lobby" else "finished" if phase == "finished" else "started",
        round_no=2, created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        winner_text="Победа <&>" if phase == "finished" else None,
    )
    if kind == "dice":
        game.dice_scores = {1: 6} if phase != "finished" else {1: 6, 2: 3}
    elif kind == "spy":
        game.roles = {1: "Шпион", **{i: "Мирный" for i in players if i != 1}}
        game.spy_location = SECRET
        game.spy_votes = {1: 2}
    elif kind == "quiz":
        game.quiz_questions = (QuizQuestion(prompt="Сколько будет 2 < 3?", options=("Да & верно", "Нет", "Не знаю", "Другое"), answer_index=0),)
        game.quiz_current_question_index = 0
        game.quiz_answers = {1: 0}
        game.quiz_scores = {1: 1, 2: 0}
    elif kind == "whoami":
        game.roles = {i: SECRET for i in players}
        game.whoami_turn_order = tuple(players)
        game.whoami_current_actor_user_id = 1
        if phase == "whoami_answer":
            game.whoami_pending_question_text = "Я человек <&>?"
            game.whoami_pending_question_user_id = 1
    elif kind == "bredovukha":
        game.bred_current_selector_user_id = 1
        game.bred_category_options = ("Наука", "История", "Спорт")
        game.bred_current_category = "Наука"
        game.bred_question_prompt = "Учёные открыли ____ <&>."
        game.bred_correct_answer = SECRET
        game.bred_lies = {1: SECRET}
        game.bred_options = ("Первый <&>", "Второй", "Третий") if phase == "public_vote" else ()
        game.bred_option_owner_user_ids = (None, 1, 2)
        game.bred_votes = {1: 0}
        game.bred_scores = {1: 2, 2: 1, 3: 0}
    elif kind == "zlobcards":
        game.zlob_black_text = "Мой секрет — ____ <&>."
        game.zlob_hands = {i: (SECRET,) for i in players}
        game.zlob_submissions = {1: (SECRET,)}
        game.zlob_options = ("Первый <&>", "Второй", "Третий") if phase == "public_vote" else ()
        game.zlob_option_owner_user_ids = tuple(players)
        game.zlob_votes = {1: 1}
        game.zlob_scores = {1: 2, 2: 1, 3: 0}
    elif kind == "bunker":
        game.bunker_seats = 3
        game.bunker_catastrophe = "Метеорит <&>"
        game.bunker_condition = "Воды на год"
        game.bunker_current_actor_user_id = 1
        game.bunker_cards = {i: BunkerCard(profession="Врач <&>", age=SECRET, gender=SECRET,
            health_condition=SECRET, skill=SECRET, hobby=SECRET, phobia=SECRET, trait=SECRET, item=SECRET)
            for i in players}
        game.bunker_revealed_fields = {i: {"profession"} for i in players}
        game.bunker_votes = {1: 2}
    elif kind == "mafia":
        game.roles = {i: SECRET for i in players}
        game.day_vote_immune_user_id = 3
        game.day_votes = {1: 2}
        game.mafia_execution_candidate_user_id = 2
        game.execution_confirm_votes = {1: True}
    return game


def render_sample(kind: str, phase: str) -> dict:
    game = sample_game(kind, phase)
    settings = default_chat_settings(Settings.model_validate({
        "BOT_TOKEN": "123456:TEST", "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "BOT_USERNAME": "fixture_bot", "WEB_AUTH_SECRET": "fixture-secret",
    }))
    public = router._build_game_controls(game=game, bot_username="fixture_bot")
    manager = router._build_game_manager_controls(game)
    execution = router._build_mafia_execution_confirm_buttons(game)
    return {
        "text_lines": router._render_game_text(game, settings).splitlines(),
        "public_keyboard": public.model_dump(mode="json", exclude_none=True) if public else None,
        "manager_keyboard": manager.model_dump(mode="json", exclude_none=True) if manager else None,
        # Mafia execution is a separate feed message, not part of the board.
        "execution_prompt_lines": router._render_execution_confirm_prompt(game).splitlines()
        if kind == "mafia" and phase == "day_execution_confirm" else None,
        "execution_keyboard": execution.model_dump(mode="json", exclude_none=True) if execution else None,
    }


@pytest.mark.parametrize(("kind", "phase"), CASES, ids=[f"{k}-{p}" for k, p in CASES])
def test_phase_render_matches_reviewed_fixture(kind, phase):
    expected = json.loads((FIXTURES / f"{kind}-{phase}.json").read_text(encoding="utf-8"))
    assert render_sample(kind, phase) == expected


def test_fixture_inventory_covers_all_games_and_phases():
    assert set(ACTIVE_PHASES) == set(GAME_LAUNCHABLE_KINDS)
    assert {phase for _, phase in CASES} == set(get_args(GamePhase))
    assert {p.stem for p in FIXTURES.glob("*.json")} == {f"{k}-{p}" for k, p in CASES}


@pytest.mark.parametrize(("kind", "phase"), CASES)
def test_public_fixture_preserves_private_secrets_and_callback_limits(kind, phase):
    rendered = render_sample(kind, phase)
    if phase != "finished":
        assert SECRET not in "\n".join(rendered["text_lines"])
    if phase not in {"lobby", "finished"}:
        callbacks = [button.get("callback_data", "")
                     for row in rendered["public_keyboard"]["inline_keyboard"] for button in row]
        assert not any(data.startswith(("game:cancel:", "game:reveal:", "game:adv:"))
                       for data in callbacks)
    for key in ("public_keyboard", "manager_keyboard", "execution_keyboard"):
        if rendered[key]:
            for row in rendered[key]["inline_keyboard"]:
                for button in row:
                    assert len(button.get("callback_data", "").encode("utf-8")) <= 64
