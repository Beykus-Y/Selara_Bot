"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3: every
private-phase DM button on the group board must carry the `?start=game_{id}`
deep-link payload so tapping it opens the bot with the right game context
auto-shown, instead of a bare DM the player then has to figure out on their
own. The original audit found Bredovukha was the one exception."""
from __future__ import annotations

import importlib

from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def test_bredovukha_private_answers_dm_button_carries_the_game_deep_link() -> None:
    game = GroupGame(
        game_id="bredg1",
        kind="bredovukha",
        chat_id=-100,
        chat_title="chat",
        owner_user_id=1,
        players={1: "owner", 2: "u2", 3: "u3"},
        status="started",
        phase="private_answers",
    )
    markup = game_router._build_game_controls(game=game, bot_username="selara_test_bot")
    assert markup is not None
    urls = [button.url for row in markup.inline_keyboard for button in row if button.url]
    assert "https://t.me/selara_test_bot?start=game_bredg1" in urls


def test_every_private_phase_dm_button_carries_the_game_deep_link() -> None:
    # Every other kind already does this (confirmed by the original audit) --
    # this is a structural guard so a future private-phase button can't
    # silently regress to a bare DM link the way bredovukha's did.
    cases = [
        ("spy", "freeplay"),
        ("mafia", "night"),
        ("whoami", "whoami_ask"),
        ("bredovukha", "private_answers"),
        ("zlobcards", "private_answers"),
        ("bunker", "bunker_reveal"),
    ]
    for kind, phase in cases:
        game = GroupGame(
            game_id=f"g-{kind}",
            kind=kind,
            chat_id=-100,
            chat_title="chat",
            owner_user_id=1,
            players={1: "owner", 2: "u2", 3: "u3"},
            status="started",
            phase=phase,
        )
        markup = game_router._build_game_controls(game=game, bot_username="selara_test_bot")
        assert markup is not None, kind
        urls = [button.url for row in markup.inline_keyboard for button in row if button.url]
        assert any(url and "?start=game_" in url for url in urls), f"{kind}: no deep-link DM button found ({urls})"
