from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from selara.core.config import Settings
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.commands.command_catalog import GAME_RULES_RU, get_command_spec
from selara.presentation.game_state import GAME_LAUNCHABLE_KINDS
from selara.presentation.handlers import help as help_module
from selara.presentation.handlers.help import (
    _LEGACY_HELP_KEYS,
    _node_keyboard,
    _policy_limit,
    _render_screen,
    _resolve_node_key,
    help_callback,
)
from selara.presentation.navigation.contract import BACK_LABEL, SECTIONS_LABEL
from selara.presentation.navigation.tree import (
    NAV_NODES,
    ROOT_KEY,
    nav_callback,
    root_node,
)

_TELEGRAM_LIMIT = 4096


def _settings() -> Settings:
    return Settings(
        BOT_TOKEN="token",
        DATABASE_URL="sqlite+aiosqlite:///tmp/test.db",
    )


def _callbacks(keyboard) -> list[str]:
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


def _screen(key: str) -> tuple[str, list[str]]:
    pages, keyboard = _render_screen(_settings(), key)
    return "\n".join(pages), _callbacks(keyboard)


def _callback_query(*, data: str, edit_side_effect: Exception | None = None) -> SimpleNamespace:
    message = SimpleNamespace(edit_text=AsyncMock(side_effect=edit_side_effect), answer=AsyncMock())
    return SimpleNamespace(data=data, message=message, from_user=SimpleNamespace(id=1), answer=AsyncMock())


def test_root_offers_the_eight_areas_and_no_back_button() -> None:
    text, callbacks = _screen(ROOT_KEY)
    expected = [nav_callback(key) for key in root_node().children]
    assert callbacks == expected
    assert "Возможности Selara" in text
    assert BACK_LABEL not in text


@pytest.mark.parametrize("node", [node for node in NAV_NODES if node.parent is not None], ids=lambda node: node.key)
def test_every_inner_screen_has_back_and_sections_buttons(node) -> None:
    keyboard = _node_keyboard(node)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = _callbacks(keyboard)
    assert nav_callback(node.parent) in callbacks
    assert labels.count(BACK_LABEL) == 1
    if node.parent != ROOT_KEY:
        assert nav_callback(ROOT_KEY) in callbacks
        assert SECTIONS_LABEL in labels


@pytest.mark.parametrize("node", NAV_NODES, ids=lambda node: node.key)
def test_every_screen_fits_one_telegram_message_and_has_no_button_wall(node) -> None:
    pages, keyboard = _render_screen(_settings(), node.key)
    assert pages, node.key
    for page in pages:
        assert len(page) < _TELEGRAM_LIMIT, node.key
    child_callbacks = {nav_callback(key) for key in node.children}
    child_rows = [[button for button in row if button.callback_data in child_callbacks] for row in keyboard.inline_keyboard]
    child_rows = [row for row in child_rows if row]
    assert sum(len(row) for row in child_rows) == len(node.children)
    assert len(node.children) <= 8
    assert all(len(row) <= 2 for row in child_rows)


@pytest.mark.parametrize("node", NAV_NODES, ids=lambda node: node.key)
def test_every_command_on_a_screen_is_visible_by_title(node) -> None:
    text, _ = _screen(node.key)
    for spec_key in node.spec_keys:
        assert escape(get_command_spec(spec_key).title_ru) in text, f"{node.key}: {spec_key} is not shown"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("help:home", ROOT_KEY),
        ("help:", ROOT_KEY),
        ("help::u5", ROOT_KEY),
        ("help:unknown", ROOT_KEY),
        ("help:economy", "economy"),
        ("help:economy:u999", "economy"),
        ("help:stats", "profile"),
        ("help:relationships:u7", "couples"),
        ("help:ai", "ai_group"),
        ("help:ai_plus", "subscriptions"),
        ("help:models", "ai_models"),
        ("help:settings", "admin_settings"),
        ("help:game_mafia", "game_mafia"),
        ("help:game_mafia:u123", "game_mafia"),
        ("nv:games_quick", "games_quick"),
        ("nv:no_such_node", ROOT_KEY),
        ("nv:", ROOT_KEY),
        ("pm:h", ROOT_KEY),
        (None, ROOT_KEY),
    ],
)
def test_callback_payloads_resolve_to_nodes(data, expected) -> None:
    assert _resolve_node_key(data) == expected


def test_every_legacy_help_key_points_at_a_real_node() -> None:
    node_keys = {node.key for node in NAV_NODES}
    assert set(_LEGACY_HELP_KEYS.values()) <= node_keys


def test_every_screen_callback_fits_telegram_limit() -> None:
    for node in NAV_NODES:
        _, callbacks = _screen(node.key)
        assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks), node.key


def test_games_menu_reaches_every_launchable_game_with_rules() -> None:
    # Regression guard: every launchable game kind must have a screen with its rules.
    node_keys = {node.key for node in NAV_NODES}
    for kind in GAME_LAUNCHABLE_KINDS:
        assert f"game_{kind}" in node_keys, f"{kind}: launchable but missing from the /help games menu"
        text, _ = _screen(f"game_{kind}")
        assert "Правила" in text, f"{kind}: /help game detail text has no rules"


def test_game_screens_show_the_full_rules_text_without_its_duplicate_title() -> None:
    for kind, rules in GAME_RULES_RU.items():
        text, _ = _screen(f"game_{kind}")
        assert rules.split("\n", 1)[1].split("\n", 1)[0] in text


def test_games_screen_offers_group_pickers_and_lobby_commands() -> None:
    text, callbacks = _screen("games")
    assert nav_callback("games_roles") in callbacks
    assert nav_callback("games_quick") in callbacks
    assert nav_callback("gacha") in callbacks
    assert "Выберите группу игр" in text


def test_game_group_screens_list_their_games() -> None:
    _, roles = _screen("games_roles")
    _, quick = _screen("games_quick")
    assert {nav_callback(f"game_{k}") for k in ("spy", "whoami", "mafia", "bunker")} <= set(roles)
    assert {nav_callback(f"game_{k}") for k in ("zlobcards", "dice", "quiz", "bredovukha")} <= set(quick)


def test_ai_group_section_documents_assistant_call_names_and_limits() -> None:
    text, _ = _screen("ai_group")
    for fragment in ("? вопрос", "?? вопрос", "?reset", "/selara кличка", "llm_enabled", f"{_policy_limit(AiFeature.LLM_ADMIN, 'telegram_message')} запросов в сутки"):
        assert fragment in text, fragment
    settings = _settings()
    assert f"{settings.group_member_free_daily_limit} на чат" in text
    assert f"{settings.group_member_paid_daily_limit}" in text


def test_subscription_section_documents_summary_autocfg_and_grants() -> None:
    text, _ = _screen("subscriptions")
    for fragment in ("/premium", "/summary", "daily_summary_enabled", "/autocfg", "/autocfgcancel", f"{_policy_limit(AiFeature.DAILY_SUMMARY, 'manual')} раз в месяц", "выдана администратором"):
        assert fragment in text, fragment


def test_models_section_lists_profiles_limit_modes_and_grants() -> None:
    text, _ = _screen("ai_models")
    for fragment in ("/ai", "Базовая", "Аналитик", "Быстрая", "AI Limits", "AIL"):
        assert fragment in text, fragment


def test_pets_section_covers_custom_actions() -> None:
    settings = _settings()
    pets, _ = _screen("pets")
    assert "/pet_do" in pets and "/pet_traits" in pets and "/pet_memory" in pets
    group, _ = _screen("ai_group")
    assert f"{settings.pet_custom_actions_daily_limit} в сутки" in group


def test_profile_section_points_to_mini_app_and_pc_login() -> None:
    text, _ = _screen("profile")
    assert "Mini App" in text and "/login" in text


def test_settings_section_keeps_admin_commands_without_the_app_lines() -> None:
    text, _ = _screen("admin_settings")
    assert "/selara" in text and "/premium" in text and "/terms" in text
    assert "Mini App" not in text


async def test_legacy_home_callback_opens_the_catalog_root() -> None:
    query = _callback_query(data="help:home")

    await help_callback(query, _settings())

    text = query.message.edit_text.await_args.args[0]
    assert "Возможности Selara" in text


async def test_section_is_readable_by_a_group_member_who_did_not_open_it() -> None:
    # /help is public in groups: another participant pressing a card's button
    # gets the section, not an "other user's menu" refusal.
    query = _callback_query(data="help:economy:u999")

    await help_callback(query, _settings())

    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "Экономика" in text
    query.answer.assert_awaited_once_with()


async def test_callback_keyboard_carries_no_owner_suffix() -> None:
    query = _callback_query(data="nv:games")

    await help_callback(query, _settings())

    keyboard = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = _callbacks(keyboard)
    assert nav_callback("games_roles") in callbacks
    assert all(":u" not in callback for callback in callbacks)


async def test_callback_treats_not_modified_as_success() -> None:
    error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: message is not modified")
    query = _callback_query(data="help:economy", edit_side_effect=error)

    await help_callback(query, _settings())

    query.answer.assert_awaited_once_with()


async def test_callback_reraises_other_edit_errors() -> None:
    error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: message to edit not found")
    query = _callback_query(data="help:economy", edit_side_effect=error)

    with pytest.raises(TelegramBadRequest, match="message to edit not found"):
        await help_callback(query, _settings())


async def test_callback_without_message_only_acknowledges() -> None:
    query = SimpleNamespace(data="help:economy", message=None, from_user=SimpleNamespace(id=1), answer=AsyncMock())

    await help_callback(query, _settings())

    query.answer.assert_awaited_once_with()


async def test_long_help_section_paginates_by_edit_without_sending_duplicate_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(help_module, "_node_text", lambda settings, node: "\n".join(["строка"] * 1500))
    query = _callback_query(data="nv:economy")

    await help_callback(query, _settings())

    first_edit = query.message.edit_text.await_args
    first_markup = first_edit.kwargs["reply_markup"]
    next_buttons = [
        button for row in first_markup.inline_keyboard for button in row
        if button.callback_data == "nvp:economy:1"
    ]
    assert len(next_buttons) == 1
    assert len(first_edit.args[0]) <= _TELEGRAM_LIMIT
    query.message.answer.assert_not_awaited()

    # A repeated click on the original menu remains a single edit.
    await help_callback(query, _settings())
    query.message.answer.assert_not_awaited()

    # Following "next" opens page 2 on the same message with a working back action.
    query.data = "nvp:economy:1"
    await help_callback(query, _settings())
    page2 = query.message.edit_text.await_args
    assert len(page2.args[0]) <= _TELEGRAM_LIMIT
    callbacks = _callbacks(page2.kwargs["reply_markup"])
    assert "nvp:economy:0" in callbacks
    assert "nvp:economy:2" in callbacks
    query.message.answer.assert_not_awaited()


async def test_malformed_pagination_callback_falls_back_to_root() -> None:
    query = _callback_query(data="nvp:no-such-node:999")
    await help_callback(query, _settings())
    assert "Возможности Selara" in query.message.edit_text.await_args.args[0]
    query.message.answer.assert_not_awaited()


async def test_send_help_puts_keyboard_on_the_only_message() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    await help_module.send_help(message, _settings())

    message.answer.assert_awaited_once()
    assert message.answer.await_args.kwargs["reply_markup"] is not None
    assert "Возможности Selara" in message.answer.await_args.args[0]
