from selara.presentation.game_state import GameKind, GroupGame
from selara.presentation.handlers.game.router import (
    _should_handle_bred_private_answer,
    _should_handle_number_guess,
    _should_handle_whoami_group_text,
)


def _base_game(kind: GameKind) -> GroupGame:
    return GroupGame(
        game_id="g1",
        kind=kind,
        chat_id=-100,
        chat_title="test",
        owner_user_id=1,
        players={1: "u1", 2: "u2"},
        status="started",
    )


def test_whoami_group_text_skips_when_no_active_game() -> None:
    assert _should_handle_whoami_group_text(None, user_id=1, text="кто я") is False


def test_whoami_group_text_handles_only_current_actor_guess_or_question() -> None:
    game = _base_game("whoami")
    game.phase = "whoami_ask"
    game.whoami_current_actor_user_id = 1

    assert _should_handle_whoami_group_text(game, user_id=1, text="Я думаю, что я кот") is True
    assert _should_handle_whoami_group_text(game, user_id=1, text="Я животное?") is True
    assert _should_handle_whoami_group_text(game, user_id=1, text="кто я") is False
    assert _should_handle_whoami_group_text(game, user_id=2, text="Я животное?") is False


def test_number_guess_handler_only_captures_active_number_game() -> None:
    assert _should_handle_number_guess(None) is False

    whoami = _base_game("whoami")
    assert _should_handle_number_guess(whoami) is False

    number = _base_game("number")
    assert _should_handle_number_guess(number) is True


def test_bred_private_answer_skips_commands_and_missing_game() -> None:
    assert _should_handle_bred_private_answer(None, text="роль") is False
    assert _should_handle_bred_private_answer(None, text="/start") is False

    bred = _base_game("bredovukha")
    assert _should_handle_bred_private_answer(bred, text="мой ответ") is True
