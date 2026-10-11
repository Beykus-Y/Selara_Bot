"""PR #253: manager actions are fenced after awaits; rules cannot flood a group."""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from selara.presentation.game_state import GameStore, GroupGame

ui = importlib.import_module("selara.presentation.handlers.game.router")


def _game(*, phase: str = "day_discussion", kind: str = "mafia") -> GroupGame:
    return GroupGame(
        game_id="g253", kind=kind, chat_id=-100, chat_title="Test",
        owner_user_id=1, players={1: "Alice", 2: "Bob", 3: "Cara"},
        status="started", phase=phase, round_no=3,
        phase_started_at=datetime.now(timezone.utc),
        alive_player_ids={1, 2, 3},
    )


class _Query:
    def __init__(self, data: str):
        self.data = data
        self.from_user = SimpleNamespace(
            id=1, username="alice", first_name="Alice", last_name=None, is_bot=False,
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup", title="Test"),
            message_id=45,
        )
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _store(monkeypatch, game: GroupGame) -> GameStore:
    store = GameStore()
    store._by_id[game.game_id] = game
    monkeypatch.setattr(ui, "GAME_STORE", store)
    monkeypatch.setattr(ui, "_refresh_game_player_label", AsyncMock(return_value="Alice"))
    monkeypatch.setattr(ui, "_safe_edit_or_send_game_board", AsyncMock())
    return store


@pytest.mark.asyncio
async def test_manager_click_frozen_during_permission_lookup_cannot_advance_next_phase(monkeypatch):
    game = _game(phase="night")
    store = _store(monkeypatch, game)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def _wait_for_permission(*args, **kwargs):
        entered.set()
        await resume.wait()
        return True

    monkeypatch.setattr(ui, "_actor_can_manage_games", _wait_for_permission)
    cancel = Mock()
    unexpected_next_phase = AsyncMock()
    monkeypatch.setattr(ui, "_cancel_phase_timer", cancel)
    monkeypatch.setattr(ui, "_open_mafia_day_vote", unexpected_next_phase)

    query = _Query("game:adv:g253:night:3:0")
    task = asyncio.create_task(ui.game_callback(
        query, bot=SimpleNamespace(), chat_settings=SimpleNamespace(),
        activity_repo=SimpleNamespace(), economy_repo=SimpleNamespace(),
    ))
    await asyncio.wait_for(entered.wait(), timeout=2)
    # Simulates another authorized callback or the timer winning the GameStore lock.
    async with store._lock_game(game.game_id):
        game.phase = "day_discussion"
        game.phase_started_at = datetime.now(timezone.utc)
    resume.set()
    await asyncio.wait_for(task, timeout=2)

    assert game.phase == "day_discussion"
    unexpected_next_phase.assert_not_awaited()
    cancel.assert_not_called()
    assert query.answers and "устарело" in (query.answers[-1][0] or "").lower()


@pytest.mark.asyncio
async def test_duplicate_manager_callbacks_enter_gamestore_only_once(monkeypatch):
    game = _game()
    _store(monkeypatch, game)
    entered, resume = asyncio.Event(), asyncio.Event()
    waiting = 0

    async def _permission(*args, **kwargs):
        nonlocal waiting
        waiting += 1
        if waiting == 2:
            entered.set()
        await resume.wait()
        return True

    monkeypatch.setattr(ui, "_actor_can_manage_games", _permission)
    monkeypatch.setattr(ui, "_notify_mafia_day_vote_private", AsyncMock())
    schedule = Mock()
    cancel = Mock()
    monkeypatch.setattr(ui, "_schedule_phase_timer", schedule)
    monkeypatch.setattr(ui, "_cancel_phase_timer", cancel)

    first = _Query("game:adv:g253:day_discussion:3:0")
    second = _Query("game:adv:g253:day_discussion:3:0")
    tasks = [
        asyncio.create_task(ui.game_callback(
            q, bot=SimpleNamespace(), chat_settings=SimpleNamespace(),
            activity_repo=SimpleNamespace(), economy_repo=SimpleNamespace(),
        )) for q in (first, second)
    ]
    await asyncio.wait_for(entered.wait(), timeout=2)
    resume.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert game.phase == "day_vote"
    assert sum("Обсуждение завершено" in (q.answers[-1][0] or "") for q in (first, second)) == 1
    assert sum("устарело" in (q.answers[-1][0] or "").lower() for q in (first, second)) == 1
    schedule.assert_called_once()
    cancel.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,phase,method,extra", [
    ("bredovukha", "category_pick", "bred_force_pick_category", {}),
    ("bredovukha", "private_answers", "bred_open_vote", {"force": True}),
    ("bredovukha", "public_vote", "bred_resolve_round", {"force": True}),
    ("bunker", "bunker_vote", "bunker_resolve_vote", {"force": True}),
    ("zlobcards", "public_vote", "zlob_resolve_round", {"force": True}),
])
async def test_stale_manager_round_is_rejected_inside_store(monkeypatch, kind, phase, method, extra):
    game = _game(kind=kind, phase=phase)
    store = _store(monkeypatch, game)
    result = await getattr(store, method)(
        game_id=game.game_id, expected_round_no=game.round_no - 1, **extra,
    )
    assert result[-1] and "устарело" in result[-1]
    assert game.phase == phase and game.round_no == 3


@pytest.mark.asyncio
async def test_rules_button_limits_concurrent_group_messages_and_allows_retry(monkeypatch):
    game = _game(phase="day_vote")
    _store(monkeypatch, game)
    ui._GAME_RULES_LAST_SENT.clear()
    current = [500.0]
    monkeypatch.setattr(ui, "monotonic", lambda: current[0])
    bot = SimpleNamespace(send_message=AsyncMock())
    try:
        first, second = _Query("game:lrules:g253"), _Query("game:lrules:g253")
        await asyncio.gather(*(
            ui.game_callback(
                query, bot=bot, chat_settings=SimpleNamespace(),
                activity_repo=SimpleNamespace(), economy_repo=SimpleNamespace(),
            ) for query in (first, second)
        ))
        assert bot.send_message.await_count == 1
        assert any("уже отправлены" in (q.answers[-1][0] or "") for q in (first, second))

        current[0] += ui._GAME_RULES_COOLDOWN_SECONDS + 1
        await ui.game_callback(
            _Query("game:lrules:g253"), bot=bot,
            chat_settings=SimpleNamespace(), activity_repo=SimpleNamespace(), economy_repo=SimpleNamespace(),
        )
        assert bot.send_message.await_count == 2
    finally:
        ui._GAME_RULES_LAST_SENT.clear()
