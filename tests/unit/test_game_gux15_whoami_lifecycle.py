"""GUX-15: WhoAmI full lifecycle and stale/private-question safety at GameStore level."""
from __future__ import annotations

import pytest

from selara.presentation.game_state import GameStore


@pytest.mark.asyncio
async def test_whoami_questions_answer_rounds_and_all_players_solve() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami", chat_id=-100, chat_title="group",
        owner_user_id=1, owner_label="u1", reveal_eliminated_role=False,
    )
    assert error is None and game is not None
    for uid in (2, 3):
        _, state = await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
        assert state == "joined"

    started, error = await store.start(game_id=game.game_id)
    assert error is None and started is game
    assert game.status == "started" and game.phase == "whoami_ask"
    assert set(game.roles) == set(game.players)
    assert len(set(game.roles.values())) == len(game.players)

    first_actor = game.whoami_current_actor_user_id
    assert first_actor in game.players
    other = next(uid for uid in game.players if uid != first_actor)
    _, refused, error = await store.whoami_submit_question(
        game_id=game.game_id, actor_user_id=other, question_text="Это известный герой?",
    )
    assert refused is None and error == "Сейчас ход другого игрока"

    _, question, error = await store.whoami_submit_question(
        game_id=game.game_id, actor_user_id=first_actor, question_text="Это известный герой",
    )
    assert error is None and question is not None
    assert game.phase == "whoami_answer" and question.question_text.endswith("?")
    version = int(game.phase_started_at.timestamp() * 1_000_000)

    _, refused, error = await store.whoami_answer_question(
        game_id=game.game_id, responder_user_id=other, answer_code="no",
        expected_chat_id=-200, expected_question_version=version,
    )
    assert refused is None and error == "Эта кнопка из другого чата"
    assert game.whoami_pending_question_user_id == first_actor

    _, refused, error = await store.whoami_answer_question(
        game_id=game.game_id, responder_user_id=first_actor, answer_code="no",
        expected_chat_id=-100, expected_question_version=version,
    )
    assert refused is None and "не может отвечать сам себе" in error
    assert game.phase == "whoami_answer"

    _, answered, error = await store.whoami_answer_question(
        game_id=game.game_id, responder_user_id=other, answer_code="no",
        expected_chat_id=-100, expected_question_version=version,
    )
    assert error is None and answered is not None
    assert not answered.keeps_turn
    assert game.phase == "whoami_ask"
    assert game.whoami_current_actor_user_id != first_actor
    assert len(game.whoami_history) == 1

    _, refused, error = await store.whoami_answer_question(
        game_id=game.game_id, responder_user_id=other, answer_code="no",
        expected_chat_id=-100, expected_question_version=version,
    )
    assert refused is None and error is not None
    assert len(game.whoami_history) == 1

    finish_order = []
    while game.status == "started":
        actor = game.whoami_current_actor_user_id
        assert actor in game.players and actor not in finish_order
        identity = game.roles[actor]
        finished, guessed, error = await store.whoami_guess_identity(
            game_id=game.game_id, actor_user_id=actor, guess_text=identity,
        )
        assert error is None and guessed is not None and finished is game
        assert guessed.guessed_correctly
        finish_order.append(actor)
        assert len(finish_order) <= len(game.players)

    assert game.status == "finished" and game.phase == "finished"
    assert game.whoami_current_actor_user_id is None
    assert list(game.whoami_finish_order) == finish_order
    assert game.whoami_winner_user_id == finish_order[0]
    assert "Все карточки разгаданы" in (game.winner_text or "")
    assert len(game.whoami_solved_user_ids) == len(game.players)

    _, too_late, error = await store.whoami_guess_identity(
        game_id=game.game_id, actor_user_id=first_actor, guess_text=game.roles[first_actor],
    )
    assert too_late is None and "нельзя" in error
