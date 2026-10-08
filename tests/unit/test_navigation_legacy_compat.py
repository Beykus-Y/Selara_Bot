"""Callbacks and deep links sent before the navigation rework must keep working.

Old /help buttons (help:<key>, with or without the :u<owner> suffix), /start panel
buttons (pm:...), Personal AI and subscription buttons (pai:, premium:) and the
game_/eco_ deep links are all still in chats, so these checks guard the routing
rather than the screen text.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from selara.presentation.handlers import private_panel
from selara.presentation.handlers.help import _resolve_node_key
from selara.presentation.handlers.private_panel import (
    _get_pending_cfg_input,
    _set_pending_cfg_input,
    decode_pm_callback,
    encode_pm_callback,
)
from selara.presentation.navigation.contract import (
    BACK_LABEL,
    CANCEL_LABEL,
    HOME_LABEL,
    MAX_CALLBACK_DATA_BYTES,
    NAV_CALLBACK_PREFIX,
    callback_data_size,
)
from selara.presentation.navigation.tree import ROOT_KEY, get_nav_node


def test_legacy_help_section_keys_resolve_to_new_nodes() -> None:
    assert _resolve_node_key("help:stats") == "profile"
    assert _resolve_node_key("help:relationships") == "couples"
    assert _resolve_node_key("help:ai_plus") == "subscriptions"
    assert _resolve_node_key("help:settings") == "admin_settings"


def test_legacy_help_owner_suffix_is_stripped() -> None:
    assert _resolve_node_key("help:stats:u183270603") == "profile"
    assert _resolve_node_key("help:games:u42") == "games"


def test_existing_keys_open_themselves() -> None:
    assert _resolve_node_key("help:games_roles") == "games_roles"
    assert _resolve_node_key(f"{NAV_CALLBACK_PREFIX}:game_mafia") == "game_mafia"


def test_unknown_or_foreign_help_payloads_open_the_root() -> None:
    assert _resolve_node_key(None) == ROOT_KEY
    assert _resolve_node_key("") == ROOT_KEY
    assert _resolve_node_key("help:no_such_section") == ROOT_KEY
    assert _resolve_node_key("pai:home") == ROOT_KEY
    assert _resolve_node_key(f"{NAV_CALLBACK_PREFIX}:no_such_node") == ROOT_KEY


def test_legacy_help_targets_exist_in_the_tree() -> None:
    for legacy_key in ("profile", "couples", "subscriptions", "ai_models", "admin_settings", "game_bunker"):
        assert get_nav_node(_resolve_node_key(f"help:{legacy_key}")).key == legacy_key


def test_panel_callbacks_round_trip_through_decode() -> None:
    assert decode_pm_callback("pm:h") == ("h", [])
    assert decode_pm_callback("pm:help") == ("help", [])
    assert decode_pm_callback("pm:sub") == ("sub", [])
    assert decode_pm_callback(encode_pm_callback("ai", -1001234567890, 3)) == ("ai", ["-1001234567890", "3"])


def test_non_panel_callbacks_are_not_decoded_as_panel_routes() -> None:
    assert decode_pm_callback("pai:home") is None
    assert decode_pm_callback("premium:self") is None
    assert decode_pm_callback("help:stats") is None


def test_worst_case_panel_callbacks_fit_telegram_limit() -> None:
    worst_chat_id = -1009999999999999
    samples = [
        encode_pm_callback("ai", worst_chat_id, 99, 99),
        encode_pm_callback("ari"),
        encode_pm_callback("rlc", worst_chat_id),
        encode_pm_callback("ul", 9999),
    ]
    for data in samples:
        assert callback_data_size(data) <= MAX_CALLBACK_DATA_BYTES, data


def test_navigation_prefix_does_not_collide_with_legacy_prefixes() -> None:
    legacy_prefixes = {"pm", "help", "pai", "premium", "game", "eco"}
    assert NAV_CALLBACK_PREFIX not in legacy_prefixes


def test_shared_button_labels_are_stable() -> None:
    assert BACK_LABEL == "⬅️ Назад"
    assert HOME_LABEL == "🏠 Главное"
    assert CANCEL_LABEL == "❌ Отмена"


async def test_any_panel_button_abandons_a_waiting_text_prompt(monkeypatch) -> None:
    user_id = 9_100_001
    _set_pending_cfg_input(user_id=user_id, chat_id=-100500, key=next(iter(private_panel.CHAT_SETTINGS_KEYS)))
    assert _get_pending_cfg_input(user_id) is not None

    send_help = AsyncMock()
    monkeypatch.setattr(private_panel, "send_help", send_help)

    query = MagicMock()
    query.from_user.id = user_id
    query.data = encode_pm_callback("help")
    query.message.chat.type = "private"
    query.answer = AsyncMock()

    await private_panel.private_panel_callback(
        query,
        activity_repo=None,
        economy_repo=None,
        settings=MagicMock(),
        session_factory=None,
        personal_config=None,
    )

    send_help.assert_awaited_once()
    assert _get_pending_cfg_input(user_id) is None
