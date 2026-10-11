"""Regression tests: obsolete Telegram I/O cannot replace a later game step."""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GameStore, GroupGame, RuntimeGameStore

ui = importlib.import_module("selara.presentation.handlers.game.router")


def _active_game(*, kind: str, phase: str) -> GroupGame:
    return GroupGame(
        game_id="race-253", kind=kind, chat_id=-100, chat_title="Race",
        owner_user_id=1, players={1: "A", 2: "B", 3: "C"},
        status="started", phase=phase, round_no=2,
        phase_started_at=datetime.now(timezone.utc),
        alive_player_ids={1, 2, 3},
    )


@pytest.mark.asyncio
async def test_late_quiz_question_cannot_overwrite_or_delete_newer_feed(monkeypatch):
    game = _active_game(kind="quiz", phase="freeplay")
    game.quiz_current_question_index = 1  # Q2 is active.
    game.quiz_feed_message_id = 101       # Prior notification to remove.
    store = GameStore()
    store._by_id[game.game_id] = game
    monkeypatch.setattr(ui, "GAME_STORE", store)
    sent_first, release_first = asyncio.Event(), asyncio.Event()
    deleted: list[int] = []
    sent_count = 0

    class Bot:
        async def send_message(self, **kwargs):
            nonlocal sent_count
            sent_count += 1
            if sent_count == 1:
                sent_first.set()
                await release_first.wait()
                return SimpleNamespace(message_id=202)  # delayed Q2
            return SimpleNamespace(message_id=303)      # live Q3

        async def delete_message(self, **kwargs):
            deleted.append(kwargs["message_id"])

    bot = Bot()
    delayed = asyncio.create_task(
        ui._sync_quiz_feed_message(
            bot, game, question_no=2, expected_question_index=1,
        )
    )
    try:
        await asyncio.wait_for(sent_first.wait(), timeout=2)
        # Q3 wins while Telegram has not returned Q2's message ID.
        async with store._lock_game(game.game_id):
            game.quiz_current_question_index = 2
            game.round_no = 3
            game.phase_started_at = datetime.now(timezone.utc)
        await ui._sync_quiz_feed_message(
            bot, game, question_no=3, expected_question_index=2,
        )
        assert game.quiz_feed_message_id == 303

        release_first.set()
        await asyncio.wait_for(delayed, timeout=2)
        assert game.quiz_feed_message_id == 303
        assert deleted == [101, 202]
        assert await store.set_quiz_feed_message_id(
            game_id=game.game_id, message_id=404, expected_question_index=1,
        ) is None

        # A fully stale publisher must neither delete the winning Q3 nor send.
        await ui._sync_quiz_feed_message(
            bot, game, question_no=2, expected_question_index=1,
        )
        assert sent_count == 2
        assert deleted == [101, 202]
        assert game.quiz_feed_message_id == 303
    finally:
        release_first.set()
        if not delayed.done():
            await delayed


@pytest.mark.asyncio
async def test_late_mafia_night_handler_cannot_restart_day_vote_timer(monkeypatch):
    game = _active_game(kind="mafia", phase="day_discussion")
    now = datetime.now(timezone.utc)
    game.phase_started_at = now
    settings = SimpleNamespace(
        mafia_night_seconds=3600, mafia_day_seconds=3600,
        mafia_vote_seconds=3600,
    )
    mock_store = SimpleNamespace(
        mafia_resolve_night=AsyncMock(return_value=(
            game,
            SimpleNamespace(
                killed_user_ids=(), tie_on_mafia_vote=False,
                public_notes=(), winner_text=None,
            ),
            None,
        )),
    )
    monkeypatch.setattr(ui, "GAME_STORE", mock_store)
    monkeypatch.setattr(ui, "_safe_edit_or_send_game_board", AsyncMock())
    monkeypatch.setattr(ui, "_send_game_feed_event", AsyncMock())
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def stalled_report(*args, **kwargs):
        entered.set()
        await proceed.wait()

    monkeypatch.setattr(ui, "_send_night_private_reports", stalled_report)
    bot = SimpleNamespace()
    old = asyncio.create_task(ui._advance_mafia_night(
        bot, game.game_id, settings, triggered_by_timer=False, expected_round_no=2,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        game.phase = "day_vote"
        game.phase_started_at = datetime.now(timezone.utc)
        ui._schedule_phase_timer(bot, game, settings)
        vote_timer = ui._GAME_PHASE_TASKS[game.game_id]

        proceed.set()
        assert await asyncio.wait_for(old, timeout=2)
        assert ui._GAME_PHASE_TASKS[game.game_id] is vote_timer
        assert not vote_timer.cancelled()
    finally:
        proceed.set()
        if not old.done():
            await old
        ui._cancel_phase_timer(game.game_id)


@pytest.mark.asyncio
async def test_late_zlob_round_handler_cannot_restart_public_vote_timer(monkeypatch):
    game = _active_game(kind="zlobcards", phase="private_answers")
    settings = SimpleNamespace()
    mock_store = SimpleNamespace(
        zlob_resolve_round=AsyncMock(return_value=(
            game, SimpleNamespace(finished=False), None,
        )),
    )
    monkeypatch.setattr(ui, "GAME_STORE", mock_store)
    monkeypatch.setattr(ui, "_format_zlob_round_resolution", lambda *args: "Раунд завершён")
    monkeypatch.setattr(ui, "_safe_edit_or_send_game_board", AsyncMock())
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def stalled_hands(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return 0

    monkeypatch.setattr(ui, "_notify_zlob_private_hands", stalled_hands)
    bot = SimpleNamespace()
    old = asyncio.create_task(ui._resolve_zlob_round(
        bot, game.game_id, settings, force=True, triggered_by_auto=False,
        expected_round_no=2,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        game.phase = "public_vote"
        game.phase_started_at = datetime.now(timezone.utc)
        ui._schedule_phase_timer(bot, game, settings)
        vote_timer = ui._GAME_PHASE_TASKS[game.game_id]

        proceed.set()
        result, error = await asyncio.wait_for(old, timeout=2)
        assert result is game and error is None
        assert ui._GAME_PHASE_TASKS[game.game_id] is vote_timer
        assert not vote_timer.cancelled()
    finally:
        proceed.set()
        if not old.done():
            await old
        ui._cancel_phase_timer(game.game_id)


@pytest.mark.asyncio
async def test_stale_restore_snapshot_cannot_replace_a_new_mafia_timer():
    game = _active_game(kind="mafia", phase="day_discussion")
    old_version = ui._timer_phase_version(game)
    game.phase = "day_vote"
    game.phase_started_at = datetime.now(timezone.utc)
    settings = SimpleNamespace(mafia_vote_seconds=3600)
    bot = SimpleNamespace()
    ui._schedule_phase_timer(bot, game, settings)
    current_timer = ui._GAME_PHASE_TASKS[game.game_id]
    try:
        ui._schedule_phase_timer_with_remaining(
            bot, game, settings, 5,
            expected_phase_version=old_version,
        )
        assert ui._GAME_PHASE_TASKS[game.game_id] is current_timer
        assert not current_timer.cancelled()
    finally:
        ui._cancel_phase_timer(game.game_id)


@pytest.mark.asyncio
async def test_runtime_store_snapshots_transition_before_slow_redis_sync(monkeypatch):
    backend = GameStore()
    game = _active_game(kind="mafia", phase="day_discussion")
    backend._by_id[game.game_id] = game
    runtime = RuntimeGameStore(backend=backend)
    sync_entered, resume_sync = asyncio.Event(), asyncio.Event()

    async def delayed_redis_sync(*args, **kwargs):
        sync_entered.set()
        await resume_sync.wait()

    monkeypatch.setattr(runtime, "_hydrate_for_call", AsyncMock())
    monkeypatch.setattr(runtime, "_game_ids_to_sync", AsyncMock(return_value=[game.game_id]))
    monkeypatch.setattr(runtime, "_sync_cached_state", delayed_redis_sync)
    monkeypatch.setattr(runtime, "_publish_after_call", AsyncMock())

    task = asyncio.create_task(
        runtime.mafia_open_day_vote(game_id=game.game_id, expected_round_no=game.round_no)
    )
    try:
        await asyncio.wait_for(sync_entered.wait(), timeout=2)
        assert game.phase == "day_vote"
        original_started_at = game.phase_started_at
        async with backend._lock_game(game.game_id):
            game.phase = "night"
            game.phase_started_at = datetime.now(timezone.utc)
        resume_sync.set()
        transition = await asyncio.wait_for(task, timeout=2)
        current, error = transition
        assert error is None and current is game and current.phase == "night"
        assert ui._phase_version_from_transition(transition, game) == (
            "mafia", "day_vote", 2, original_started_at,
        )
    finally:
        resume_sync.set()
        if not task.done():
            await task


@pytest.mark.asyncio
async def test_quiz_rechecks_phase_after_delayed_persisted_take(monkeypatch):
    game = _active_game(kind="quiz", phase="freeplay")
    game.quiz_current_question_index = 1

    class DelayedStore:
        async def take_quiz_feed_message_id(self, **kwargs):
            # The lock already accepted Q2, but Redis sync let Q3 win.
            game.quiz_current_question_index = 2
            return True, None

        async def get_game(self, game_id):
            return game

    monkeypatch.setattr(ui, "GAME_STORE", DelayedStore())
    bot = SimpleNamespace(send_message=AsyncMock(), delete_message=AsyncMock())
    await ui._sync_quiz_feed_message(
        bot, game, question_no=2, expected_question_index=1,
    )
    bot.send_message.assert_not_awaited()
    bot.delete_message.assert_not_awaited()
