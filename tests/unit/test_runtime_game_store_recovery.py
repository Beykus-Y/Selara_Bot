"""Outage -> mutations -> Redis return -> restart recovery tests for RuntimeGameStore.

See issue #65: a Redis outage degrades the store to memory-only; when Redis
comes back the runtime must reconcile the diverged state so a restart can
never resurrect a stale phase or a game finished during the degradation.
"""

from __future__ import annotations

import asyncio

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
    ) -> None:
        self.payloads = dict(payloads or {})
        self.active_pointers = dict(active_pointers or {})
        self.saved: list[tuple[GroupGame, bool]] = []
        self.ping_calls = 0
        self.closed = False
        self._fail_ping_until = fail_ping_until
        self._fail_save = fail_save

    async def ping(self) -> None:
        self.ping_calls += 1
        if self.ping_calls <= self._fail_ping_until:
            raise _FakeRedisError("redis still down")

    async def save_game(self, game, *, is_active: bool) -> None:
        if self._fail_save:
            raise _FakeRedisError("redis still down")
        self.saved.append((game, is_active))
        self.payloads[game.game_id] = game_state_module.GameStateCodec().dumps(game)

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
