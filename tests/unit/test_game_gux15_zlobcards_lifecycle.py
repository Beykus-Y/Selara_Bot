"""GUX-15: Zlobcards lobby→private choices→public votes→winner lifecycle."""
from __future__ import annotations

import pytest

from selara.presentation.game_state import GameStore


@pytest.mark.asyncio
async def test_zlobcards_every_round_to_finish_and_no_self_voting() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="zlobcards", chat_id=-100, chat_title="group",
        owner_user_id=1, owner_label="u1", reveal_eliminated_role=False,
        actions_18_enabled=False,
    )
    assert error is None and game is not None
    for uid in (2, 3):
        _, state = await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
        assert state == "joined"
    started, error = await store.start(game_id=game.game_id, actions_18_enabled=False)
    assert error is None and started is game
    assert game.status == "started" and game.phase == "private_answers"
    assert all(game.zlob_hands[uid] for uid in game.players)

    rounds_completed = 0
    while game.status == "started":
        rounds_completed += 1
        assert rounds_completed <= game.zlob_rounds
        round_no = game.round_no
        assert game.phase == "private_answers"
        assert game.zlob_black_text is not None
        slots = game.zlob_black_slots
        assert slots in (1, 2)

        _, blocked = await store.zlob_open_vote(game_id=game.game_id, force=False)
        assert blocked == "Ещё не все игроки прислали карточки"
        for uid in sorted(game.players):
            indexes = tuple(range(slots))
            assert len(game.zlob_hands[uid]) >= slots
            _, refused, error = await store.zlob_submit_cards(
                game_id=game.game_id, user_id=uid, card_indexes=indexes,
                expected_round_no=round_no, expected_chat_id=-200,
            )
            assert refused is None and error == "Эта кнопка из другого чата"
            assert uid not in game.zlob_submissions

            _, submitted, error = await store.zlob_submit_cards(
                game_id=game.game_id, user_id=uid, card_indexes=indexes,
                expected_round_no=round_no, expected_chat_id=-100,
            )
            assert error is None and submitted is not None
            assert submitted.vote_opened == (uid == max(game.players))

        assert game.phase == "public_vote"
        assert len(game.zlob_options) == len(game.players)
        assert len(game.zlob_option_owner_user_ids) == len(game.zlob_options)
        owner = game.zlob_option_owner_user_ids[0]
        assert owner in game.players

        _, invalid, error = await store.zlob_register_vote(
            game_id=game.game_id, voter_user_id=owner, option_index=0,
            expected_round_no=round_no, expected_chat_id=-100,
        )
        assert invalid is None and "Нельзя голосовать за свою карточку" in error
        assert game.zlob_votes == {}

        for uid in sorted(game.players):
            option_index = next(
                idx for idx, card_owner in enumerate(game.zlob_option_owner_user_ids)
                if card_owner != uid
            )
            _, vote, error = await store.zlob_register_vote(
                game_id=game.game_id, voter_user_id=uid,
                option_index=option_index, expected_round_no=round_no,
                expected_chat_id=-100,
            )
            assert error is None and vote is not None
            assert vote.all_voted == (uid == max(game.players))

        _, resolved, error = await store.zlob_resolve_round(game_id=game.game_id, force=False)
        assert error is None and resolved is not None
        assert resolved.round_no == round_no
        assert game.zlob_last_round_no == round_no
        assert sum(game.zlob_last_vote_tally) == len(game.players)
        assert resolved.finished == (game.status == "finished")

        if game.status == "started":
            assert game.round_no == round_no + 1
            assert game.phase == "private_answers"
            _, stale, error = await store.zlob_submit_cards(
                game_id=game.game_id, user_id=1, card_indexes=(0,),
                expected_round_no=round_no, expected_chat_id=-100,
            )
            assert stale is None and "предыдущего раунда" in error

    assert rounds_completed >= 1
    assert game.phase == "finished"
    assert game.winner_text
    _, result, error = await store.zlob_resolve_round(game_id=game.game_id, force=False)
    assert result is None and "не этап голосования" in error
