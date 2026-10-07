"""Write scope and hot-memory bounds of RuntimeGameStore (issue #66).

A mutation must persist only the games it changed, finished games must not be
rewritten by other games' activity, and finished games must leave hot memory
after their retention window and come back from Redis on demand.
"""

from __future__ import annotations

import time

import pytest

from selara.presentation.game_state import GameStateCodec, GameStore, GroupGame, RuntimeGameStore


class _CountingRepo:
    """Records every payload write; stands in for RedisGameStateRepository."""

    def __init__(self) -> None:
        self.payloads: dict[str, str] = {}
        self.saved: list[str] = []

    async def save_game(self, game: GroupGame, *, is_active: bool) -> None:
        self.saved.append(game.game_id)
        self.payloads[game.game_id] = GameStateCodec().dumps(game)

    async def load_game(self, game_id: str) -> GroupGame | None:
        raw = self.payloads.get(game_id)
        return None if raw is None else GameStateCodec().loads(raw)

    async def load_active_game_id(self, chat_id: int) -> str | None:
        return None

    async def close(self) -> None:
        return None


def _runtime_store() -> tuple[RuntimeGameStore, _CountingRepo]:
    store = RuntimeGameStore(backend=GameStore())
    repo = _CountingRepo()
    store._state_repo = repo  # type: ignore[assignment]
    # Keep the periodic active-TTL renewal out of the way unless a test asks for it.
    store._next_active_refresh_at = time.monotonic() + 3600
    return store, repo


async def _started_dice_game(store: RuntimeGameStore, *, chat_id: int) -> GroupGame:
    game, error = await store.create_lobby(
        kind="dice",
        chat_id=chat_id,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    _, join_error = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    assert join_error == "joined"
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started is not None
    return started


@pytest.mark.asyncio
async def test_mutation_persists_only_the_game_it_changed() -> None:
    store, repo = _runtime_store()
    games = [await _started_dice_game(store, chat_id=100 + index) for index in range(30)]
    repo.saved.clear()

    await store.set_message_id(game_id=games[0].game_id, message_id=77)

    assert repo.saved == [games[0].game_id]


@pytest.mark.asyncio
async def test_finished_game_is_not_rewritten_by_other_games_activity() -> None:
    store, repo = _runtime_store()
    finished = await _started_dice_game(store, chat_id=100)
    await store.finish(game_id=finished.game_id, winner_text="done")
    other = await _started_dice_game(store, chat_id=200)
    repo.saved.clear()

    for message_id in range(5):
        await store.set_message_id(game_id=other.game_id, message_id=message_id)

    assert finished.game_id not in repo.saved
    assert repo.saved == [other.game_id] * 5


@pytest.mark.asyncio
async def test_finished_game_leaves_hot_memory_and_reloads_from_redis() -> None:
    store, _repo = _runtime_store()
    store._finished_game_hot_seconds = 0
    game = await _started_dice_game(store, chat_id=100)

    await store.finish(game_id=game.game_id, winner_text="done")

    assert game.game_id not in store._backend._by_id
    assert game.game_id not in store._game_revisions
    reloaded = await store.get_game(game_id=game.game_id)
    assert reloaded is not None
    assert reloaded.status == "finished"
    assert reloaded.winner_text == "done"


@pytest.mark.asyncio
async def test_idle_active_games_are_renewed_once_per_refresh_interval() -> None:
    store, repo = _runtime_store()
    idle = await _started_dice_game(store, chat_id=100)
    busy = await _started_dice_game(store, chat_id=200)
    repo.saved.clear()
    store._next_active_refresh_at = 0.0

    await store.set_message_id(game_id=busy.game_id, message_id=1)
    assert sorted(repo.saved) == sorted([idle.game_id, busy.game_id])

    repo.saved.clear()
    await store.set_message_id(game_id=busy.game_id, message_id=2)
    assert repo.saved == [busy.game_id]


@pytest.mark.asyncio
async def test_economy_rewards_flag_survives_eviction() -> None:
    store, _repo = _runtime_store()
    store._finished_game_hot_seconds = 0
    game = await _started_dice_game(store, chat_id=100)
    await store.finish(game_id=game.game_id, winner_text="done")

    await store.mark_economy_rewards_granted(game_id=game.game_id)

    reloaded = await store.get_game(game_id=game.game_id)
    assert reloaded is not None
    assert reloaded.economy_rewards_granted is True


@pytest.mark.asyncio
async def test_chat_migration_persists_the_moved_game() -> None:
    store, repo = _runtime_store()
    game = await _started_dice_game(store, chat_id=100)
    repo.saved.clear()

    migrated = await store.migrate_chat_id(old_chat_id=100, new_chat_id=-200)

    assert migrated == 1
    assert repo.saved == [game.game_id]
    assert GameStateCodec().loads(repo.payloads[game.game_id]).chat_id == -200
