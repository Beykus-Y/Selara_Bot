"""Outage -> mutations -> Redis return -> restart recovery tests for RuntimeGameStore.

See issue #65: a Redis outage degrades the store to memory-only; when Redis
comes back the runtime must reconcile the diverged state so a restart can
never resurrect a stale phase or a game finished during the degradation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest

import selara.presentation.game_state as game_state_module
from selara.presentation.game_state import GameStore, GroupGame, RuntimeGameStore


class _FakeRedisError(Exception):
    pass


class _BrokenSaveRepo:
    """Repo that fails every write the way Redis does during an outage."""

    def __init__(self) -> None:
        self.closed = False

    async def save_game(self, game, *, is_active: bool) -> None:
        raise _FakeRedisError("redis unavailable")

    async def close(self) -> None:
        self.closed = True


class _RecordingBroker:
    def __init__(self) -> None:
        self.published: list[object] = []
        self.closed = False

    async def publish(self, event) -> None:
        self.published.append(event)

    async def close(self) -> None:
        self.closed = True


class _RecordingRepo:
    """Stand-in for RedisGameStateRepository that records reconciliation writes."""

    def __init__(
        self,
        *,
        payloads: dict[str, str] | None = None,
        active_pointers: dict[int, str] | None = None,
        fail_ping_until: int = 0,
        fail_save: bool = False,
        save_hook=None,
    ) -> None:
        self.payloads = dict(payloads or {})
        self.active_pointers = dict(active_pointers or {})
        self.saved: list[tuple[GroupGame, bool]] = []
        self.ping_calls = 0
        self.closed = False
        self._fail_ping_until = fail_ping_until
        self._fail_save = fail_save
        self._save_hook = save_hook

    async def ping(self) -> None:
        self.ping_calls += 1
        if self.ping_calls <= self._fail_ping_until:
            raise _FakeRedisError("redis still down")

    async def save_game(self, game, *, is_active: bool) -> None:
        if self._fail_save:
            raise _FakeRedisError("redis still down")
        self.saved.append((game, is_active))
        self.payloads[game.game_id] = game_state_module.GameStateCodec().dumps(game)
        # Mirror RedisGameStateRepository.save_game pointer handling so the
        # active-pointer assertions below are meaningful.
        if is_active and game.status != "finished":
            self.active_pointers[game.chat_id] = game.game_id
        elif self.active_pointers.get(game.chat_id) == game.game_id:
            self.active_pointers.pop(game.chat_id, None)
        if self._save_hook is not None:
            await self._save_hook(len(self.saved))

    async def set_active_game_id(self, *, chat_id: int, game_id: str | None) -> None:
        if self._fail_save:
            raise _FakeRedisError("redis still down")
        if game_id is None:
            self.active_pointers.pop(chat_id, None)
        else:
            self.active_pointers[chat_id] = game_id

    async def load_game(self, game_id: str):
        raw = self.payloads.get(game_id)
        if raw is None:
            return None
        return game_state_module.GameStateCodec().loads(raw)

    async def load_active_game_id(self, chat_id: int):
        return self.active_pointers.get(chat_id)

    async def close(self) -> None:
        self.closed = True


async def _make_started_game(store: RuntimeGameStore, *, chat_id: int = 100) -> GroupGame:
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


def _saved_by_id(repo: _RecordingRepo) -> dict[str, tuple[GroupGame, bool]]:
    return {saved_game.game_id: (saved_game, is_active) for saved_game, is_active in repo.saved}


@pytest.mark.asyncio
async def test_recovery_after_outage_resyncs_memory_and_prevents_resurrection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store)
    assert store._state_repo is None
    assert store.redis_recovery_state == "degraded"

    # Redis still holds the pre-outage snapshot: the game started but was
    # never finished there.
    stale_payload = game_state_module.GameStateCodec().dumps(game)

    # Players finish the game while the store is degraded (memory only).
    await store.finish(game_id=game.game_id, winner_text="u1 wins")

    repo = _RecordingRepo(
        payloads={game.game_id: stale_payload},
        active_pointers={game.chat_id: game.game_id},
    )
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    await store._attempt_recovery()

    assert store._state_repo is repo
    assert store._broker is broker
    assert store.redis_recovery_state == "connected"

    saved_game, is_active = _saved_by_id(repo)[game.game_id]
    assert saved_game.status == "finished"
    assert saved_game.winner_text == "u1 wins"
    assert is_active is False
    # The active pointer for the chat is cleared, so the finished game cannot
    # come back as active.
    assert repo.active_pointers.get(game.chat_id) is None

    # Restart simulation: a fresh runtime hydrates from the reconciled Redis
    # snapshot and must see the finished game, not the stale started copy.
    restarted = RuntimeGameStore(backend=GameStore())
    restarted._state_repo = repo  # type: ignore[assignment]
    await restarted._hydrate_active_for_chat(game.chat_id)
    active_after_restart = await restarted.backend.get_active_game_for_chat(chat_id=game.chat_id)
    assert active_after_restart is None
    await restarted._hydrate_game(game.game_id)
    restored = await restarted.backend.get_game(game.game_id)
    assert restored is not None
    assert restored.status == "finished"
    assert restored.winner_text == "u1 wins"

    await store.close()
    await restarted.close()


@pytest.mark.asyncio
async def test_recovery_repoints_stale_active_key_to_memory_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store, chat_id=200)

    # Redis active key still references a stale/foreign game the process does
    # not know; the in-memory view wins deterministically.
    repo = _RecordingRepo(active_pointers={game.chat_id: "stalegame1"})
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    await store._attempt_recovery()

    assert repo.active_pointers[game.chat_id] == game.game_id
    saved_game, is_active = _saved_by_id(repo)[game.game_id]
    assert saved_game.status == "started"
    assert is_active is True

    await store.close()


@pytest.mark.asyncio
async def test_stale_redis_snapshot_never_overwrites_newer_memory_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store)
    stale_payload = game_state_module.GameStateCodec().dumps(game)
    await store.finish(game_id=game.game_id, winner_text="done")

    repo = _RecordingRepo(payloads={game.game_id: stale_payload})
    store._state_repo = repo  # type: ignore[assignment]

    await store._hydrate_game(game.game_id)

    current = await store.backend.get_game(game.game_id)
    assert current is game
    assert current.status == "finished"

    await store.close()


@pytest.mark.asyncio
async def test_recovery_stays_degraded_when_redis_still_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store)
    assert store.redis_recovery_state == "degraded"

    # Ping still fails: nothing is switched over and the candidate is closed.
    repo = _RecordingRepo(fail_ping_until=999)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    with pytest.raises(_FakeRedisError):
        await store._attempt_recovery()

    assert store._state_repo is None
    assert store._broker is None
    assert store.redis_recovery_state == "degraded"
    assert repo.saved == []
    assert repo.closed is True

    # Redis answers the ping but writes fail: fail closed as well.
    repo_failing_writes = _RecordingRepo(fail_save=True)
    store._build_redis_runtime = lambda: (repo_failing_writes, broker)  # type: ignore[method-assign]

    with pytest.raises(_FakeRedisError):
        await store._attempt_recovery()

    assert store._state_repo is None
    assert store.redis_recovery_state == "degraded"
    assert repo_failing_writes.closed is True

    await store.close()
    _ = game


class _GatedFailingRepo(_RecordingRepo):
    """First ping blocks on a gate and then fails; later pings succeed."""

    def __init__(self) -> None:
        super().__init__(fail_ping_until=1)
        self.gate = asyncio.Event()

    async def ping(self) -> None:
        self.ping_calls += 1
        if self.ping_calls <= self._fail_ping_until:
            await self.gate.wait()
            raise _FakeRedisError("redis still down")


@pytest.mark.asyncio
async def test_recovery_loop_reconnects_once_redis_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 0.01
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    repo = _GatedFailingRepo()
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    game = await _make_started_game(store)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while store.redis_recovery_state != "recovering":
        assert loop.time() < deadline, "recovery loop did not start probing Redis"
        await asyncio.sleep(0.01)

    assert store._state_repo is None
    assert store._recovery_task is not None

    repo.gate.set()

    deadline = loop.time() + 5
    while store.redis_recovery_state != "connected":
        assert loop.time() < deadline, "recovery loop did not reconnect in time"
        await asyncio.sleep(0.01)

    assert store._state_repo is repo
    assert store._broker is broker
    assert repo.ping_calls >= 2
    saved_game, _ = _saved_by_id(repo)[game.game_id]
    assert saved_game.status == "started"

    deadline = loop.time() + 5
    while store._recovery_task is not None:
        assert loop.time() < deadline, "recovery loop did not stop after recovery"
        await asyncio.sleep(0.01)

    await store.close()


@pytest.mark.asyncio
async def test_degrade_schedules_recovery_only_when_redis_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)

    memory_only = RuntimeGameStore(backend=GameStore())
    memory_only._recovery_retry_seconds = 3600
    memory_only._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]
    _, error = await memory_only.create_lobby(
        kind="dice",
        chat_id=1,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert memory_only._state_repo is None
    assert memory_only._recovery_task is None
    assert memory_only.redis_recovery_state == "disabled"

    configured = RuntimeGameStore(backend=GameStore())
    configured._redis_url = "redis://unit-test"
    configured._recovery_retry_seconds = 3600
    configured._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]
    _, error = await configured.create_lobby(
        kind="dice",
        chat_id=2,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert configured._recovery_task is not None
    assert configured.redis_recovery_state == "degraded"

    configured.use_in_memory()
    assert configured._recovery_task is None
    assert configured.redis_recovery_state == "disabled"
    assert configured._redis_url is None

    await memory_only.close()
    await configured.close()


def _degraded_store(*, monkeypatch: pytest.MonkeyPatch) -> RuntimeGameStore:
    """A store that degraded before it ever observed an active pointer."""
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]
    store._degrade_to_in_memory(stage="unit-test", exc=_FakeRedisError("redis unavailable"))
    store._cancel_recovery_task()
    return store


@pytest.mark.asyncio
async def test_recovery_preserves_pointer_of_chat_known_only_by_finished_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1: a pointer to a game this process never saw must not be deleted."""
    store = _degraded_store(monkeypatch=monkeypatch)
    # This process never observed an active pointer for any chat.
    assert store._degraded_owned_chats == set()

    # It only learns about chat 300 through a game it finishes while degraded,
    # so chat 300 has no in-memory active pointer.
    game = await _make_started_game(store, chat_id=300)
    await store.finish(game_id=game.game_id, winner_text="done")
    assert store._backend._active_by_chat.get(300) is None

    # Redis still holds a live pointer from another epoch; "foreign-game" was
    # never hydrated into this process.
    repo = _RecordingRepo(active_pointers={300: "foreign-game"})
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    await store._attempt_recovery()

    assert store.redis_recovery_state == "connected"
    # The unknown pointer is preserved, so the other epoch's game stays
    # discoverable ...
    assert repo.active_pointers.get(300) == "foreign-game"
    # ... while the game this process does know is still pushed from memory.
    restored = game_state_module.GameStateCodec().loads(repo.payloads[game.game_id])
    assert restored.status == "finished"
    assert restored.winner_text == "done"

    await store.close()


@pytest.mark.asyncio
async def test_mutation_during_reconciliation_is_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2: a mutation interleaving with the resync must survive in Redis."""
    store = _degraded_store(monkeypatch=monkeypatch)

    game_a = await _make_started_game(store, chat_id=401)
    game_b = await _make_started_game(store, chat_id=402)

    mutated: list[str] = []
    hook_repo: list[_RecordingRepo] = []

    async def _save_hook(save_index: int) -> None:
        # Fires while the first reconciliation pass is awaiting writes: the
        # first game was already persisted from the stale in-memory snapshot,
        # so its post-call sync (repo not wired yet) was dropped.
        if save_index != 2 or mutated:
            return
        mutated.append("yes")
        first_game_id = hook_repo[0].saved[0][0].game_id
        await store.finish(game_id=first_game_id, winner_text="late finish")

    repo = _RecordingRepo(
        active_pointers={401: game_a.game_id, 402: game_b.game_id},
        save_hook=_save_hook,
    )
    hook_repo.append(repo)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    await store._attempt_recovery()

    assert mutated == ["yes"]
    assert store.redis_recovery_state == "connected"
    assert repo.saved[0][0].game_id == game_a.game_id
    restored = game_state_module.GameStateCodec().loads(repo.payloads[game_a.game_id])
    assert restored.status == "finished"
    assert restored.winner_text == "late finish"
    assert repo.active_pointers.get(401) is None
    assert repo.active_pointers.get(402) == game_b.game_id

    await store.close()


class _BlockingPingRepo(_RecordingRepo):
    """Ping that blocks until cancelled -- models a half-open Redis."""

    def __init__(self) -> None:
        super().__init__()
        self.ping_started = asyncio.Event()

    async def ping(self) -> None:
        self.ping_calls += 1
        self.ping_started.set()
        await asyncio.Event().wait()


class _TimeoutThenSuccessRepo(_RecordingRepo):
    """First ping never answers; later pings succeed."""

    def __init__(self) -> None:
        super().__init__()
        self.ping_started = asyncio.Event()

    async def ping(self) -> None:
        self.ping_calls += 1
        self.ping_started.set()
        if self.ping_calls == 1:
            await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_cancelled_recovery_closes_candidate_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-3: cancelling mid-probe must not leak the candidate repo/broker."""
    store = _degraded_store(monkeypatch=monkeypatch)

    repo = _BlockingPingRepo()
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    await asyncio.wait_for(repo.ping_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repo.closed is True
    assert broker.closed is True
    assert store._state_repo is None
    assert store._broker is None
    assert store.redis_recovery_state == "degraded"
    assert store._recovery_in_progress is False

    await store.close()


@pytest.mark.asyncio
async def test_recovery_attempt_is_bounded_by_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-5: a probe that never answers fails the attempt instead of hanging."""
    store = _degraded_store(monkeypatch=monkeypatch)
    store._recovery_attempt_timeout_seconds = 0.05

    repo = _BlockingPingRepo()
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    with pytest.raises(TimeoutError):
        await store._attempt_recovery()

    assert repo.closed is True
    assert broker.closed is True
    assert store._state_repo is None
    assert store._broker is None
    assert store.redis_recovery_state == "degraded"
    assert store._recovery_in_progress is False

    await store.close()


@pytest.mark.asyncio
async def test_recovery_loop_retries_after_timed_out_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-5: a timed-out attempt is treated as failed and the loop retries."""
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 0.02
    store._recovery_attempt_timeout_seconds = 0.2
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    repo = _TimeoutThenSuccessRepo()
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    game = await _make_started_game(store)
    assert store.redis_recovery_state == "degraded"

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while store.redis_recovery_state != "connected":
        assert loop.time() < deadline, "recovery loop did not recover after a timed-out attempt"
        await asyncio.sleep(0.01)

    assert repo.ping_calls >= 2
    assert store._state_repo is repo
    assert store._recovery_in_progress is False
    saved_game, _ = _saved_by_id(repo)[game.game_id]
    assert saved_game.status == "started"

    deadline = loop.time() + 5
    while store._recovery_task is not None:
        assert loop.time() < deadline, "recovery loop did not stop after recovery"
        await asyncio.sleep(0.01)

    await store.close()


@pytest.mark.asyncio
async def test_close_is_terminal_and_blocks_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-4: after close() no Redis error may start a recovery task."""
    store = _degraded_store(monkeypatch=monkeypatch)

    await store.close()
    assert store._closed is True
    assert store._recovery_task is None

    built: list[str] = []

    def _build_redis_runtime():
        built.append("built")
        return _RecordingRepo(), _RecordingBroker()

    store._build_redis_runtime = _build_redis_runtime  # type: ignore[method-assign]

    # A game call that hits the (now closed) repo would normally re-arm
    # recovery; it must not create a task after shutdown.
    _, error = await store.create_lobby(
        kind="dice",
        chat_id=7,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert store._recovery_task is None
    assert store.redis_recovery_state == "degraded"

    await store._attempt_recovery()
    assert built == []
    assert store._recovery_task is None


class _GatedPointerRepo(_RecordingRepo):
    """Blocks the n-th ``set_active_game_id`` call until the test releases it."""

    def __init__(self, *, gate_on_call: int) -> None:
        super().__init__()
        self._gate_on_call = gate_on_call
        self._pointer_calls = 0
        self.gate_entered = asyncio.Event()
        self.release_gate = asyncio.Event()

    async def set_active_game_id(self, *, chat_id: int, game_id: str | None) -> None:
        self._pointer_calls += 1
        if self._pointer_calls == self._gate_on_call:
            self.gate_entered.set()
            await self.release_gate.wait()
        await super().set_active_game_id(chat_id=chat_id, game_id=game_id)


@pytest.mark.asyncio
async def test_final_pointer_pass_keeps_replacement_game_of_finished_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1: a B->C replacement during the final pass must not orphan C.

    While the final pointer pass is awaiting the write for chat 401, a live
    handler finishes B and starts C in chat 402 (the store is already wired to
    the live repo, so those mutations persist themselves). The pass must not
    clear C's freshly persisted pointer from its stale snapshot.

    The handlers' own syncs queue behind the in-flight write (chat write
    locks), so they run as tasks: their in-memory mutations land while the
    pass is suspended and their Redis writes are ordered after it.
    """
    store = _degraded_store(monkeypatch=monkeypatch)

    game_a = await _make_started_game(store, chat_id=401)
    game_b = await _make_started_game(store, chat_id=402)

    # Pass one writes one pointer per known chat (2 calls); the gated call is
    # the final pass's first write (chat 401).
    repo = _GatedPointerRepo(gate_on_call=3)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    await asyncio.wait_for(repo.gate_entered.wait(), timeout=5)

    # Fires while the final pass is suspended in `set_active_game_id` for
    # chat 401, exactly like a concurrent handler would.
    finish_task = asyncio.create_task(store.finish(game_id=game_b.game_id, winner_text="b done"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while game_b.status != "finished":
        assert loop.time() < deadline, "finish never reached the in-memory mutation"
        await asyncio.sleep(0)
    create_c_task = asyncio.create_task(_make_started_game(store, chat_id=402))
    for _ in range(10):
        await asyncio.sleep(0)

    repo.release_gate.set()
    await asyncio.wait_for(task, timeout=5)
    await asyncio.wait_for(finish_task, timeout=5)
    game_c = await asyncio.wait_for(create_c_task, timeout=5)

    assert store.redis_recovery_state == "connected"
    # C replaced the finished B and keeps its Redis pointer ...
    assert repo.active_pointers.get(402) == game_c.game_id
    # ... while chat 401 still points at its untouched active game.
    assert repo.active_pointers.get(401) == game_a.game_id

    # Restart simulation: C must be discovered as the chat's active game.
    restarted = RuntimeGameStore(backend=GameStore())
    restarted._state_repo = repo  # type: ignore[assignment]
    await restarted._hydrate_active_for_chat(chat_id=402)
    active_after_restart = await restarted.backend.get_active_game_for_chat(chat_id=402)
    assert active_after_restart is not None
    assert active_after_restart.game_id == game_c.game_id

    await store.close()
    await restarted.close()


class _HangingSecondPassRepo(_RecordingRepo):
    """Pass-one saves succeed; the second-pass resync hangs like a half-open Redis."""

    def __init__(self, *, games_expected: int) -> None:
        super().__init__()
        self._pass_one_saves = games_expected
        self._saves_since_ping = 0
        self.hang = True
        self.hung_pass_twos = 0

    async def ping(self) -> None:
        self._saves_since_ping = 0
        await super().ping()

    async def save_game(self, game, *, is_active: bool) -> None:
        self._saves_since_ping += 1
        if self.hang and self._saves_since_ping > self._pass_one_saves:
            self.hung_pass_twos += 1
            await asyncio.Event().wait()
        await super().save_game(game, is_active=is_active)


@pytest.mark.asyncio
async def test_second_pass_hang_is_bounded_by_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2: ping and pass one succeed, the second pass hangs -> attempt times out.

    The attempt must fail closed: candidate clients closed and the store back
    in degraded, so the recovery loop can retry it later.
    """
    store = _degraded_store(monkeypatch=monkeypatch)
    store._recovery_attempt_timeout_seconds = 0.05

    await _make_started_game(store, chat_id=501)

    repo = _HangingSecondPassRepo(games_expected=1)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    with pytest.raises(TimeoutError):
        await store._attempt_recovery()

    assert repo.hung_pass_twos >= 1
    assert repo.closed is True
    assert broker.closed is True
    assert store._state_repo is None
    assert store._broker is None
    assert store.redis_recovery_state == "degraded"
    assert store._recovery_in_progress is False

    await store.close()


@pytest.mark.asyncio
async def test_recovery_loop_retries_after_second_pass_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2: a second pass that hangs fails the attempt and the loop retries it."""
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 0.02
    store._recovery_attempt_timeout_seconds = 0.2
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store, chat_id=502)

    repo = _HangingSecondPassRepo(games_expected=1)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while repo.hung_pass_twos < 1:
        assert loop.time() < deadline, "second pass never hung"
        await asyncio.sleep(0.01)

    repo.hang = False

    deadline = loop.time() + 5
    while store.redis_recovery_state != "connected":
        assert loop.time() < deadline, "recovery loop did not retry after a second-pass timeout"
        await asyncio.sleep(0.01)

    assert repo.ping_calls >= 2
    assert store._state_repo is repo
    saved_game, _ = _saved_by_id(repo)[game.game_id]
    assert saved_game.status == "started"

    deadline = loop.time() + 5
    while store._recovery_task is not None:
        assert loop.time() < deadline, "recovery loop did not stop after recovery"
        await asyncio.sleep(0.01)

    await store.close()


class _GatedFakeRedisPipeline:
    """Pipeline stand-in collecting zadds until ``execute``."""

    def __init__(self, client: _GatedFakeRedisClient) -> None:
        self._client = client
        self._zadds: list[tuple[str, dict[str, float]]] = []

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._zadds.append((key, mapping))

    def expire(self, key: str, ttl: int) -> None:
        return None

    async def execute(self) -> list[bool]:
        for key, mapping in self._zadds:
            self._client.zsets.setdefault(key, {}).update(mapping)
        return [True] * len(self._zadds)


class _GatedFakeRedisClient:
    """Minimal async redis client backing a real ``RedisGameStateRepository``.

    SETs for which ``hold_when`` returns true are parked on ``release_gate``
    instead of being applied, modelling a command that was serialized and
    sent but only lands in Redis later (the review's delayed second-pass
    SET); everything else applies immediately, like another connection.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.held_sets: list[str] = []
        self.gate_entered = asyncio.Event()
        self.release_gate = asyncio.Event()
        self.hold_when: Callable[[], bool] | None = None
        # GETs for which ``hang_get_when`` returns true never answer, like a
        # connection that went half-open after the previous command.
        self.hang_get_when: Callable[[str], bool] | None = None
        self.get_hung = asyncio.Event()
        self.closed = False

    async def ping(self) -> None:
        # RedisGameStateRepository.ping() probes through client.ping().
        return None

    async def _hold_if_requested(self, value: str) -> None:
        if self.hold_when is None or not self.hold_when():
            return
        self.held_sets.append(value)
        self.gate_entered.set()
        await self.release_gate.wait()

    async def get(self, key: str) -> str | None:
        if self.hang_get_when is not None and self.hang_get_when(key):
            self.get_hung.set()
            await asyncio.Event().wait()
        return self.data.get(key)

    async def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        ranked = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1], reverse=True)
        return [member for member, _ in ranked][start : end + 1]

    async def aclose(self) -> None:
        self.closed = True

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._hold_if_requested(value)
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.data or key in self.zsets

    def pipeline(self) -> _GatedFakeRedisPipeline:
        return _GatedFakeRedisPipeline(self)


@pytest.mark.asyncio
async def test_second_pass_stale_set_cannot_overwrite_confirmed_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #101 review HIGH: the second pass's delayed SET must not win.

    Schedule from the review: the second pass serializes the started game G
    and parks inside its Redis SET; a concurrent handler finishes G (its own
    sync persists the finished payload through another connection, clears
    the pointer and returns successfully); then the delayed started-SET
    lands and must not overwrite the confirmed finished state, or a restart
    resurrects the finished game as active.
    """
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store, chat_id=601)
    assert store._state_repo is None

    client = _GatedFakeRedisClient()
    repo = game_state_module.RedisGameStateRepository(
        client=client,
        codec=game_state_module.GameStateCodec(),
        ttl=timedelta(hours=1),
    )
    broker = _RecordingBroker()
    # Hold exactly the writes made by the recovery attempt once the live repo
    # is wired in: the second pass. Pass one (repo not yet wired) applies.
    attempt: dict[str, asyncio.Task[None]] = {}
    client.hold_when = lambda: asyncio.current_task() is attempt.get("task") and store._state_repo is repo
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    attempt["task"] = task
    await asyncio.wait_for(client.gate_entered.wait(), timeout=5)

    # The parked write is the second pass's started serialization of G.
    assert len(client.held_sets) == 1
    held = game_state_module.GameStateCodec().loads(client.held_sets[0])
    assert held.game_id == game.game_id
    assert held.status == "started"

    # Step 2 of the schedule: the handler finishes G. Its own sync is
    # ordered behind the in-flight recovery write (chat write lock), so it
    # cannot be confirmed before the delayed SET has landed.
    finish_task = asyncio.create_task(store.finish(game_id=game.game_id, winner_text="u1 wins"))
    for _ in range(10):
        await asyncio.sleep(0)
    assert game.status == "finished"
    assert not finish_task.done()

    # Step 3: let the delayed started-SET land; the handler writes after it.
    client.release_gate.set()
    await asyncio.wait_for(task, timeout=5)
    await asyncio.wait_for(finish_task, timeout=5)
    game_key = repo._game_key(game.game_id)

    assert store.redis_recovery_state == "connected"
    final = game_state_module.GameStateCodec().loads(client.data[game_key])
    assert final.status == "finished"
    assert final.winner_text == "u1 wins"
    assert client.data.get(repo._active_key(game.chat_id)) is None

    # Step 5: restart simulation -- the finished game must not resurrect.
    restarted = RuntimeGameStore(backend=GameStore())
    restarted._state_repo = repo  # type: ignore[assignment]
    await restarted._hydrate_game(game.game_id)
    restored = await restarted.backend.get_game(game.game_id)
    assert restored is not None
    assert restored.status == "finished"
    assert restored.winner_text == "u1 wins"
    active_after_restart = await restarted.backend.get_active_game_for_chat(chat_id=game.chat_id)
    assert active_after_restart is None

    await store.close()
    await restarted.close()


class _GatedSecondPassRepo(_RecordingRepo):
    """Pass-one saves succeed; the second pass's first save blocks on a gate."""

    def __init__(self, *, games_expected: int) -> None:
        super().__init__()
        self._pass_one_saves = games_expected
        self._saves_since_ping = 0
        self.gate_entered = asyncio.Event()
        self.release_gate = asyncio.Event()

    async def ping(self) -> None:
        self._saves_since_ping = 0
        await super().ping()

    async def save_game(self, game, *, is_active: bool) -> None:
        self._saves_since_ping += 1
        if self._saves_since_ping > self._pass_one_saves:
            self.gate_entered.set()
            await self.release_gate.wait()
        await super().save_game(game, is_active=is_active)


@pytest.mark.asyncio
async def test_reconfigure_during_second_pass_keeps_new_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #101 review MEDIUM: reconfigure vs a cancelled old recovery.

    configure_runtime installs a fresh runtime while an old recovery attempt
    is parked inside its second pass and cancels that attempt. The dying
    attempt must close only its own candidates: an unconditional degrade
    would null the new repo/broker, flip the fresh configuration back to
    degraded, re-schedule recovery and leak the new clients -- all from a
    stale generation.
    """
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    await _make_started_game(store, chat_id=701)
    store._cancel_recovery_task()  # drop the idle background loop; the attempt below is driven directly

    repo = _GatedSecondPassRepo(games_expected=1)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    store._recovery_task = task  # type: ignore[assignment]
    await asyncio.wait_for(repo.gate_entered.wait(), timeout=5)

    # Reconfigure while the old attempt is suspended mid-second-pass.
    new_repo = _RecordingRepo()
    new_broker = _RecordingBroker()
    monkeypatch.setattr(
        game_state_module.RedisGameStateRepository,
        "from_url",
        classmethod(lambda cls, *, redis_url, codec, ttl: new_repo),
    )
    monkeypatch.setattr(
        game_state_module.RedisLiveEventBroker,
        "from_url",
        classmethod(lambda cls, *, redis_url: new_broker),
    )
    store.configure_runtime(redis_url="redis://reconfigured", ttl_hours=24)

    assert store._state_repo is new_repo
    assert store._broker is new_broker

    with pytest.raises(asyncio.CancelledError):
        await task

    # The stale generation left the freshly configured runtime untouched ...
    assert store._state_repo is new_repo
    assert store._broker is new_broker
    assert store._redis_degraded is False
    assert store._degraded_owned_chats == set()
    assert store.redis_recovery_state == "connected"
    assert store._recovery_task is None
    assert store._recovery_in_progress is False
    # ... closed only its own candidates, and did not leak the new clients.
    assert repo.closed is True
    assert broker.closed is True
    assert new_repo.closed is False
    assert new_broker.closed is False

    await store.close()
    assert new_repo.closed is True
    assert new_broker.closed is True


@pytest.mark.asyncio
async def test_use_in_memory_during_second_pass_keeps_memory_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #101 review MEDIUM: use_in_memory vs a cancelled old recovery.

    Switching to memory-only while an old recovery attempt is parked in its
    second pass must stay a clean switch: the dying attempt may not flip the
    store to degraded -- a state a later configure_runtime would otherwise
    inherit.
    """
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    await _make_started_game(store, chat_id=702)
    store._cancel_recovery_task()  # drop the idle background loop; the attempt below is driven directly

    repo = _GatedSecondPassRepo(games_expected=1)
    broker = _RecordingBroker()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    store._recovery_task = task  # type: ignore[assignment]
    await asyncio.wait_for(repo.gate_entered.wait(), timeout=5)

    store.use_in_memory()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store._state_repo is None
    assert store._broker is None
    assert store._redis_url is None
    assert store._redis_degraded is False
    assert store.redis_recovery_state == "disabled"
    assert store._recovery_task is None
    assert repo.closed is True
    assert broker.closed is True

    await store.close()



async def _stale_set_then_hang_schedule(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chat_id: int,
    attempt_timeout: float,
) -> tuple[RuntimeGameStore, GroupGame, _GatedFakeRedisClient, game_state_module.RedisGameStateRepository, _RecordingBroker, asyncio.Task[None], asyncio.Task[object]]:
    """Drive the PR #101 review HIGH schedule up to the hung recovery GET.

    1. Pass one succeeds; the second pass serializes started G and parks
       inside its SET.
    2. A handler finishes G (in memory) and starts its own sync.
    3. The delayed started-SET lands. G is finished in memory by now, so the
       recovery's ``save_game`` continues on the finished branch ...
    4. ... and hangs on the GET of the chat's active key: the repair SET
       never comes. The caller then times out / closes the attempt.
    """
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._recovery_attempt_timeout_seconds = attempt_timeout
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store, chat_id=chat_id)
    assert store._state_repo is None
    store._cancel_recovery_task()  # the attempt below is driven directly

    client = _GatedFakeRedisClient()
    repo = game_state_module.RedisGameStateRepository(
        client=client,
        codec=game_state_module.GameStateCodec(),
        ttl=timedelta(hours=1),
    )
    broker = _RecordingBroker()
    attempt: dict[str, asyncio.Task[None]] = {}

    def _in_second_pass() -> bool:
        return asyncio.current_task() is attempt.get("task") and store._state_repo is repo

    client.hold_when = _in_second_pass
    active_key = repo._active_key(game.chat_id)
    client.hang_get_when = lambda key: key == active_key and _in_second_pass() and client.release_gate.is_set()
    store._build_redis_runtime = lambda: (repo, broker)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    attempt["task"] = task
    store._recovery_task = task  # type: ignore[assignment]
    await asyncio.wait_for(client.gate_entered.wait(), timeout=5)
    held = game_state_module.GameStateCodec().loads(client.held_sets[0])
    assert held.game_id == game.game_id
    assert held.status == "started"

    finish_task: asyncio.Task[object] = asyncio.create_task(
        store.finish(game_id=game.game_id, winner_text="u1 wins")
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert game.status == "finished"

    client.release_gate.set()
    await asyncio.wait_for(client.get_hung.wait(), timeout=5)
    # The stale started payload has landed and the repair never came.
    stale = game_state_module.GameStateCodec().loads(client.data[repo._game_key(game.game_id)])
    assert stale.status == "started"
    return store, game, client, repo, broker, task, finish_task


async def _assert_finished_survives_restart(
    repo: game_state_module.RedisGameStateRepository,
    client: _GatedFakeRedisClient,
    game: GroupGame,
) -> None:
    client.hang_get_when = None
    persisted = game_state_module.GameStateCodec().loads(client.data[repo._game_key(game.game_id)])
    assert persisted.status == "finished"
    assert persisted.winner_text == "u1 wins"
    assert client.data.get(repo._active_key(game.chat_id)) is None

    # Restart by game id ...
    restarted = RuntimeGameStore(backend=GameStore())
    restarted._state_repo = repo  # type: ignore[assignment]
    await restarted._hydrate_game(game.game_id)
    restored = await restarted.backend.get_game(game.game_id)
    assert restored is not None
    assert restored.status == "finished"
    assert await restarted.backend.get_active_game_for_chat(chat_id=game.chat_id) is None

    # ... and by the players' recent-games index.
    restarted_recent = RuntimeGameStore(backend=GameStore())
    restarted_recent._state_repo = repo  # type: ignore[assignment]
    await restarted_recent._hydrate_recent_games_for_user(user_id=2)
    restored_recent = await restarted_recent.backend.get_game(game.game_id)
    assert restored_recent is not None
    assert restored_recent.status == "finished"
    assert await restarted_recent.backend.get_active_game_for_chat(chat_id=game.chat_id) is None


@pytest.mark.asyncio
async def test_stale_second_pass_set_then_timeout_keeps_confirmed_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #101 third review HIGH, timeout variant.

    The stale second-pass SET already landed and the recovery hangs on the
    next GET of its own ``save_game`` before any repair SET; the second-pass
    timeout fails the attempt. The handler's finish must still end up in
    Redis (its write is ordered after the stale one), and a restart must
    hydrate G as finished -- by id and through the recent-games index.
    """
    store, game, client, repo, broker, task, finish_task = await _stale_set_then_hang_schedule(
        monkeypatch, chat_id=611, attempt_timeout=0.5
    )
    # The handler's write is ordered behind the in-flight recovery write.
    assert not finish_task.done()

    # The second-pass timeout fires while the recovery hangs on the GET.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=5)

    await asyncio.wait_for(finish_task, timeout=5)
    assert store.redis_recovery_state == "degraded"
    assert store._state_repo is None
    # The candidates are closed -- the repo only after the handler's sync
    # that was still writing through it.
    assert broker.closed is True
    assert client.closed is True

    await _assert_finished_survives_restart(repo, client, game)
    await store.close()


@pytest.mark.asyncio
async def test_stale_second_pass_set_then_close_keeps_confirmed_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #101 third review HIGH, shutdown variant.

    Same schedule, but a regular ``close()`` cancels the recovery while it
    hangs on the GET after the stale SET. The finish must survive in Redis
    and after the restart.
    """
    store, game, client, repo, broker, task, finish_task = await _stale_set_then_hang_schedule(
        monkeypatch, chat_id=612, attempt_timeout=3600
    )
    assert not finish_task.done()

    await store.close()
    assert task.done()
    await asyncio.wait_for(finish_task, timeout=5)
    assert broker.closed is True
    assert client.closed is True

    await _assert_finished_survives_restart(repo, client, game)


class _FailingPublishBroker(_RecordingBroker):
    """Broker whose connection breaks independently of the repo's."""

    async def publish(self, event) -> None:
        raise _FakeRedisError("broker connection reset")


@pytest.mark.asyncio
async def test_concurrent_broker_degrade_during_second_pass_fails_attempt_and_closes_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #101 third review MEDIUM: parallel degradation during the second pass.

    Recovery wires R1/B1 and parks in its second-pass write; another handler
    calls ``publish_event`` and B1 fails on its own connection, which
    degrades the store (repo/broker nulled, no extra task since the attempt
    is registered). R1's remaining second-pass work then succeeds. The
    attempt must not report success: R1 and B1 are closed, the store stays
    degraded, and the next attempt recovers on a fresh R2/B2 pair.
    """
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 3600
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    game = await _make_started_game(store, chat_id=801)
    store._cancel_recovery_task()  # the attempts below are driven directly

    repo_1 = _GatedSecondPassRepo(games_expected=1)
    broker_1 = _FailingPublishBroker()
    repo_2 = _RecordingRepo()
    broker_2 = _RecordingBroker()
    pairs = [(repo_1, broker_1), (repo_2, broker_2)]
    store._build_redis_runtime = lambda: pairs.pop(0)  # type: ignore[method-assign]

    task = asyncio.create_task(store._attempt_recovery())
    store._recovery_task = task  # type: ignore[assignment]
    await asyncio.wait_for(repo_1.gate_entered.wait(), timeout=5)
    assert store._state_repo is repo_1
    assert store._broker is broker_1

    # Steps 2-3: an unrelated handler publishes through B1, which fails.
    await store.publish_event(event_type="new_vote", scope="chat", chat_id=801)
    assert store._state_repo is None
    assert store._broker is None
    assert store._redis_degraded is True
    assert store._recovery_task is task

    # Step 4: R1's own second-pass operations now succeed.
    repo_1.release_gate.set()
    with pytest.raises(game_state_module._RecoverySupersededError):
        await asyncio.wait_for(task, timeout=5)

    assert repo_1.closed is True
    assert broker_1.closed is True
    assert store._state_repo is None
    assert store._broker is None
    assert store.redis_recovery_state == "degraded"
    assert store._recovery_in_progress is False

    # Step 5: the next attempt recovers on a fresh pair.
    store._recovery_task = None
    await store._attempt_recovery()
    assert store.redis_recovery_state == "connected"
    assert store._state_repo is repo_2
    assert store._broker is broker_2
    assert repo_2.closed is False
    assert broker_2.closed is False
    saved_game, _ = _saved_by_id(repo_2)[game.game_id]
    assert saved_game.status == "started"

    await store.close()
    assert repo_2.closed is True
    assert broker_2.closed is True


@pytest.mark.asyncio
async def test_recovery_loop_retries_after_superseded_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop treats a superseded attempt as an ordinary failed one."""
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._redis_url = "redis://unit-test"
    store._recovery_retry_seconds = 0.01
    store._recovery_attempt_timeout_seconds = 5
    store._state_repo = _BrokenSaveRepo()  # type: ignore[assignment]

    repo_1 = _GatedSecondPassRepo(games_expected=1)
    broker_1 = _FailingPublishBroker()
    repo_2 = _RecordingRepo()
    broker_2 = _RecordingBroker()
    pairs = [(repo_1, broker_1), (repo_2, broker_2)]
    store._build_redis_runtime = lambda: pairs.pop(0)  # type: ignore[method-assign]

    await _make_started_game(store, chat_id=802)
    loop_task = store._recovery_task
    assert loop_task is not None
    await asyncio.wait_for(repo_1.gate_entered.wait(), timeout=5)

    await store.publish_event(event_type="new_vote", scope="chat", chat_id=802)
    assert store._recovery_task is loop_task
    repo_1.release_gate.set()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while store.redis_recovery_state != "connected":
        assert loop.time() < deadline, "recovery loop did not retry after a superseded attempt"
        await asyncio.sleep(0.01)

    assert store._state_repo is repo_2
    assert store._broker is broker_2
    assert repo_1.closed is True
    assert broker_1.closed is True

    await store.close()
