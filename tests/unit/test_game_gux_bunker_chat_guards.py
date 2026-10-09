"""Regression coverage for GUX-10 Bunker cross-chat mutation guards."""
from __future__ import annotations

import pytest

from selara.presentation.game_state import GameStore, GroupGame


@pytest.mark.asyncio
async def test_bunker_vote_checks_chat_under_per_game_lock() -> None:
    store = GameStore()
    game = GroupGame(
        game_id="bunker-chat", kind="bunker", chat_id=-100, chat_title="room",
        owner_user_id=1, players={1: "Alice", 2: "Bob", 3: "Cara"},
        status="started", phase="bunker_vote", round_no=2,
        alive_player_ids={1, 2, 3},
    )
    store._by_id[game.game_id] = game

    _, previous, error = await store.bunker_register_vote(
        game_id=game.game_id, voter_user_id=1, target_user_id=2,
        expected_round_no=2, expected_chat_id=-200,
    )
    assert previous is None and error == "Эта кнопка из другого чата"
    assert not game.bunker_votes

    _, previous, error = await store.bunker_register_vote(
        game_id=game.game_id, voter_user_id=1, target_user_id=2,
        expected_round_no=2, expected_chat_id=-100,
    )
    assert error is None and previous is None
    assert game.bunker_votes == {1: 2}


@pytest.mark.asyncio
async def test_bunker_reveal_checks_chat_before_card_mutation() -> None:
    store = GameStore()
    game = GroupGame(
        game_id="bunker-reveal", kind="bunker", chat_id=-100, chat_title="room",
        owner_user_id=1, players={1: "Alice", 2: "Bob", 3: "Cara"},
        status="started", phase="bunker_reveal", round_no=3,
        alive_player_ids={1, 2, 3}, bunker_current_actor_user_id=1,
    )
    store._by_id[game.game_id] = game

    _, result, error = await store.bunker_register_reveal(
        game_id=game.game_id, actor_user_id=1, field_key="profession",
        expected_round_no=3, expected_chat_id=-200,
    )
    assert result is None and error == "Эта кнопка из другого чата"
    assert not game.bunker_revealed_fields

    _, result, error = await store.bunker_register_reveal(
        game_id=game.game_id, actor_user_id=1, field_key="profession",
        expected_round_no=2, expected_chat_id=-100,
    )
    assert result is None and "предыдущего раунда" in error
    assert not game.bunker_revealed_fields
