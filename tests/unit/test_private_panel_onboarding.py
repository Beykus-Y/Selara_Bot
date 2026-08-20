"""Regression tests for the /start onboarding redesign (documentation
modernization TODO, "P1 -- Discoverability / Onboarding"): /start used to
open straight into the technical ЛС-panel (group counts, Mini App/PC-panel
links) with zero explanation of what Selara does -- bad first-run
experience. These tests pin the required behavior: a plain-language intro,
an obvious "Как начать" button linking to a real public route, and that the
existing panel info (group counts, Mini App, PC-panel) is preserved."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from selara.core.config import Settings
from selara.presentation.handlers.private_panel import (
    _build_getting_started_url,
    _build_home_keyboard,
    _render_home_text,
)


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


def _user():
    return SimpleNamespace(id=1, username="ilya", first_name="Ilya", last_name=None, is_bot=False)


def test_getting_started_url_uses_web_base_url_when_web_enabled():
    url = _build_getting_started_url(_settings())
    assert url == "https://selara.example/app/docs/getting-started"


def test_getting_started_url_is_none_when_web_disabled():
    assert _build_getting_started_url(_settings(WEB_ENABLED=False)) is None


@pytest.mark.asyncio
async def test_home_text_leads_with_a_plain_language_intro_before_panel_details():
    text = await _render_home_text(user=_user(), admin_groups=[], user_groups=[])
    intro_idx = text.find("Selara")
    panel_idx = text.find("ЛС-панель")
    assert intro_idx != -1, "home text must introduce what Selara is"
    assert panel_idx != -1
    assert intro_idx < panel_idx, "the plain-language intro must come before the technical panel details"
    assert "групп" in text.lower(), "must mention Selara is for group chats"


@pytest.mark.asyncio
async def test_home_text_still_shows_group_counts():
    text = await _render_home_text(
        user=_user(),
        admin_groups=[object(), object()],
        user_groups=[object()],
    )
    assert "2" in text
    assert "1" in text


def test_home_keyboard_has_getting_started_button_first_when_available():
    markup = _build_home_keyboard(
        has_admin_groups=False,
        has_user_groups=False,
        miniapp_url=None,
        miniapp_webapp_url=None,
        desktop_url=None,
        getting_started_url="https://selara.example/app/docs/getting-started",
    )
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert buttons, "keyboard must not be empty"
    assert "начать" in buttons[0].text.lower(), "Как начать must be the first, most prominent button"
    assert buttons[0].url == "https://selara.example/app/docs/getting-started"


def test_home_keyboard_omits_getting_started_button_when_web_disabled():
    markup = _build_home_keyboard(
        has_admin_groups=False,
        has_user_groups=False,
        miniapp_url=None,
        miniapp_webapp_url=None,
        desktop_url=None,
        getting_started_url=None,
    )
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert not any("начать" in b.text.lower() for b in buttons)


def test_home_keyboard_still_has_miniapp_and_desktop_buttons():
    from aiogram.types import WebAppInfo

    markup = _build_home_keyboard(
        has_admin_groups=False,
        has_user_groups=False,
        miniapp_url=None,
        miniapp_webapp_url="https://selara.example/miniapp/",
        desktop_url="https://selara.example/login",
        getting_started_url="https://selara.example/app/docs/getting-started",
    )
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert any("Mini App" in b.text for b in buttons)
    assert any("ПК-панель" in b.text for b in buttons)
    assert any(isinstance(getattr(b, "web_app", None), WebAppInfo) for b in buttons)
