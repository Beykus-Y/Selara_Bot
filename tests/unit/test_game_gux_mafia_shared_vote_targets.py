"""GUX-11 regression: shared Mafia day-vote actions must not invite invalid targets."""
from __future__ import annotations

import importlib

from selara.presentation.game_state import GroupGame

ui = importlib.import_module("selara.presentation.handlers.game.router")


def make_game(count: int, *, immune: int | None = None) -> GroupGame:
    return GroupGame(
        game_id="maf-gux", kind="mafia", chat_id=-100, chat_title="Mafia",
        owner_user_id=1,
        players={uid: f"User{uid}" for uid in range(1, count + 1)},
        alive_player_ids=set(range(1, count + 1)),
        status="started", phase="day_vote", round_no=3,
        day_vote_immune_user_id=immune,
    )


def callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_mafia_group_vote_hides_protected_player_for_everyone() -> None:
    for count in (4, 6, 10):
        game = make_game(count, immune=2)
        kb = ui._build_mafia_day_vote_buttons(game)
        assert kb is not None
        items = callbacks(kb)
        assert f"gmvote:maf-gux:3:2" not in items
        assert len(items) == count - 1
        assert all(len(value.encode("utf-8")) <= 64 for value in items)
        assert f"gmvote:maf-gux:3:1" in items  # shared menu is not personalized
        for viewer in (1, 3):
            own = ui._build_private_day_vote_keyboard(game, actor_user_id=viewer)
            assert own is not None
            choices = callbacks(own)
            assert f"gmvote:maf-gux:3:{viewer}" not in choices
            assert f"gmvote:maf-gux:3:2" not in choices


def test_mafia_group_vote_without_protection_preserves_existing_targets() -> None:
    game = make_game(4)
    kb = ui._build_mafia_day_vote_buttons(game)
    assert kb is not None
    assert len(callbacks(kb)) == 4


def test_mafia_vote_board_warns_about_self_and_protected_targets() -> None:
    from selara.core.chat_settings import default_chat_settings
    from selara.core.config import Settings

    settings = Settings.model_validate({
        "BOT_TOKEN": "123456:TEST",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        "BOT_USERNAME": "selara_test_bot",
        "WEB_AUTH_SECRET": "secret",
    })
    game = make_game(6, immune=2)
    board = ui._render_game_text(game, default_chat_settings(settings))
    assert "За себя голосовать нельзя" in board
    assert "Защищённая цель недоступна" in board
