"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 1 (shared
navigation foundation): the game catalog moves from "one long vertical list of
buttons" to a paginated catalog (3-5 games/page) -> game detail card -> rules,
all via editing the same message, with "back" returning to the page the user
came from rather than always page 1."""
from __future__ import annotations

import importlib

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_catalog_page_size_is_within_ilyas_3_to_5_range() -> None:
    assert 3 <= game_router._GAME_CATALOG_PAGE_SIZE <= 5


def test_catalog_kinds_for_page_slices_launchable_kinds() -> None:
    page_size = game_router._GAME_CATALOG_PAGE_SIZE
    first_page = game_router._catalog_kinds_for_page(0)
    assert first_page == game_router.GAME_LAUNCHABLE_KINDS[:page_size]


def test_catalog_page_count_covers_every_launchable_kind() -> None:
    page_size = game_router._GAME_CATALOG_PAGE_SIZE
    total_kinds = len(game_router.GAME_LAUNCHABLE_KINDS)
    page_count = game_router._catalog_page_count()
    assert page_count == -(-total_kinds // page_size)  # ceil division
    covered = sum(len(game_router._catalog_kinds_for_page(p)) for p in range(page_count))
    assert covered == total_kinds


def test_catalog_keyboard_buttons_open_detail_not_create_directly() -> None:
    # Core interaction-pattern shift: tapping a game in the catalog must show
    # its detail card, not immediately create a lobby.
    markup = game_router._build_game_catalog_keyboard(requester_user_id=123, page=0)
    callbacks = _callbacks(markup)
    for kind in game_router._catalog_kinds_for_page(0):
        assert f"game:detail:{kind}:0:u123" in callbacks
    assert not any(cb.startswith("game:new:") for cb in callbacks)


def test_catalog_keyboard_first_page_has_no_back_arrow() -> None:
    markup = game_router._build_game_catalog_keyboard(requester_user_id=123, page=0)
    callbacks = _callbacks(markup)
    assert not any(cb == "game:list:-1:u123" for cb in callbacks)
    assert "game:list:1:u123" in callbacks  # forward arrow present (2 pages at current count)


def test_catalog_keyboard_last_page_has_no_forward_arrow() -> None:
    last_page = game_router._catalog_page_count() - 1
    markup = game_router._build_game_catalog_keyboard(requester_user_id=123, page=last_page)
    callbacks = _callbacks(markup)
    assert not any(cb == f"game:list:{last_page + 1}:u123" for cb in callbacks)
    if last_page > 0:
        assert f"game:list:{last_page - 1}:u123" in callbacks


def test_catalog_keyboard_all_buttons_bound_to_requester() -> None:
    markup = game_router._build_game_catalog_keyboard(requester_user_id=42, page=0)
    callbacks = _callbacks(markup)
    assert callbacks
    assert all(cb.endswith(":u42") for cb in callbacks)


def test_detail_keyboard_has_create_rules_and_back_buttons() -> None:
    markup = game_router._build_game_detail_keyboard(kind="spy", page=1, requester_user_id=7)
    callbacks = _callbacks(markup)
    assert "game:new:spy:u7" in callbacks
    assert "game:rules:spy:1:u7" in callbacks
    assert "game:list:1:u7" in callbacks


def test_detail_text_surfaces_title_min_players_and_description() -> None:
    text = game_router._render_game_detail_text("spy")
    definition = game_router.GAME_DEFINITIONS["spy"]
    assert definition.title in text
    assert str(definition.min_players) in text
    assert definition.short_description in text


def test_detail_text_notes_dm_requirement_only_for_secret_role_games() -> None:
    spy_text = game_router._render_game_detail_text("spy")  # secret_roles=True
    dice_text = game_router._render_game_detail_text("dice")  # secret_roles=False
    assert "ЛС" in spy_text
    assert "ЛС" not in dice_text


def test_rules_keyboard_back_returns_to_detail_not_list() -> None:
    markup = game_router._build_game_rules_keyboard(kind="mafia", page=0, requester_user_id=9)
    callbacks = _callbacks(markup)
    assert "game:detail:mafia:0:u9" in callbacks


def test_every_launchable_kind_has_nonempty_rules_text() -> None:
    for kind in game_router.GAME_LAUNCHABLE_KINDS:
        text = game_router._render_game_rules_text(kind)
        assert text.strip(), f"missing rules text for {kind!r}"
