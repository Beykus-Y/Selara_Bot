"""GUX-15 automation for Bunker private keyboards at 6, 8 and 12 players.

Replaces the manual Telegram layout check for these keyboards with asserts on
button count, row shape, callback size and uniqueness. Real iOS/Android
rendering is not automated and is documented as such in
docs/GAME_UX_BASELINE.md.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from selara.presentation.game_state import BUNKER_CARD_FIELDS, GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _bunker_game(player_count: int, *, phase: str, long_names: bool = False) -> GroupGame:
    names = {
        uid: (f"Очень-очень длинное-имя-игрока-{uid}" * 2 if long_names else f"Player{uid}")
        for uid in range(1, player_count + 1)
    }
    return GroupGame(
        game_id="g1",
        kind="bunker",
        chat_id=-100,
        chat_title="chat",
        owner_user_id=1,
        players=names,
        status="started",
        phase=phase,
        round_no=2,
        alive_player_ids=set(names),
        bunker_seats=max(1, player_count // 2),
        bunker_current_actor_user_id=1,
        bunker_reveal_cursor=4,
        bunker_cards={1: SimpleNamespace()},
        bunker_revealed_fields={1: set()},
    )


def _rows(markup) -> list[list[str]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_bunker_has_nine_characteristics() -> None:
    assert len(BUNKER_CARD_FIELDS) == 9


@pytest.mark.parametrize("player_count", [6, 8, 12])
def test_reveal_keyboard_offers_each_hidden_field_once_on_its_own_row(player_count: int) -> None:
    game = _bunker_game(player_count, phase="bunker_reveal")

    markup = game_router._build_private_bunker_reveal_keyboard(game, actor_user_id=1)

    rows = _rows(markup)
    assert len(rows) == len(BUNKER_CARD_FIELDS) + 1  # nine fields plus refresh
    assert all(len(row) == 1 for row in rows)
    values = [row[0] for row in rows]
    assert len(set(values)) == len(values)
    assert all(len(value.encode("utf-8")) <= 64 for value in values)
    assert values[-1] == "gbkr:g1:2:4:noop"


@pytest.mark.parametrize("player_count", [6, 8, 12])
def test_reveal_keyboard_drops_already_revealed_fields(player_count: int) -> None:
    game = _bunker_game(player_count, phase="bunker_reveal")
    game.bunker_revealed_fields = {1: set(BUNKER_CARD_FIELDS[:3])}

    markup = game_router._build_private_bunker_reveal_keyboard(game, actor_user_id=1)

    rows = _rows(markup)
    assert len(rows) == (len(BUNKER_CARD_FIELDS) - 3) + 1
    for field_key in BUNKER_CARD_FIELDS[:3]:
        assert all(f":{field_key}" not in row[0] for row in rows)


@pytest.mark.parametrize("player_count", [6, 8, 12])
def test_vote_keyboard_lists_every_other_alive_player_once(player_count: int) -> None:
    game = _bunker_game(player_count, phase="bunker_vote", long_names=True)

    markup = game_router._build_private_bunker_vote_keyboard(game, actor_user_id=1)

    rows = _rows(markup)
    assert len(rows) == (player_count - 1) + 1  # every other player plus refresh
    assert all(len(row) == 1 for row in rows)
    values = [row[0] for row in rows]
    assert len(set(values)) == len(values)
    assert all(len(value.encode("utf-8")) <= 64 for value in values)
    target_ids = {int(value.split(":")[3]) for value in values[:-1]}
    assert target_ids == set(range(2, player_count + 1))
    assert values[-1] == "gbkv:g1:2:noop"


@pytest.mark.parametrize("player_count", [6, 8, 12])
def test_vote_keyboard_labels_are_truncated_for_long_names(player_count: int) -> None:
    game = _bunker_game(player_count, phase="bunker_vote", long_names=True)

    markup = game_router._build_private_bunker_vote_keyboard(game, actor_user_id=1)

    labels = [button.text for row in markup.inline_keyboard for button in row][:-1]
    # Icon, space and at most 24 visible characters of name.
    assert all(len(label) <= 2 + 24 for label in labels)
    assert all(label.endswith("...") for label in labels)


@pytest.mark.parametrize("player_count", [6, 8, 12])
def test_vote_keyboard_marks_current_choice_and_keeps_others_neutral(player_count: int) -> None:
    game = _bunker_game(player_count, phase="bunker_vote")
    game.bunker_votes = {1: 2}

    markup = game_router._build_private_bunker_vote_keyboard(game, actor_user_id=1)

    labels = [button.text for row in markup.inline_keyboard for button in row][:-1]
    assert labels[0].startswith("✅ ")
    assert all(label.startswith("🗳 ") for label in labels[1:])
