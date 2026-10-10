"""Real-store timeout recovery and the race between timer preflight and lock."""
from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from selara.presentation.game_state import GameStore

router = importlib.import_module("selara.presentation.handlers.game.router")


async def started_game():
    store = GameStore()
    game, error = await store.create_lobby(
        kind="zlobcards", chat_id=-100, chat_title="group",
        owner_user_id=1, owner_label="u1", reveal_eliminated_role=False,
        actions_18_enabled=False,
    )
    assert game is not None and error is None
    for uid in (2, 3):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
    _, error = await store.start(game_id=game.game_id, actions_18_enabled=False)
    assert error is None
    return store, game


async def submit(store, game, uid):
    _, result, error = await store.zlob_submit_cards(
        game_id=game.game_id, user_id=uid,
        card_indexes=tuple(range(game.zlob_black_slots)),
    )
    assert error is None and result is not None
    return result


@pytest.mark.parametrize("count", [0, 1, 2])
async def test_real_private_timer_is_bounded_and_retains_answers(monkeypatch, count):
    store, game = await started_game()
    for uid in range(1, count + 1):
        await submit(store, game, uid)
    submissions = dict(game.zlob_submissions)
    hands = dict(game.zlob_hands)
    scores = dict(game.zlob_scores)
    phase_start = game.phase_started_at
    now = phase_start

    class Clock:
        @staticmethod
        def now(_tz):
            return now

    sleeps = []

    async def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += timedelta(seconds=seconds)

    monkeypatch.setattr(router, "datetime", Clock)
    monkeypatch.setattr(router.asyncio, "sleep", sleep)
    monkeypatch.setattr(router, "GAME_STORE", store)
    monkeypatch.setattr(router, "_GAME_PHASE_TASKS", {})
    board = AsyncMock()
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", board)
    schedule_private = router._schedule_phase_timer
    schedule_vote = Mock()
    monkeypatch.setattr(router, "_schedule_phase_timer", schedule_vote)
    bot, settings = Mock(), Mock()

    schedule_private(bot, game, settings)
    original = router._GAME_PHASE_TASKS[game.game_id]
    await original
    if count < 2:
        grace = router._GAME_PHASE_TASKS[game.game_id]
        assert grace is not original
        await grace
        assert router._GAME_PHASE_TASKS[game.game_id] is grace
        assert grace.done()  # no third timer, even after an unattended timeout
        assert sleeps == [75, 90]
        assert board.await_count == 2
        assert game.phase == "private_answers"
        assert game.phase_started_at == phase_start
        assert game.zlob_submissions == submissions
        assert game.zlob_hands == hands
        assert game.zlob_scores == scores
        assert "Ведущему" in board.await_args.kwargs["note"]
        assert "завершить" in board.await_args.kwargs["note"]
        # Late completion remains valid after all automatic retries expired.
        for uid in range(count + 1, 4):
            result = await submit(store, game, uid)
        assert result.vote_opened and game.phase == "public_vote"
    else:
        assert sleeps == [75]
        assert game.phase == "public_vote" and len(game.zlob_options) == 2
        assert board.await_count == 1
        schedule_vote.assert_called_once_with(bot, game, settings)


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("changed", ["round", "phase", "transitioned"])
async def test_real_timer_cannot_mutate_after_preflight_before_lock(monkeypatch, restored, changed):
    store, game = await started_game()
    await submit(store, game, 1)
    await submit(store, game, 2)
    monkeypatch.setattr(router, "GAME_STORE", store)
    monkeypatch.setattr(router, "_GAME_PHASE_TASKS", {})
    board, schedule = AsyncMock(), Mock()
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", board)
    original_schedule = router._schedule_phase_timer
    monkeypatch.setattr(router, "_schedule_phase_timer", schedule)

    async def sleep(_seconds):
        pass

    monkeypatch.setattr(router.asyncio, "sleep", sleep)
    lock_game = store._lock_game
    calls = 0

    @asynccontextmanager
    async def interleaved_lock(game_id):
        nonlocal calls
        calls += 1
        if calls == 2:  # get_game preflight passed; zlob_open_vote now acquires
            if changed == "round":
                game.round_no += 1
            elif changed == "phase":
                game.phase_started_at += timedelta(seconds=1)
            else:
                game.phase = "public_vote"
        async with lock_game(game_id):
            yield

    monkeypatch.setattr(store, "_lock_game", interleaved_lock)
    before = (dict(game.zlob_submissions), dict(game.zlob_hands), dict(game.zlob_scores))
    if restored:
        router._schedule_phase_timer_with_remaining(Mock(), game, Mock(), 5)
    else:
        original_schedule(Mock(), game, Mock())
    await router._GAME_PHASE_TASKS[game.game_id]
    assert calls == 2
    assert game.phase == ("public_vote" if changed == "transitioned" else "private_answers")
    assert (game.zlob_submissions, game.zlob_hands, game.zlob_scores) == before
    board.assert_not_awaited()
    schedule.assert_not_called()


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("elapsed", [110, 200])
async def test_real_store_restart_preserves_recovery_deadline(monkeypatch, count, elapsed):
    from types import SimpleNamespace

    import selara.infrastructure.db.repositories as repositories
    from selara.presentation.game_state import GameStateCodec

    old_store, old_game = await started_game()
    for uid in range(1, count + 1):
        await submit(old_store, old_game, uid)
    old_game.phase_started_at = datetime.now(timezone.utc) - timedelta(seconds=elapsed)
    codec = GameStateCodec()
    game = codec.loads(codec.dumps(old_game))
    store = GameStore()
    store._by_id[game.game_id] = game
    store._active_by_chat[game.chat_id] = game.game_id
    now = datetime.now(timezone.utc)
    phase_start = game.phase_started_at

    class Clock:
        @staticmethod
        def now(_tz):
            return now

    sleeps = []

    async def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += timedelta(seconds=seconds + 0.01)

    class Repo:
        def __init__(self, _session):
            pass

        async def get_chat_settings(self, *, chat_id):
            assert chat_id == game.chat_id
            return SimpleNamespace()

    @asynccontextmanager
    async def session_factory():
        yield SimpleNamespace()

    monkeypatch.setattr(repositories, "SqlAlchemyActivityRepository", Repo)
    monkeypatch.setattr(router, "datetime", Clock)
    monkeypatch.setattr(router.asyncio, "sleep", sleep)
    monkeypatch.setattr(router, "GAME_STORE", store)
    monkeypatch.setattr(router, "_GAME_PHASE_TASKS", {})
    board, schedule = AsyncMock(), Mock()
    monkeypatch.setattr(router, "_safe_edit_or_send_game_board", board)
    monkeypatch.setattr(router, "_schedule_phase_timer", schedule)

    await router.restore_phase_timers(Mock(), session_factory)
    restored_timer = router._GAME_PHASE_TASKS[game.game_id]
    await restored_timer
    assert router._GAME_PHASE_TASKS[game.game_id] is restored_timer
    assert restored_timer.done()
    assert len(sleeps) == 1
    if elapsed == 110:
        assert 54 <= sleeps[0] <= 55
    else:
        assert sleeps == [5]
    assert board.await_count == 1
    if count < 2:
        assert game.phase == "private_answers"
        assert game.phase_started_at == phase_start
        assert game.zlob_submissions == old_game.zlob_submissions
        assert game.zlob_hands == old_game.zlob_hands
        assert game.zlob_scores == old_game.zlob_scores
        schedule.assert_not_called()
        assert "Ведущему" in board.await_args.kwargs["note"]
    else:
        assert game.phase == "public_vote"
        assert len(game.zlob_options) == 2
        schedule.assert_called_once()
