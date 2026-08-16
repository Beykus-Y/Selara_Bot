"""Functional coverage for chat.html's externalized JS (chat-overview.js /
chat-settings.js), extracted from inline <script> blocks in this slice.

These tests exist to prove the extraction was behavior-preserving, not just
a line-count move: they exercise the real fetch()-driven leaderboard/overview
refresh and the settings save/dirty-state flow through the actual DOM, with
network calls intercepted via page.route() rather than asserting on JS source.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"

CHAT_ID = 123


def _base_context(*, active_tab: str) -> dict[str, object]:
    return {
        "page_title": "Selara",
        "page_name": "chat",
        "top_links": [],
        "show_logout": False,
        "flash": None,
        "error": None,
        "chat_title": "Test Chat",
        "hero_subtitle": "Test subtitle",
        "chat_id": CHAT_ID,
        "chat_section_links": [
            {"href": f"/app/chat/{CHAT_ID}", "label": "Обзор", "variant": "subtle"},
            {"href": f"/app/chat/{CHAT_ID}/achievements", "label": "Достижения", "variant": "ghost"},
            {"href": f"/app/chat/{CHAT_ID}/settings", "label": "Настройки", "variant": "ghost"},
        ],
        "metrics": [],
        "dashboard_panels": [],
        "access_rows": [],
        "roles": [],
        "command_rules": [],
        "leaderboards": [],
        "aliases": [],
        "triggers": [],
        "trigger_template_quick_rows": [],
        "trigger_template_examples": [],
        "trigger_template_docs_url": f"/app/docs/admin?chat_id={CHAT_ID}#docs-trigger-variables",
        "audit_rows": [],
        "can_manage_settings": True,
        "manage_settings_tone": "ok",
        "manage_settings_note": "Настройки доступны.",
        "active_tab": active_tab,
        "settings_sections": [],
        "alias_mode_setting": {
            "key": "alias_mode",
            "title": "Режим алиасов команд",
            "description": "Описание.",
            "current_value": "both",
            "default_value": "both",
            "current_value_display": "смешанный режим",
            "default_value_display": "смешанный режим",
            "hint": "aliases_if_exists / both / standard_only.",
            "editable": True,
            "input_kind": "select",
            "options": [{"value": "both", "label": "смешанный режим", "selected": True}],
            "doc_href": "#docs-aliases",
        },
        "alias_source_options": [],
        "admin_docs_url": "/app/docs/admin",
        "extra_scripts": ["chat-overview.js", "chat-settings.js"],
    }


def _render(*, active_tab: str, **overrides: object) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    context = _base_context(active_tab=active_tab)
    context.update(overrides)
    return environment.get_template("chat.html").render(**context)


async def _goto(page, html: str) -> None:
    url = f"http://selara.test/app/chat/{CHAT_ID}"

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route(url, serve_page)
    await page.goto(url)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


@pytest.mark.asyncio
async def test_chat_overview_loads_activity_and_leaderboard_from_live_api() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _goto(page, _render(active_tab="overview"))

            async def handle_overview(route):
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"ok": true, "daily_activity": [{"label": "Пн", "messages": 12}, '
                        '{"label": "Вт", "messages": 30}], '
                        '"hero_of_day": {"label": "Ivan", "messages": 30, "karma": 5}, '
                        '"richest_of_day": {"label": "Petr", "balance": 900}}'
                    ),
                )

            async def handle_leaderboard(route):
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"ok": true, "rows": [{"position": 1, "name": "Ivan", "username": "ivan", '
                        '"is_me": false, "activity": 30, "karma": 5, "hybrid_score": 1.234, '
                        '"last_seen_at": "16.08.2026"}], "page": 1, "total_pages": 1, '
                        '"total_rows": 1, "my_rank": 1, "truncated": false}'
                    ),
                )

            await page.route(f"**/api/chat/{CHAT_ID}/overview", handle_overview)
            await page.route(f"**/api/chat/{CHAT_ID}/leaderboard*", handle_leaderboard)
            await page.add_script_tag(path=str(STATIC_DIR / "chat-overview.js"), type="module")

            chart = page.locator("[data-chat-activity-chart]")
            await chart.locator(".activity-bar").first.wait_for(state="visible")
            assert await chart.locator(".activity-bar").count() == 2
            assert "30" in await chart.inner_text()

            hero = page.locator("[data-hero-of-day]")
            await hero.get_by_text("Ivan").wait_for(state="visible")

            table_body = page.locator("[data-lb-body]")
            await table_body.get_by_text("Ivan").first.wait_for(state="visible")
            assert "1.234" in await table_body.inner_text()

            karma_button = page.locator('[data-lb-mode="karma"]')
            await karma_button.click()
            assert "is-active" in (await karma_button.get_attribute("class") or "")
            mix_button = page.locator('[data-lb-mode="mix"]')
            assert "is-active" not in (await mix_button.get_attribute("class") or "")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_chat_settings_toggle_marks_card_dirty_and_saves_via_fetch() -> None:
    settings_sections = [
        {
            "title": "Основное",
            "items": [
                {
                    "title": "Экономика включена",
                    "key": "economy_enabled",
                    "description": "Описание настройки.",
                    "current_value": "true",
                    "default_value": "true",
                    "current_value_display": "включено",
                    "default_value_display": "включено",
                    "hint": "true/false",
                    "editable": True,
                    "input_kind": "toggle",
                    "options": [],
                    "doc_anchor": "docs-economy-enabled",
                }
            ],
        }
    ]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _goto(page, _render(active_tab="settings", settings_sections=settings_sections))
            await page.add_script_tag(path=str(STATIC_DIR / "chat-settings.js"), type="module")

            card = page.locator("[data-setting-card][data-setting-key='economy_enabled']")
            toggle = card.locator("[data-setting-toggle]")

            await card.locator("label.setting-toggle").click()
            assert "is-dirty" in (await card.get_attribute("class") or "")
            assert not await toggle.is_checked()

            async def handle_save(route):
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"ok": true, "message": "Сохранено.", '
                        '"setting": {"current_value": "false", "default_value": "true", '
                        '"current_value_display": "выключено", "default_value_display": "включено"}}'
                    ),
                )

            await page.route(f"**/app/chat/{CHAT_ID}/settings", handle_save)
            await card.locator(".setting-form button[type=submit]").click()

            current = card.locator("[data-setting-current]")
            await page.wait_for_timeout(120)
            assert "выключено" in (await current.inner_text()).lower()
            assert "is-dirty" not in (await card.get_attribute("class") or "")
        finally:
            await browser.close()
