"""Regression guard for chat.html's mobile tab navigation between sections
(overview/achievements/settings/economy/family/audit) — the final sub-slice
of chat.html's Etap 4 multi-part checklist item, see
docs/WEB_UI_MODERNIZATION_TODO.md.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"
CHAT_ID = 123


def _render(*, active_tab: str) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    links = [
        {"href": f"/app/chat/{CHAT_ID}?tab=overview", "label": "Обзор", "variant": "primary" if active_tab == "overview" else "ghost"},
        {"href": f"/app/chat/{CHAT_ID}?tab=achievements", "label": "Достижения", "variant": "primary" if active_tab == "achievements" else "ghost"},
        {"href": f"/app/chat/{CHAT_ID}?tab=settings", "label": "Настройки", "variant": "primary" if active_tab == "settings" else "ghost"},
        {"href": f"/app/chat/{CHAT_ID}/economy", "label": "Экономика", "variant": "primary" if active_tab == "economy" else "ghost"},
        {"href": f"/app/family/{CHAT_ID}", "label": "Моя семья", "variant": "primary" if active_tab == "family" else "ghost"},
        {"href": f"/app/chat/{CHAT_ID}/audit", "label": "Аудит", "variant": "primary" if active_tab == "audit" else "ghost"},
    ]
    return environment.get_template("chat.html").render(
        page_title="Selara", page_name="chat", top_links=[], show_logout=True,
        flash=None, error=None,
        chat_title="Selara Community", hero_subtitle="Обзор группы.", chat_id=CHAT_ID,
        chat_section_links=links,
        metrics=[], dashboard_panels=[], access_rows=[], roles=[], command_rules=[],
        leaderboards=[], trigger_template_quick_rows=[], trigger_template_examples=[],
        trigger_template_docs_url="/docs", audit_rows=[],
        can_manage_settings=True, manage_settings_tone="ok", manage_settings_note="x",
        active_tab=active_tab,
        settings_sections=[],
        alias_mode_setting={
            "key": "alias_mode", "title": "t", "description": "d", "current_value": "both",
            "default_value": "both", "current_value_display": "x", "default_value_display": "x",
            "hint": "h", "editable": True, "input_kind": "select",
            "options": [{"value": "both", "label": "x", "selected": True}], "doc_href": "#x",
        },
        alias_source_options=[], aliases=[], triggers=[],
        admin_docs_url="/app/docs/admin",
        achievement_sections=[],
        extra_scripts=["chat-overview.js", "chat-settings.js", "chat-plain-forms.js"],
    )


async def _mount(page, *, active_tab: str) -> None:
    await page.set_content(_render(active_tab=active_tab))
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 900},
        {"width": 320, "height": 900},
        {"width": 820, "height": 1100},
    ],
)
async def test_chat_page_has_no_horizontal_overflow_with_all_six_section_links(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, active_tab="audit")
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("active_tab", ["overview", "achievements", "settings", "economy", "family", "audit"])
async def test_active_section_tab_is_always_fully_visible_on_mobile(active_tab: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 900})
        try:
            await _mount(page, active_tab=active_tab)
            in_view = await page.evaluate(
                """() => {
                    const container = document.querySelector('.section-tabs');
                    const active = document.querySelector('.section-tabs .button.primary');
                    const cRect = container.getBoundingClientRect();
                    const aRect = active.getBoundingClientRect();
                    return aRect.left >= cRect.left - 1 && aRect.right <= cRect.right + 1;
                }"""
            )
            assert in_view, f"active tab {active_tab!r} is not fully visible without scrolling"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_section_tab_buttons_meet_the_minimum_touch_target_height() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 900})
        try:
            await _mount(page, active_tab="overview")
            heights = await page.eval_on_selector_all(
                ".section-tabs .button", "els => els.map(e => e.getBoundingClientRect().height)"
            )
            assert len(heights) == 6
            assert all(height >= 44 for height in heights)
        finally:
            await browser.close()
