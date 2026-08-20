from selara.presentation.handlers.game.router import _build_game_catalog_keyboard


def test_game_catalog_keyboard_binds_requester_user_id() -> None:
    # Stage 1 of docs/GAMES_UX_MODERNIZATION_TODO.md: the catalog no longer
    # jumps straight to "game:new:" -- tapping a game opens its detail card
    # first (see test_game_catalog_pagination.py / test_game_catalog_navigation.py
    # for the full paginated-catalog contract).
    keyboard = _build_game_catalog_keyboard(requester_user_id=123, page=0)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks
    assert all(callback is not None and callback.endswith(":u123") for callback in callbacks)
    assert "game:detail:mafia:0:u123" in callbacks
