from selara.presentation.handlers.game.router import _build_game_selection_keyboard


def test_game_selection_keyboard_binds_requester_user_id() -> None:
    keyboard = _build_game_selection_keyboard(requester_user_id=123)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks
    assert all(callback is not None and callback.endswith(":u123") for callback in callbacks)
    assert "game:new:bunker:u123" in callbacks
