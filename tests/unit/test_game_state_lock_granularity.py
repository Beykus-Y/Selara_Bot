"""Regression tests for per-game lock granularity in :class:`GameStore`.

The store used to guard every operation with a single global ``asyncio.Lock``,
so independent games in unrelated chats could not mutate their state
concurrently. These tests pin the intended granularity:

* different games mutate concurrently,
* two actions of one game still serialize,
* a chat can never end up with two active lobbies,
* the lock table is reference counted and does not leak,
* a finished game stops being the active game of its chat.
"""

import asyncio
from contextlib import asynccontextmanager

from selara.presentation.game_state import GameStore


async def _create_lobby(store: GameStore, *, chat_id: int, owner_user_id: int = 1):
    return await store.create_lobby(
        kind="mafia",
        chat_id=chat_id,
        chat_title=f"chat-{chat_id}",
        owner_user_id=owner_user_id,
        owner_label=f"u{owner_user_id}",
        reveal_eliminated_role=True,
    )


def _install_lock_probe(store: GameStore, watched_game_id: str):
    """Make the critical section of ``watched_game_id`` observable.

    Returns the event that is set while that critical section is held and the
    event that releases it. On a store that still serializes everything on one
    global lock the probe falls back to that lock, which is exactly how the
    original bug is reproduced: a mutation of an unrelated game cannot proceed.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    lock_game = getattr(store, "_lock_game", None)

    if lock_game is not None:
        @asynccontextmanager
        async def probe(game_id: str):
            async with lock_game(game_id):
                if game_id == watched_game_id:
                    entered.set()
                    await release.wait()
                yield

        store._lock_game = probe
    else:
        global_lock = store._lock

        class _GlobalLockProbe:
            async def __aenter__(self):
                await global_lock.acquire()
                entered.set()
                await release.wait()

            async def __aexit__(self, *exc_info):
                global_lock.release()
                return False

        store._lock = _GlobalLockProbe()

    return entered, release


async def test_independent_games_mutate_concurrently():
    store = GameStore()
    first, first_error = await _create_lobby(store, chat_id=101)
    second, second_error = await _create_lobby(store, chat_id=102)
    assert first_error is None and second_error is None
    assert first is not None and second is not None

    first_entered, release_first = _install_lock_probe(store, first.game_id)

    first_task = asyncio.create_task(store.set_message_id(game_id=first.game_id, message_id=11))
    await asyncio.wait_for(first_entered.wait(), timeout=1.0)

    # The second game must not wait for the first game's critical section.
    second_task = asyncio.create_task(store.set_message_id(game_id=second.game_id, message_id=22))
    try:
        await asyncio.wait_for(asyncio.shield(second_task), timeout=1.0)
    finally:
        release_first.set()
    await asyncio.wait_for(first_task, timeout=1.0)

    assert first.message_id == 11
    assert second.message_id == 22


async def test_two_actions_of_one_game_serialize():
    store = GameStore()
    game, error = await _create_lobby(store, chat_id=201)
    assert error is None and game is not None

    entered, release = _install_lock_probe(store, game.game_id)
    observed = []

    first_task = asyncio.create_task(store.set_message_id(game_id=game.game_id, message_id=1))
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    async def second_action():
        result = await store.set_execution_confirm_message_id(game_id=game.game_id, message_id=2)
        observed.append(result)

    second_task = asyncio.create_task(second_action())
    await asyncio.sleep(0)
    assert observed == [], "second action of the same game entered its critical section early"

    release.set()
    await asyncio.wait_for(first_task, timeout=1.0)
    await asyncio.wait_for(second_task, timeout=1.0)
    assert observed == [game]


async def test_concurrent_lobby_creation_keeps_a_single_active_game():
    store = GameStore()

    results = await asyncio.gather(
        *(
            _create_lobby(store, chat_id=301, owner_user_id=owner_user_id)
            for owner_user_id in range(1, 6)
        )
    )
    created = [game for game, error in results if error is None]
    assert len(created) == 1

    active = await store.get_active_game_for_chat(chat_id=301)
    assert active is not None
    assert active.game_id == created[0].game_id

    rejected = [error for game, error in results if error is not None]
    assert len(rejected) == 4


async def test_second_lobby_in_same_chat_is_rejected():
    store = GameStore()
    first, first_error = await _create_lobby(store, chat_id=401)
    assert first_error is None and first is not None

    second, second_error = await _create_lobby(store, chat_id=401)
    assert second is None
    assert second_error is not None

    active = await store.get_active_game_for_chat(chat_id=401)
    assert active is not None and active.game_id == first.game_id


async def test_lock_table_is_cleaned_up_and_does_not_leak():
    store = GameStore()
    game, error = await _create_lobby(store, chat_id=501)
    assert error is None and game is not None
    assert store._game_locks == {}
    assert store._game_lock_refs == {}

    for message_id in range(5):
        await store.set_message_id(game_id=game.game_id, message_id=message_id)

    # Every operation released its reference, so no lock stays registered.
    assert store._game_locks == {}
    assert store._game_lock_refs == {}

    await store.finish(game_id=game.game_id)
    assert store._game_locks == {}
    assert store._game_lock_refs == {}


async def test_finished_game_stops_being_active_for_its_chat():
    store = GameStore()
    game, error = await _create_lobby(store, chat_id=601)
    assert error is None and game is not None
    assert await store.get_active_game_for_chat(chat_id=601) is not None

    await store.finish(game_id=game.game_id)

    assert await store.get_active_game_for_chat(chat_id=601) is None
    assert await store.list_active_games(chat_ids={601}) == []

    # A fresh lobby can be created in the freed chat.
    replacement, replacement_error = await _create_lobby(store, chat_id=601)
    assert replacement_error is None and replacement is not None
    assert replacement.game_id != game.game_id


async def test_chat_scoped_action_and_game_action_do_not_deadlock():
    store = GameStore()
    game, error = await _create_lobby(store, chat_id=701)
    assert error is None and game is not None

    entered, release = _install_lock_probe(store, game.game_id)
    label_task = asyncio.create_task(store.set_player_label(chat_id=701, user_id=1, user_label="renamed"))
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    # finish() takes the same game lock and then briefly the registry lock.
    finish_task = asyncio.create_task(store.finish(game_id=game.game_id))
    await asyncio.sleep(0)
    assert not finish_task.done()

    release.set()
    await asyncio.wait_for(label_task, timeout=1.0)
    finished = await asyncio.wait_for(finish_task, timeout=1.0)
    assert finished is not None
    assert game.status == "finished"
    assert await store.get_active_game_for_chat(chat_id=701) is None
