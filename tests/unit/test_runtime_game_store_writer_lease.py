"""Single-writer lease for the Redis game store (issue #87).

Every instance used to hydrate its own copy of a game and persist it with a
blind whole-object SET, so the last writer silently erased the other's action.
Only the lease holder may write game state now: a second instance refuses to
become a writer, and a process that lost the lease is fenced off. The Lua
scripts behind the lease are exercised against a real Redis in
tests/integration/test_game_state_writer_lease_redis.py.
"""

from __future__ import annotations

import asyncio

import pytest

import selara.presentation.game_state as game_state_module
from selara.presentation.game_state import (
    GameStateCodec,
    GameStore,
    GameWriterFencedError,
    GameWriterLeaseHeldError,
    GroupGame,
    RuntimeGameStore,
)


class _FakeRedisError(Exception):
    pass


class _SharedRedis:
    """One Redis shared by several processes, with the lease rules of the Lua scripts."""

    def __init__(self) -> None:
        self.payloads: dict[str, str] = {}
        self.active: dict[int, str] = {}
        self.owner: str | None = None
        self.epoch = 0
        self.claims = 0

    def repo(self, token: str) -> _FakeGameRepo:
        return _FakeGameRepo(self, token)

    def lapse(self) -> None:
        """The lease TTL ran out and nobody has claimed the lease since."""
        self.owner = None

    def take_over(self, token: str) -> None:
        """Another instance claimed the lease, bumping the epoch as the claim script does."""
        self.owner = token
        self.epoch += 1


class _FakeGameRepo:
    """Stands in for RedisGameStateRepository; every game write checks the lease first."""

    def __init__(self, shared: _SharedRedis, token: str) -> None:
        self._shared = shared
        self._token = token

    async def ping(self) -> None:
        return None

    async def claim_writer_lease(self, *, ttl_ms: int, known_epoch: int | None) -> tuple[int, int]:
        shared = self._shared
        shared.claims += 1
        counter = shared.epoch
        if shared.owner == self._token:
            return 1, counter
        if shared.owner is not None:
            return 0, counter
        if known_epoch is not None and known_epoch != counter:
            return 3, counter
        shared.owner = self._token
        shared.epoch += 1
        return 2, counter

    def _fence(self) -> None:
        if self._shared.owner != self._token:
            raise GameWriterFencedError("this process no longer holds the game writer lease")

    async def save_game(self, game: GroupGame, *, is_active: bool) -> None:
        self._fence()
        self._shared.payloads[game.game_id] = GameStateCodec().dumps(game)
        if is_active and game.status != "finished":
            self._shared.active[game.chat_id] = game.game_id
        elif self._shared.active.get(game.chat_id) == game.game_id:
            self._shared.active.pop(game.chat_id, None)

    async def set_active_game_id(self, *, chat_id: int, game_id: str | None) -> None:
        self._fence()
        if game_id is None:
            self._shared.active.pop(chat_id, None)
        else:
            self._shared.active[chat_id] = game_id

    async def load_game(self, game_id: str) -> GroupGame | None:
        raw = self._shared.payloads.get(game_id)
        return None if raw is None else GameStateCodec().loads(raw)

    async def load_active_game_id(self, chat_id: int) -> str | None:
        return self._shared.active.get(chat_id)

    async def close(self) -> None:
        return None


class _DownRepo(_FakeGameRepo):
    """Every Redis call fails the way an outage does."""

    async def claim_writer_lease(self, *, ttl_ms: int, known_epoch: int | None) -> tuple[int, int]:
        raise _FakeRedisError("redis unavailable")


class _RecordingBroker:
    def __init__(self) -> None:
        self.closed = False

    async def publish(self, event) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def _store_on(shared: _SharedRedis) -> RuntimeGameStore:
    store = RuntimeGameStore(backend=GameStore())
    token = store._writer_token
    store._state_repo = shared.repo(token)  # type: ignore[assignment]
    store._broker = _RecordingBroker()  # type: ignore[assignment]
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 0
    store._writer_start_timeout_seconds = 0
    store._writer_start_retry_seconds = 0.01
    store._writer_heartbeat_seconds = 3600
    store._build_redis_runtime = lambda: (shared.repo(token), _RecordingBroker())  # type: ignore[method-assign]
    return store


async def _create_game(store: RuntimeGameStore, *, chat_id: int) -> GroupGame:
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
    return game


@pytest.mark.asyncio
async def test_second_instance_is_refused_while_another_holds_the_writer_lease() -> None:
    shared = _SharedRedis()
    first = _store_on(shared)
    second = _store_on(shared)
    try:
        await first.start_writer_lease()

        with pytest.raises(GameWriterLeaseHeldError):
            await second.start_writer_lease()
        assert shared.owner == first._writer_token
    finally:
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_stale_writer_action_is_fenced_instead_of_overwriting_a_takeover() -> None:
    """The lost-update schedule of issue #87, with the lease making it impossible."""
    shared = _SharedRedis()
    first = _store_on(shared)
    second = _store_on(shared)
    try:
        await first.start_writer_lease()
        game = await _create_game(first, chat_id=500)

        # The first instance's lease lapses (its process stalled past the TTL) and
        # the second instance takes over and persists its own action on the game.
        shared.lapse()
        await second.start_writer_lease()
        _, joined = await second.join(game_id=game.game_id, user_id=2, user_label="u2")
        assert joined == "joined"

        # The first instance still has the game in memory without user 2 and acts
        # on that copy. The write is fenced: it must not land over the takeover.
        watcher = asyncio.create_task(first.watch_writer_lease())
        _, stale_join = await first.join(game_id=game.game_id, user_id=3, user_label="u3")
        assert stale_join == "joined"
        with pytest.raises(GameWriterLeaseHeldError):
            await asyncio.wait_for(watcher, timeout=5)

        persisted = await shared.repo("reader").load_game(game.game_id)
        assert persisted is not None
        assert set(persisted.players) == {1, 2}
        assert shared.owner == second._writer_token
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_lapsed_lease_is_reclaimed_when_no_other_instance_took_it() -> None:
    shared = _SharedRedis()
    store = _store_on(shared)
    store._recovery_retry_seconds = 3600  # the test drives the re-claim itself
    try:
        await store.start_writer_lease()
        game = await _create_game(store, chat_id=501)

        shared.lapse()
        _, joined = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
        assert joined == "joined"
        assert store.redis_recovery_state == "degraded"

        # Nobody else acquired the lease in between, so the epoch check passes and
        # the memory state (which includes user 2) is reconciled into Redis.
        await store._attempt_recovery()

        assert store.redis_recovery_state == "connected"
        assert shared.owner == store._writer_token
        persisted = await shared.repo("reader").load_game(game.game_id)
        assert persisted is not None
        assert set(persisted.players) == {1, 2}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recovery_refuses_to_reconcile_after_another_instance_acquired_the_lease() -> None:
    shared = _SharedRedis()
    store = _store_on(shared)
    store._recovery_retry_seconds = 3600
    try:
        await store.start_writer_lease()
        game = await _create_game(store, chat_id=502)

        # The lease lapsed, another instance acquired it and released it again. The
        # epoch moved, so this process's memory is not provably the newest state.
        shared.lapse()
        shared.take_over("other-instance")
        shared.lapse()
        _, joined = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
        assert joined == "joined"

        with pytest.raises(GameWriterLeaseHeldError):
            await store._attempt_recovery()
        assert store.redis_recovery_state == "degraded"
        assert shared.owner is None
        persisted = await shared.repo("reader").load_game(game.game_id)
        assert persisted is not None
        assert set(persisted.players) == {1}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_heartbeat_ends_the_process_when_another_instance_takes_the_lease() -> None:
    shared = _SharedRedis()
    store = _store_on(shared)
    store._writer_heartbeat_seconds = 0.01
    try:
        await store.start_writer_lease()

        shared.lapse()
        shared.take_over("other-instance")
        with pytest.raises(GameWriterLeaseHeldError):
            await asyncio.wait_for(store.watch_writer_lease(), timeout=5)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_clean_shutdown_keeps_the_lease_until_its_ttl_runs_out() -> None:
    shared = _SharedRedis()
    first = _store_on(shared)
    successor = _store_on(shared)
    successor._writer_start_timeout_seconds = 5
    try:
        await first.start_writer_lease()
        await first.close()
        # Writes still in flight at shutdown must land, so the lease is not released early.
        assert shared.owner == first._writer_token

        starting = asyncio.create_task(successor.start_writer_lease())
        await asyncio.sleep(0.05)
        assert not starting.done()

        shared.lapse()
        await asyncio.wait_for(starting, timeout=5)
        assert shared.owner == successor._writer_token
    finally:
        await successor.close()


@pytest.mark.asyncio
async def test_startup_waits_for_a_crashed_predecessors_lease_to_expire() -> None:
    shared = _SharedRedis()
    crashed = _store_on(shared)
    successor = _store_on(shared)
    successor._writer_start_timeout_seconds = 5
    try:
        await crashed.start_writer_lease()  # never closed: the process crashed
        starting = asyncio.create_task(successor.start_writer_lease())
        await asyncio.sleep(0.05)
        assert not starting.done()

        shared.lapse()
        await asyncio.wait_for(starting, timeout=5)
        assert shared.owner == successor._writer_token
    finally:
        await successor.close()
        await crashed.close()


@pytest.mark.asyncio
async def test_redis_outage_at_startup_degrades_and_claims_once_redis_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    shared = _SharedRedis()
    store = _store_on(shared)
    store._state_repo = _DownRepo(shared, store._writer_token)  # type: ignore[assignment]
    store._recovery_retry_seconds = 3600
    try:
        await store.start_writer_lease()
        assert store.redis_recovery_state == "degraded"

        await store._attempt_recovery()

        assert store.redis_recovery_state == "connected"
        assert shared.owner == store._writer_token
    finally:
        await store.close()


class _HangingRepo(_FakeGameRepo):
    """Redis accepts the call and never answers the lease claim."""

    async def claim_writer_lease(self, *, ttl_ms: int, known_epoch: int | None) -> tuple[int, int]:
        await asyncio.Event().wait()
        raise AssertionError("the claim is cancelled by its timeout")


@pytest.mark.asyncio
async def test_recovery_keeps_renewing_the_lease_while_it_reconciles() -> None:
    shared = _SharedRedis()
    store = _store_on(shared)
    store._writer_heartbeat_seconds = 0.01
    store._recovery_retry_seconds = 3600
    try:
        await store.start_writer_lease()
        game = await _create_game(store, chat_id=503)
        shared.lapse()
        _, joined = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
        assert joined == "joined"
        assert store.redis_recovery_state == "degraded"

        renewals_during_reconcile: list[int] = []

        async def slow_reconcile(repo) -> int:
            # Longer than several heartbeats: the lease must be renewed under the pass.
            before = shared.claims
            await asyncio.sleep(0.1)
            renewals_during_reconcile.append(shared.claims - before)
            return 1

        store._reconcile_degraded_state = slow_reconcile  # type: ignore[method-assign]
        await store._attempt_recovery()

        assert store.redis_recovery_state == "connected"
        assert renewals_during_reconcile[0] >= 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_hung_lease_call_degrades_startup_instead_of_blocking_it() -> None:
    shared = _SharedRedis()
    store = _store_on(shared)
    store._state_repo = _HangingRepo(shared, store._writer_token)  # type: ignore[assignment]
    store._writer_claim_timeout_seconds = 0.01
    store._recovery_retry_seconds = 3600
    try:
        await asyncio.wait_for(store.start_writer_lease(), timeout=5)

        assert store.redis_recovery_state == "degraded"
        assert shared.owner is None
    finally:
        await store.close()
