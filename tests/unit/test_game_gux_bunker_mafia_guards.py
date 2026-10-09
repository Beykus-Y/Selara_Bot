"""GUX-10/11: Bunker and Mafia round/turn callback safety regressions."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from selara.presentation.game_state import GameStore, GroupGame

ui = importlib.import_module("selara.presentation.handlers.game.router")


def active(kind: str, phase: str, round_no: int = 3) -> GroupGame:
    return GroupGame(
        game_id="g1011", kind=kind, chat_id=-100, chat_title="Demo",
        owner_user_id=1, players={1: "Alice", 2: "Bob", 3: "Cara"},
        status="started", phase=phase, round_no=round_no,
        alive_player_ids={1, 2, 3},
    )


def put(game: GroupGame) -> GameStore:
    store = GameStore()
    store._by_id[game.game_id] = game
    return store


def callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_bunker_reveal_old_round_and_turn_do_not_disclose_field() -> None:
    game = active("bunker", "bunker_reveal")
    game.bunker_current_actor_user_id = 1
    game.bunker_reveal_cursor = 2
    store = put(game)
    for round_no, cursor, needle in [(2, 2, "предыдущего раунда"), (3, 1, "уже завершён")]:
        _, result, error = await store.bunker_register_reveal(
            game_id=game.game_id, actor_user_id=1, field_key="profession",
            expected_round_no=round_no, expected_reveal_cursor=cursor,
        )
        assert result is None and needle in error
    assert not game.bunker_revealed_fields


@pytest.mark.asyncio
async def test_bunker_vote_old_round_does_not_change_vote() -> None:
    game = active("bunker", "bunker_vote")
    store = put(game)
    _, previous, error = await store.bunker_register_vote(
        game_id=game.game_id, voter_user_id=1, target_user_id=2, expected_round_no=2,
    )
    assert previous is None and "предыдущего раунда" in error
    assert game.bunker_votes == {}
    markup = ui._build_private_bunker_vote_keyboard(game, actor_user_id=1)
    assert markup is not None
    assert "gbkv:g1011:3:2" in callbacks(markup)


@pytest.mark.asyncio
async def test_mafia_old_night_day_and_execution_buttons_do_not_mutate() -> None:
    game = active("mafia", "night", round_no=4)
    store = put(game)
    _, error = await store.mafia_register_night_action(
        game_id=game.game_id, actor_user_id=1, target_user_id=2,
        expected_round_no=3,
    )
    assert "предыдущего раунда" in error
    assert not game.mafia_votes and not game.sheriff_checks
    game.phase = "day_vote"
    _, previous, error = await store.mafia_register_day_vote(
        game_id=game.game_id, voter_user_id=1, target_user_id=2,
        expected_round_no=3,
    )
    assert previous is None and "предыдущего раунда" in error
    assert game.day_votes == {}
    game.phase = "day_execution_confirm"
    _, previous, error = await store.mafia_register_execution_confirm_vote(
        game_id=game.game_id, voter_user_id=1, approve=True,
        expected_round_no=3,
    )
    assert previous is None and "предыдущего раунда" in error
    assert game.execution_confirm_votes == {}


def test_bunker_turn_private_buttons_include_round_and_cursor() -> None:
    game = active("bunker", "bunker_reveal", round_no=5)
    game.bunker_current_actor_user_id = 1
    game.bunker_reveal_cursor = 3
    game.bunker_cards = {1: SimpleNamespace()}
    kb = ui._build_private_bunker_reveal_keyboard(game, actor_user_id=1)
    assert kb is not None
    values = callbacks(kb)
    assert "gbkr:g1011:5:3:noop" in values
    assert all(value.startswith("gbkr:g1011:5:3:") for value in values)
    assert all(len(value.encode()) <= 64 for value in values)
    assert ui._build_private_bunker_reveal_keyboard(game, actor_user_id=2) is None


def test_mafia_execution_buttons_remain_group_visible_and_short() -> None:
    game = active("mafia", "day_execution_confirm", round_no=7)
    kb = ui._build_mafia_execution_confirm_buttons(game)
    assert kb is not None
    values = callbacks(kb)
    assert "gmconfirm:g1011:7:yes" in values
    assert "gmconfirm:g1011:7:no" in values
    assert "gmconfirm:g1011:7:noop" in values
    assert all(len(value.encode()) <= 64 for value in values)


class FakeQuery:
    def __init__(self, data, chat_type="private", chat_id=1):
        self.data = data
        self.from_user = SimpleNamespace(id=1)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(type=chat_type, id=chat_id), message_id=45,
        )
        self.alerts = []

    async def answer(self, text=None, show_alert=False):
        self.alerts.append(text)


@pytest.mark.asyncio
async def test_legacy_bunker_and_mafia_callbacks_are_rejected_without_mutation() -> None:
    checks = [
        (ui.bunker_reveal_callback, "gbkr:g1011:profession"),
        (ui.bunker_vote_callback, "gbkv:g1011:2"),
        (ui.mafia_night_action_callback, "gmact:g1011:2"),
        (ui.mafia_day_vote_callback, "gmvote:g1011:2"),
        (ui.mafia_execution_confirm_callback, "gmconfirm:g1011:yes"),
    ]
    for handler, data in checks:
        q = FakeQuery(data)
        await handler(
            q, bot=SimpleNamespace(), chat_settings=SimpleNamespace(),
            **({"economy_repo": SimpleNamespace()} if handler in (
                ui.bunker_vote_callback, ui.mafia_night_action_callback,
                ui.mafia_day_vote_callback, ui.mafia_execution_confirm_callback
            ) else {}),
        )
        assert q.alerts and "устарел" in q.alerts[-1].lower()

@pytest.mark.asyncio
async def test_stale_mafia_timers_cannot_change_even_same_round_phase() -> None:
    from datetime import datetime, timezone

    old_stamp = datetime(2000, 1, 1, tzinfo=timezone.utc)
    for phase, method_name in (
        ("night", "mafia_resolve_night"),
        ("day_discussion", "mafia_open_day_vote"),
        ("day_vote", "mafia_resolve_day_vote"),
        ("day_execution_confirm", "mafia_resolve_execution_confirm"),
    ):
        game = active("mafia", phase, round_no=9)
        game.phase_started_at = datetime.now(timezone.utc)
        store = put(game)
        method = getattr(store, method_name)
        outcome = await method(
            game_id=game.game_id, expected_round_no=9, expected_phase_started_at=old_stamp,
        )
        assert outcome[-1] == "Таймер предыдущей фазы не применён"
        assert game.phase == phase, method_name

        outcome = await method(
            game_id=game.game_id, expected_round_no=8,
            expected_phase_started_at=game.phase_started_at,
        )
        assert outcome[-1] == "Таймер предыдущего раунда не применён"
        assert game.phase == phase, method_name


def test_bunker_eliminated_player_cannot_get_reveal_or_vote_keyboard() -> None:
    game = active("bunker", "bunker_vote")
    game.alive_player_ids = {1, 2}
    assert ui._build_private_bunker_vote_keyboard(game, actor_user_id=3) is None
    # Full private card is still allowed for a participant, but no actions.
    game.phase = "bunker_reveal"
    game.bunker_current_actor_user_id = 1
    assert ui._build_private_bunker_reveal_keyboard(game, actor_user_id=3) is None
