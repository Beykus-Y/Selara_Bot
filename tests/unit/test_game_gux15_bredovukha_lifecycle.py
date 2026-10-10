"""GUX-15: run every Bredovukha phase and round against the real GameStore."""
from __future__ import annotations

import pytest

from selara.presentation.game_state import GameStore


@pytest.mark.asyncio
async def test_bredovukha_full_three_player_game_and_round_guards() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bredovukha", chat_id=-100, chat_title="group",
        owner_user_id=1, owner_label="u1", reveal_eliminated_role=False,
    )
    assert error is None and game is not None
    for uid in (2, 3):
        _, outcome = await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
        assert outcome == "joined"
    started, error = await store.start(game_id=game.game_id)
    assert error is None and started is game
    assert game.phase == "category_pick" and game.bred_rounds >= 3

    rounds_seen = 0
    while game.status == "started":
        rounds_seen += 1
        assert rounds_seen <= game.bred_rounds
        assert game.phase == "category_pick"
        round_no = game.round_no
        selector = game.bred_current_selector_user_id
        assert selector in game.players
        assert game.bred_category_options

        _, unused, error = await store.bred_choose_category(
            game_id=game.game_id, actor_user_id=selector, option_index=0,
            expected_round_no=round_no, expected_chat_id=-200,
        )
        assert unused is None and error == "Эта кнопка из другого чата"
        assert game.phase == "category_pick"

        selected, category, error = await store.bred_choose_category(
            game_id=game.game_id, actor_user_id=selector, option_index=0,
            expected_round_no=round_no, expected_chat_id=-100,
        )
        assert error is None and selected is game and category is not None
        assert game.phase == "private_answers" and game.bred_correct_answer

        _, blocked = await store.bred_open_vote(game_id=game.game_id, force=False)
        assert blocked == "Ещё не все игроки прислали ответы"

        for uid in sorted(game.players):
            _, submission, error = await store.bred_submit_lie(
                game_id=game.game_id, user_id=uid,
                lie_text=f"вымышленный ответ игрока {uid} для раунда {round_no}",
            )
            assert error is None and submission is not None
            assert submission.vote_opened == (uid == max(game.players))

        assert game.phase == "public_vote"
        assert len(game.bred_options) == len(game.players) + 1
        correct = game.bred_option_owner_user_ids.index(None)
        for uid in sorted(game.players):
            _, invalid, error = await store.bred_register_vote(
                game_id=game.game_id, voter_user_id=uid, option_index=correct,
                expected_round_no=round_no, expected_chat_id=-200,
            )
            assert invalid is None and error == "Эта кнопка из другого чата"
            _, vote, error = await store.bred_register_vote(
                game_id=game.game_id, voter_user_id=uid, option_index=correct,
                expected_round_no=round_no, expected_chat_id=-100,
            )
            assert error is None and vote is not None
            assert vote.all_voted == (uid == max(game.players))

        _, resolution, error = await store.bred_resolve_round(
            game_id=game.game_id, force=False,
        )
        assert error is None and resolution is not None
        assert resolution.finished == (rounds_seen >= game.bred_rounds)
        assert game.bred_last_round_no == round_no
        assert game.bred_last_correct_option_index == correct
        assert all(score == 2 * rounds_seen for score in game.bred_scores.values())

        if game.status == "started":
            assert game.round_no == round_no + 1
            assert game.phase == "category_pick"
            _, stale, error = await store.bred_choose_category(
                game_id=game.game_id, actor_user_id=game.bred_current_selector_user_id,
                option_index=0, expected_round_no=round_no, expected_chat_id=-100,
            )
            assert stale is None and "предыдущего раунда" in error

    assert game.phase == "finished"
    assert rounds_seen == game.bred_rounds
    assert game.winner_text
    _, result, error = await store.bred_resolve_round(game_id=game.game_id, force=True)
    assert result is None and "не этап голосования" in error
