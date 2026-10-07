"""Real-Redis checks of the game store's single-writer lease and fenced writes (issue #87).

The unit suite drives RuntimeGameStore against a fake that copies the lease
rules; these tests run the Lua scripts themselves.
"""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from selara.presentation.game_state import (
    GameStateCodec,
    GameStore,
    GameWriterFencedError,
    RedisGameStateRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _repo(client: Redis, token: str) -> RedisGameStateRepository:
    return RedisGameStateRepository(
        client=client,
        codec=GameStateCodec(),
        ttl=timedelta(hours=1),
        writer_token=token,
    )


async def test_writer_lease_fences_stale_owners_on_real_redis():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    lease_keys = [RedisGameStateRepository._WRITER_LEASE_KEY, RedisGameStateRepository._WRITER_EPOCH_KEY]
    game, error = await GameStore().create_lobby(
        kind="dice",
        chat_id=-1001234500,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    async with Redis.from_url(url, decode_responses=True) as client:
        await client.delete(*lease_keys)
        owner = _repo(client, f"owner-{uuid4().hex}")
        rival = _repo(client, f"rival-{uuid4().hex}")
        try:
            assert await owner.claim_writer_lease(ttl_ms=30_000, known_epoch=None) == (2, 0)
            assert await rival.claim_writer_lease(ttl_ms=30_000, known_epoch=None) == (0, 1)

            await owner.save_game(game, is_active=True)
            assert await owner.load_active_game_id(game.chat_id) == game.game_id
            with pytest.raises(GameWriterFencedError):
                await rival.save_game(game, is_active=True)
            with pytest.raises(GameWriterFencedError):
                await rival.set_active_game_id(chat_id=game.chat_id, game_id=None)
            assert await owner.load_active_game_id(game.chat_id) == game.game_id

            # A finished game clears its chat pointer and joins each player's recent games.
            game.status = "finished"
            await owner.save_game(game, is_active=False)
            assert await owner.load_active_game_id(game.chat_id) is None
            assert await client.zscore(owner._recent_key(1), game.game_id) == float(
                int((game.started_at or game.created_at).timestamp())
            )

            # The owner's TTL runs out (the lease key expires), then the rival takes the free lease.
            await client.delete(RedisGameStateRepository._WRITER_LEASE_KEY)
            assert await rival.claim_writer_lease(ttl_ms=30_000, known_epoch=None) == (2, 1)
            await client.delete(RedisGameStateRepository._WRITER_LEASE_KEY)

            # The owner last held the lease at epoch 1; an acquisition since then
            # must not be taken over silently, and the lease stays free for it.
            assert await owner.claim_writer_lease(ttl_ms=30_000, known_epoch=1) == (3, 2)
            assert await client.get(RedisGameStateRepository._WRITER_LEASE_KEY) is None
            assert await owner.claim_writer_lease(ttl_ms=30_000, known_epoch=2) == (2, 2)
            assert await owner.claim_writer_lease(ttl_ms=30_000, known_epoch=3) == (1, 3)

            await owner.set_active_game_id(chat_id=game.chat_id, game_id=game.game_id)
            assert await owner.load_active_game_id(game.chat_id) == game.game_id
            await owner.set_active_game_id(chat_id=game.chat_id, game_id=None)
            assert await owner.load_active_game_id(game.chat_id) is None
        finally:
            await client.delete(
                *lease_keys,
                owner._game_key(game.game_id),
                owner._active_key(game.chat_id),
                owner._recent_key(1),
            )
