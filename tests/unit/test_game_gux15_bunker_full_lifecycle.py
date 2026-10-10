"""GUX-15: complete 6/8/12-player Bunker matches through the real GameStore.

Checks every reveal/vote phase, stale and foreign-chat mutation guards, and
terminal winners. Separate manual iOS/Android public-board evaluation remains #194.
"""
from __future__ import annotations

import pytest

from selara.presentation.game_state import BUNKER_CARD_FIELDS, GameStore


@pytest.mark.asyncio
@pytest.mark.parametrize(("player_count", "seats"), [(6, 2), (8, 2), (12, 5)])
async def test_bunker_players_reveal_vote_to_final_seats(player_count: int, seats: int) -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bunker", chat_id=-100, chat_title="group", owner_user_id=1,
        owner_label="u1", reveal_eliminated_role=True,
    )
    assert error is None and game is not None
    for uid in range(2, player_count + 1):
        _, status = await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
        assert status == "joined"
    started, error = await store.start(game_id=game.game_id)
    assert error is None and started is game
    assert game.status == "started" and game.phase == "bunker_reveal"
    assert len(game.bunker_cards) == player_count
    assert game.bunker_seats == seats

    eliminated = []
    while game.status == "started":
        assert game.phase in {"bunker_reveal", "bunker_vote"}
        current_round = game.round_no
        while game.phase == "bunker_reveal":
            actor = game.bunker_current_actor_user_id
            assert actor in game.alive_player_ids
            field_key = next(
                field for field in BUNKER_CARD_FIELDS
                if field not in game.bunker_revealed_fields[actor]
            )
            cursor = game.bunker_reveal_cursor

            _, rejected, error = await store.bunker_register_reveal(
                game_id=game.game_id, actor_user_id=actor, field_key=field_key,
                expected_round_no=current_round, expected_reveal_cursor=cursor,
                expected_chat_id=-200,
            )
            assert rejected is None and error == "Эта кнопка из другого чата"
            assert game.bunker_reveal_cursor == cursor
            assert field_key not in game.bunker_revealed_fields[actor]

            _, reveal, error = await store.bunker_register_reveal(
                game_id=game.game_id, actor_user_id=actor, field_key=field_key,
                expected_round_no=current_round, expected_reveal_cursor=cursor,
                expected_chat_id=-100,
            )
            assert error is None and reveal is not None
            assert field_key in game.bunker_revealed_fields[actor]
            assert reveal.revealed_value is not None

            if game.phase == "bunker_reveal":
                _, stale, error = await store.bunker_register_reveal(
                    game_id=game.game_id, actor_user_id=actor, field_key=field_key,
                    expected_round_no=current_round, expected_reveal_cursor=cursor,
                    expected_chat_id=-100,
                )
                assert stale is None and "ход уже завершён" in error

        assert game.phase == "bunker_vote"
        before = set(game.alive_player_ids)
        assert len(before) > game.bunker_seats
        target = max(before)
        fallback = min(before)

        _, unresolved, error = await store.bunker_resolve_vote(game_id=game.game_id, force=False)
        assert unresolved is None and "не все участники" in error

        for voter in sorted(before):
            chosen = target if voter != target else fallback
            _, previous, error = await store.bunker_register_vote(
                game_id=game.game_id, voter_user_id=voter, target_user_id=chosen,
                expected_round_no=current_round, expected_chat_id=-200,
            )
            assert previous is None and error == "Эта кнопка из другого чата"
            assert voter not in game.bunker_votes
            _, previous, error = await store.bunker_register_vote(
                game_id=game.game_id, voter_user_id=voter, target_user_id=chosen,
                expected_round_no=current_round, expected_chat_id=-100,
            )
            assert error is None and previous is None

        updated, resolution, error = await store.bunker_resolve_vote(
            game_id=game.game_id, force=False,
        )
        assert error is None and updated is game and resolution is not None
        assert not resolution.tie
        assert resolution.eliminated_user_id == target
        assert before - game.alive_player_ids == {target}
        eliminated.append(target)
        assert len(eliminated) <= player_count - game.bunker_seats

        if game.status == "started":
            assert game.round_no == current_round + 1
            _, stale, error = await store.bunker_register_vote(
                game_id=game.game_id, voter_user_id=min(game.alive_player_ids),
                target_user_id=max(game.alive_player_ids),
                expected_round_no=current_round, expected_chat_id=-100,
            )
            assert stale is None and "предыдущего раунда" in error

    assert len(eliminated) == player_count - game.bunker_seats
    assert len(game.alive_player_ids) == game.bunker_seats
    assert game.phase == "finished"
    assert game.winner_text and "В бункер попали" in game.winner_text
    assert all(f"u{uid}" in game.winner_text for uid in game.alive_player_ids)
    _, repeated, error = await store.bunker_resolve_vote(game_id=game.game_id, force=False)
    assert repeated is None and "не этап голосования" in error
