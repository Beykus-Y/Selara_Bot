import pytest

import selara.presentation.game_state as game_state_module

from selara.presentation.game_state import GameStore, RuntimeGameStore


class _FakeRedisError(Exception):
    pass


class _BrokenReadRepo:
    async def load_active_game_id(self, chat_id: int):
        raise _FakeRedisError("redis unavailable")


class _BrokenWriteRepo:
    async def save_game(self, game, *, is_active: bool):
        raise _FakeRedisError("redis unavailable")


class _BrokenBroker:
    async def publish(self, event):
        raise _FakeRedisError("redis unavailable")


@pytest.mark.asyncio
async def test_runtime_store_falls_back_to_memory_on_hydration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._state_repo = _BrokenReadRepo()  # type: ignore[assignment]

    active_game = await store.get_active_game_for_chat(chat_id=777)

    assert active_game is None
    assert store._state_repo is None


@pytest.mark.asyncio
async def test_runtime_store_falls_back_to_memory_on_sync_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._state_repo = _BrokenWriteRepo()  # type: ignore[assignment]

    game, error = await store.create_lobby(
        kind="dice",
        chat_id=101,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )

    assert error is None
    assert game is not None
    assert store._state_repo is None


@pytest.mark.asyncio
async def test_runtime_store_falls_back_to_memory_on_publish_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_state_module, "_RedisError", _FakeRedisError)
    store = RuntimeGameStore(backend=GameStore())
    store._state_repo = _BrokenWriteRepo()  # type: ignore[assignment]
    store._broker = _BrokenBroker()  # type: ignore[assignment]

    await store.publish_event(
        event_type="new_vote",
        scope="chat",
        chat_id=321,
    )

    assert store._state_repo is None
    assert store._broker is None
