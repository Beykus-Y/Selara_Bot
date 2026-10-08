"""Tests for the /start home screen (#184 PR-D).

The home screen is the first thing a user sees in a private chat. These tests
pin three things: the plain-language intro, the feature buttons (Personal AI,
help catalog, subscriptions, add-to-group, groups) with their state rules, and
that every route that shows home renders it through one helper, so the
buttons cannot drift between /start, refresh and the cancel paths.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import WebAppInfo

from selara.core.config import Settings
from selara.presentation.handlers import personal_ai
from selara.presentation.handlers.private_panel import (
    _build_getting_started_url,
    _build_home_keyboard,
    _build_startgroup_url,
    _get_pending_admin_input,
    _get_pending_cfg_input,
    _render_home_screen,
    _render_home_text,
    _reset_home_pending,
    _set_pending_admin_input,
    _set_pending_cfg_input,
    send_private_start_panel,
)

_PRIVATE_PANEL_SOURCE = Path(__file__).resolve().parents[2] / "src/selara/presentation/handlers/private_panel.py"
_STARTGROUP_URL = "https://t.me/selara_test_bot?startgroup=true"


def _settings(**overrides) -> Settings:
    base = {
        "BOT_TOKEN": "123456:TEST",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        "BOT_USERNAME": "selara_test_bot",
        "WEB_ENABLED": True,
        "WEB_BASE_URL": "https://selara.example",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def _user(user_id: int = 1):
    return SimpleNamespace(id=user_id, username="ilya", first_name="Ilya", last_name=None, is_bot=False)


def _buttons(markup) -> list:
    return [button for row in markup.inline_keyboard for button in row]


def _find(markup, text: str):
    matches = [button for button in _buttons(markup) if button.text == text]
    assert matches, f"no button named {text!r}"
    return matches[0]


class _FakeActivityRepo:
    def __init__(self, *, admin=(), activity=(), fail: bool = False) -> None:
        self._admin = list(admin)
        self._activity = list(activity)
        self._fail = fail

    async def list_user_admin_chats(self, *, user_id: int):
        if self._fail:
            raise RuntimeError("db is down")
        return self._admin

    async def list_user_activity_chats(self, *, user_id: int, limit: int):
        if self._fail:
            raise RuntimeError("db is down")
        return self._activity


def test_getting_started_url_uses_web_base_url_when_web_enabled():
    url = _build_getting_started_url(_settings())
    assert url == "https://selara.example/app/docs/getting-started"


def test_getting_started_url_is_none_when_web_disabled():
    assert _build_getting_started_url(_settings(WEB_ENABLED=False)) is None


def test_startgroup_url_uses_bot_username_and_is_hidden_without_one():
    assert _build_startgroup_url(_settings()) == _STARTGROUP_URL
    assert _build_startgroup_url(_settings(BOT_USERNAME="@selara_test_bot")) == _STARTGROUP_URL
    assert _build_startgroup_url(_settings(BOT_USERNAME="")) is None


def test_home_text_leads_with_a_plain_language_intro():
    text = _render_home_text(user=_user(), admin_groups=[], user_groups=[])
    assert "Selara" in text
    assert "групп" in text.lower(), "must say Selara is for group chats"
    assert "ЛС-панель" not in text, "the technical panel wording is gone from the home screen"


def test_home_text_still_shows_group_counts():
    text = _render_home_text(user=_user(), admin_groups=[object(), object()], user_groups=[object()])
    assert "<code>2</code>" in text
    assert "<code>1</code>" in text


def test_home_text_without_groups_explains_how_to_get_them():
    text = _render_home_text(user=_user(), admin_groups=[], user_groups=[])
    assert "Пока нет групп" in text


def test_home_keyboard_starts_with_personal_ai_and_help_catalog():
    markup = _build_home_keyboard(has_admin_groups=False, has_user_groups=False)
    first_two = [row[0].callback_data for row in markup.inline_keyboard[:2]]
    assert first_two == ["pai:home", "help:home"]


def test_home_keyboard_has_add_to_group_link_when_username_is_known():
    markup = _build_home_keyboard(has_admin_groups=False, has_user_groups=False, startgroup_url=_STARTGROUP_URL)
    assert _find(markup, "➕ Добавить в группу").url == _STARTGROUP_URL


def test_home_keyboard_omits_add_to_group_without_startgroup_url():
    markup = _build_home_keyboard(has_admin_groups=False, has_user_groups=False, startgroup_url=None)
    assert all("Добавить в группу" not in button.text for button in _buttons(markup))


def test_home_keyboard_has_subscriptions_button_routed_to_the_premium_screen():
    markup = _build_home_keyboard(has_admin_groups=False, has_user_groups=False)
    assert _find(markup, "💎 Подписки").callback_data == "pm:sub"


def test_group_buttons_follow_the_user_group_state():
    none = _build_home_keyboard(has_admin_groups=False, has_user_groups=False)
    assert all("Мои группы" not in b.text and "Управление группами" not in b.text for b in _buttons(none))

    activity_only = _build_home_keyboard(has_admin_groups=False, has_user_groups=True)
    assert _find(activity_only, "👥 Мои группы").callback_data == "pm:ul:0"
    assert all("Управление группами" not in b.text for b in _buttons(activity_only))

    admin = _build_home_keyboard(has_admin_groups=True, has_user_groups=False)
    assert _find(admin, "🛠 Управление группами").callback_data == "pm:al:0"


def test_home_keyboard_has_miniapp_and_desktop_buttons_when_web_is_on():
    markup = _build_home_keyboard(
        has_admin_groups=False,
        has_user_groups=False,
        miniapp_url=None,
        miniapp_webapp_url="https://selara.example/miniapp/",
        desktop_url="https://selara.example/login",
        getting_started_url="https://selara.example/app/docs/getting-started",
    )
    assert _find(markup, "📱 Mini App").web_app == WebAppInfo(url="https://selara.example/miniapp/")
    assert _find(markup, "🖥 ПК-панель").url == "https://selara.example/login"
    assert _find(markup, "🚀 Как начать").url == "https://selara.example/app/docs/getting-started"
    assert _find(markup, "📖 Документация").url == "https://selara.example/app/user"


def test_home_keyboard_omits_web_buttons_when_web_is_off():
    markup = _build_home_keyboard(
        has_admin_groups=False,
        has_user_groups=False,
        startgroup_url=_STARTGROUP_URL,
        miniapp_url=None,
        miniapp_webapp_url=None,
        desktop_url=None,
        getting_started_url=None,
    )
    texts = {button.text for button in _buttons(markup)}
    assert {"🤖 Личный AI", "✨ Возможности", "💎 Подписки", "🔄 Обновить"} <= texts
    assert not texts & {"📱 Mini App", "🖥 ПК-панель", "🚀 Как начать", "📖 Документация"}


def test_every_home_callback_fits_the_telegram_limit():
    markup = _build_home_keyboard(
        has_admin_groups=True,
        has_user_groups=True,
        startgroup_url=_STARTGROUP_URL,
        miniapp_webapp_url="https://selara.example/miniapp/",
        desktop_url="https://selara.example/login",
        getting_started_url="https://selara.example/app/docs/getting-started",
    )
    callbacks = [button.callback_data for button in _buttons(markup) if button.callback_data]
    assert callbacks
    assert all(len(data.encode("utf-8")) <= 64 for data in callbacks)


def _home_keyboard_call_sites() -> list[ast.Call]:
    tree = ast.parse(_PRIVATE_PANEL_SOURCE.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_build_home_keyboard"
    ]


def test_home_keyboard_is_built_only_by_the_shared_home_renderer():
    # Regression guard: the home screen used to be rebuilt in seven places with
    # slightly different keyword sets, so a new call site could silently drop a
    # button. Every route must go through _render_home_screen instead.
    tree = ast.parse(_PRIVATE_PANEL_SOURCE.read_text(encoding="utf-8"))
    renderer = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_render_home_screen"
    )
    inside_renderer = {id(call) for call in ast.walk(renderer) if isinstance(call, ast.Call)}
    call_sites = _home_keyboard_call_sites()
    assert len(call_sites) == 1, "_build_home_keyboard must have exactly one call site"
    assert id(call_sites[0]) in inside_renderer, "that call site must live in _render_home_screen"


@pytest.mark.asyncio
async def test_render_home_screen_builds_text_and_keyboard_from_repo_state():
    repo = _FakeActivityRepo(admin=[object()], activity=[object(), object()])
    text, markup = await _render_home_screen(activity_repo=repo, user=_user(), settings=_settings())
    assert "<code>2</code>" in text
    assert _find(markup, "🛠 Управление группами")
    assert _find(markup, "👥 Мои группы")


@pytest.mark.asyncio
async def test_home_screen_keeps_feature_buttons_when_group_lookup_fails():
    text, markup = await _render_home_screen(
        activity_repo=_FakeActivityRepo(fail=True), user=_user(), settings=_settings()
    )
    assert "Пока нет групп" in text
    assert _find(markup, "🤖 Личный AI").callback_data == "pai:home"
    assert _find(markup, "✨ Возможности").callback_data == "help:home"
    assert _find(markup, "➕ Добавить в группу").url == _STARTGROUP_URL


@pytest.mark.asyncio
async def test_notice_screens_keep_the_home_buttons():
    text, markup = await _render_home_screen(
        activity_repo=_FakeActivityRepo(), user=_user(), settings=_settings(), notice="Ввод отменён."
    )
    assert text == "Ввод отменён."
    assert _find(markup, "🤖 Личный AI").callback_data == "pai:home"


@pytest.mark.asyncio
async def test_start_panel_sends_home_without_link_preview():
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=_user(),
        answer=AsyncMock(),
    )
    await send_private_start_panel(message, _FakeActivityRepo(), None, _settings())

    message.answer.assert_awaited_once()
    kwargs = message.answer.await_args.kwargs
    assert kwargs["disable_web_page_preview"] is True
    assert "http" not in message.answer.await_args.args[0], "links live on buttons, not in the text"
    assert kwargs["reply_markup"] is not None


def test_home_reset_drops_every_waiting_text_input():
    user_id = 4242
    _set_pending_cfg_input(user_id=user_id, chat_id=-100, key="warn_limit")
    _set_pending_admin_input(user_id=user_id, chat_id=-100, mode="roles")
    personal_ai._set_pending_input(user_id, "system_prompt")

    _reset_home_pending(user_id)

    assert _get_pending_cfg_input(user_id) is None
    assert _get_pending_admin_input(user_id) is None
    assert user_id not in personal_ai._pending_inputs
