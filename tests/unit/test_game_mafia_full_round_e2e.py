"""GUX-15 automation: one complete Mafia round through GameStore, no Telegram.

Walks the successful path that the manual smoke was meant to cover:
lobby -> start -> every night action -> night resolution -> day discussion ->
day vote -> execution confirmation -> resolution. Asserts only the public
contract: the phase sequence, that the confirmed candidate leaves the alive set,
and that the game either advanced to a new night or finished.

Six players, not four: at four, the game ends on the first execution and the
next-night branch can never run. At six, the final assertion accepts either outcome:
a Mafia win by parity, or a live game that moves to the next night.
"""
from __future__ import annotations

import pytest

from selara.presentation.game_state import GameStore


async def _started_mafia(store: GameStore):
    game, error = await store.create_lobby(
        kind="mafia",
        chat_id=700,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None and game is not None
    for user_id in (2, 3, 4, 5, 6):
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None and status == "joined"
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None and started is not None
    assert started.phase == "night"
    return started


async def _finish_night(store: GameStore, game_id: str) -> None:
    for _ in range(6):
        current, ready, error = await store.mafia_is_night_ready(game_id=game_id)
        assert error is None and current is not None
        if ready:
            return
        for actor_user_id in sorted(current.alive_player_ids):
            targets = store._mafia_night_action_targets(current, actor_user_id=actor_user_id)
            if not targets:
                continue
            _, action_error = await store.mafia_register_night_action(
                game_id=game_id,
                actor_user_id=actor_user_id,
                target_user_id=targets[0],
            )
            assert action_error is None
    pytest.fail("night never became ready")


@pytest.mark.asyncio
async def test_full_mafia_round_from_night_to_next_phase() -> None:
    store = GameStore()
    game = await _started_mafia(store)
    round_before = game.round_no

    await _finish_night(store, game.game_id)
    night_done, _, error = await store.mafia_resolve_night(game_id=game.game_id)
    assert error is None and night_done is not None
    assert night_done.phase == "day_discussion"

    day_vote, error = await store.mafia_open_day_vote(game_id=game.game_id)
    assert error is None and day_vote is not None
    assert day_vote.phase == "day_vote"

    alive = sorted(day_vote.alive_player_ids)
    candidate = alive[-1]
    fallback_target = alive[0]
    for voter in alive:
        target = candidate if voter != candidate else fallback_target
        _, _, error = await store.mafia_register_day_vote(
            game_id=game.game_id, voter_user_id=voter, target_user_id=target
        )
        assert error is None

    _, voted, alive_count = await store.mafia_get_vote_snapshot(game_id=game.game_id)
    assert voted == alive_count

    voted_game, resolution, error = await store.mafia_resolve_day_vote(game_id=game.game_id)
    assert error is None and resolution is not None
    assert resolution.opened_execution_confirm is True
    assert voted_game is not None and voted_game.phase == "day_execution_confirm"

    confirm_voters = sorted(voted_game.alive_player_ids)
    for voter in confirm_voters:
        _, _, error = await store.mafia_register_execution_confirm_vote(
            game_id=game.game_id, voter_user_id=voter, approve=True
        )
        assert error is None

    final_game, confirm, error = await store.mafia_resolve_execution_confirm(game_id=game.game_id)
    assert error is None and confirm is not None and final_game is not None
    assert confirm.passed is True
    assert confirm.executed_user_id == candidate
    assert candidate not in final_game.alive_player_ids

    # Either the game ended on this execution, or the next night opened with a new round.
    assert final_game.status == "finished" or (
        final_game.phase == "night" and final_game.round_no > round_before
    )
