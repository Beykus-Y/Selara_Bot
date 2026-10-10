"""GUX-15: real GameStore lifecycles for simple games, without Telegram transport.

Start from a lobby (not fabricated mutable state), exercise cross-chat and stale
callbacks, advance all real phases, and assert terminal cleanup and rematch viability.
Live Telegram and third-party provider smoke are tracked separately in #194.
"""
from __future__ import annotations

import pytest

from selara.presentation.game_state import GameStore


async def _start_game(store: GameStore, *, kind: str, players: int, chat_id: int = -100):
    game, error = await store.create_lobby(
        kind=kind,
        chat_id=chat_id,
        chat_title="Test group",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None and game is not None
    for uid in range(2, players + 1):
        _, status = await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
        assert status == "joined"
    started, error = await store.start(game_id=game.game_id)
    assert error is None and started is not None
    assert started.status == "started"
    return started


@pytest.mark.asyncio
async def test_dice_two_players_finish_then_chat_accepts_new_lobby(monkeypatch) -> None:
    store = GameStore()
    game = await _start_game(store, kind="dice", players=2)
    assert game.phase == "freeplay"

    throws = iter((6, 2))
    monkeypatch.setattr("selara.presentation.game_state.random.randint", lambda lo, hi: next(throws))
    _, rejected, error = await store.dice_register_roll(
        game_id=game.game_id, user_id=1, expected_chat_id=-200,
    )
    assert rejected is None and error == "Эта кнопка из другого чата"
    assert game.dice_scores == {}

    current, first, error = await store.dice_register_roll(
        game_id=game.game_id, user_id=1, expected_chat_id=-100,
    )
    assert current is game and error is None and first is not None
    assert first.roll_value == 6 and not first.finished
    _, repeat, error = await store.dice_register_roll(game_id=game.game_id, user_id=1)
    assert repeat is None and "уже бросили" in error

    final, second, error = await store.dice_register_roll(
        game_id=game.game_id, user_id=2, expected_chat_id=-100,
    )
    assert error is None and second is not None and final is game
    assert second.roll_value == 2 and second.finished
    assert final.status == "finished" and final.phase == "finished"
    assert "u1" in (final.winner_text or "")

    _, duplicate, error = await store.dice_register_roll(game_id=game.game_id, user_id=2)
    assert duplicate is None and "завершена" in error
    fresh, error = await store.create_lobby(
        kind="dice", chat_id=-100, chat_title="Test group",
        owner_user_id=1, owner_label="u1", reveal_eliminated_role=True,
    )
    assert error is None and fresh is not None and fresh.game_id != game.game_id


@pytest.mark.asyncio
async def test_spy_votes_finish_with_actual_spy_and_reject_foreign_chat() -> None:
    store = GameStore()
    game = await _start_game(store, kind="spy", players=3)
    assert game.phase == "freeplay"
    spy = next(uid for uid, role in game.roles.items() if role == "Шпион")
    civilians = [uid for uid in game.players if uid != spy]
    assert len(civilians) == 2

    _, result, _, error = await store.spy_register_vote(
        game_id=game.game_id, voter_user_id=civilians[0],
        target_user_id=spy, expected_chat_id=-200,
    )
    assert result is None and error == "Эта кнопка из другого чата"
    assert game.spy_votes == {}

    _, first, _, error = await store.spy_register_vote(
        game_id=game.game_id, voter_user_id=civilians[0],
        target_user_id=spy, expected_chat_id=-100,
    )
    assert error is None and first is None and game.phase == "freeplay"
    finished, resolution, _, error = await store.spy_register_vote(
        game_id=game.game_id, voter_user_id=civilians[1],
        target_user_id=spy, expected_chat_id=-100,
    )
    assert error is None and resolution is not None and finished is game
    assert resolution.candidate_user_id == spy and resolution.candidate_is_spy is True
    assert game.status == "finished" and game.phase == "finished"
    assert "Победа мирных" in (game.winner_text or "")

    _, stale, _, error = await store.spy_register_vote(
        game_id=game.game_id, voter_user_id=spy, target_user_id=civilians[0],
    )
    assert stale is None and "завершена" in error


@pytest.mark.asyncio
async def test_quiz_every_round_correctness_and_stale_question_guards() -> None:
    store = GameStore()
    game = await _start_game(store, kind="quiz", players=2)
    assert game.phase == "freeplay"
    questions = tuple(game.quiz_questions)
    assert questions and game.quiz_current_question_index == 0

    for index, question in enumerate(questions):
        assert game.quiz_current_question_index == index
        assert game.round_no == index + 1
        answer = question.answer_index
        wrong = next(choice for choice in range(len(question.options)) if choice != answer)

        _, refused, error = await store.quiz_submit_answer(
            game_id=game.game_id, user_id=1, option_index=answer,
            expected_chat_id=-200, expected_question_index=index,
        )
        assert refused is None and error == "Эта кнопка из другого чата"
        assert game.quiz_answers == {}

        _, refused, error = await store.quiz_submit_answer(
            game_id=game.game_id, user_id=1, option_index=answer,
            expected_chat_id=-100, expected_question_index=index - 1,
        )
        assert refused is None and "вопрос уже закрыт" in error
        assert game.quiz_answers == {}

        _, one, error = await store.quiz_submit_answer(
            game_id=game.game_id, user_id=1, option_index=answer,
            expected_chat_id=-100, expected_question_index=index,
        )
        assert error is None and one is not None and not one.all_answered

        _, unresolved, error = await store.quiz_resolve_round(
            game_id=game.game_id, force=False,
        )
        assert unresolved is None and "не все участники" in error

        _, two, error = await store.quiz_submit_answer(
            game_id=game.game_id, user_id=2, option_index=wrong,
            expected_chat_id=-100, expected_question_index=index,
        )
        assert error is None and two is not None and two.all_answered
        _, resolution, error = await store.quiz_resolve_round(
            game_id=game.game_id, force=False,
        )
        assert error is None and resolution is not None
        assert resolution.correct_players == (1,)
        assert resolution.question_index == index
        assert resolution.finished == (index == len(questions) - 1)

    assert game.status == "finished" and game.phase == "finished"
    assert game.quiz_scores[1] == len(questions)
    assert game.quiz_scores[2] == 0
    assert "u1" in (game.winner_text or "")
    _, result, error = await store.quiz_resolve_round(game_id=game.game_id, force=True)
    assert result is None and "активной фазе" in error
