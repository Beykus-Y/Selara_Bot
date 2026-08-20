"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 5: the
"if failed_ids: await _notify_private_delivery_warning(...)" guard was
duplicated at 7 call sites (game start x2, Mafia night re-opens x2, Bunker
reveal advance x3). Extracted to _warn_on_failed_dm; behavior-preserving --
every existing game test still passes unchanged."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


@pytest.mark.asyncio
async def test_warns_when_there_are_failed_ids(monkeypatch) -> None:
    mock = AsyncMock()
    monkeypatch.setattr(game_router, "_notify_private_delivery_warning", mock)
    game = GroupGame(game_id="g1", kind="spy", chat_id=-100, chat_title="chat", owner_user_id=1, players={1: "A"})

    await game_router._warn_on_failed_dm(SimpleNamespace(), game, [2, 3])

    mock.assert_awaited_once_with(SimpleNamespace(), game, [2, 3])


@pytest.mark.asyncio
async def test_no_warning_for_an_empty_failure_list(monkeypatch) -> None:
    mock = AsyncMock()
    monkeypatch.setattr(game_router, "_notify_private_delivery_warning", mock)
    game = GroupGame(game_id="g1", kind="spy", chat_id=-100, chat_title="chat", owner_user_id=1, players={1: "A"})

    await game_router._warn_on_failed_dm(SimpleNamespace(), game, [])

    mock.assert_not_awaited()
