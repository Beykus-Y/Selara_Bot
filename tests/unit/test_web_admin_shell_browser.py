from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}
TABLET = {"width": 820, "height": 1180}


def _render_shell(*, current_label: str) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    labels = ["Обзор", "Рассылки", "История", "База данных"]
    hrefs = {
        "Обзор": "/app/admin",
        "Рассылки": "/app/admin#broadcasts",
        "История": "/app/admin/table/messages_compact",
        "База данных": "/app/admin#database",
    }
    top_links = [
        {
            "href": hrefs[label],
            "label": label,
            "variant": "subtle" if label == current_label else "ghost",
            "current": label == current_label,
        }
        for label in labels
    ]
    return environment.get_template("admin.html").render(
        page_title="Selara test admin shell",
        page_name="admin",
        top_links=top_links,
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=[],
        extra_scripts=[],
        admin_user_id=77,
        open_feedback_count=0,
        attention_broadcast_count=0,
        attention_summary="0 задач требуют внимания",
        recent_broadcast_count=0,
        admin_table_count=0,
        broadcast_active_days=3,
        recent_active_chat_count=0,
        broadcast_audience_status="0 чатов доступны",
        recent_active_chats=[],
        recent_broadcasts=[],
        feedback_requests=[],
        feedback_status="all",
        feedback_filter_error=None,
        table_sections=[],
    )


async def _load(page, *, current_label: str) -> None:
    await page.set_content(_render_shell(current_label=current_label))
    await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "admin-overview.css"))
    # Stylesheets are attached sequentially after the initial paint, so the
    # skip-link's `transform` transition can still be mid-flight when a test
    # reads computed style right away. Disable transitions for deterministic,
    # immediate assertions (matches the reduced-motion behavior it already has).
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [DESKTOP, MOBILE, TABLET])
async def test_admin_shell_marks_active_page_and_has_no_overflow(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _load(page, current_label="Рассылки")

            active = page.locator('.top-actions [aria-current="page"]')
            assert await active.count() == 1
            assert await active.inner_text() == "Рассылки"
            # Active state isn't color-only: distinct border + background + box-shadow.
            active_style = await active.evaluate(
                "el => { const s = getComputedStyle(el); return {border: s.borderColor, shadow: s.boxShadow}; }"
            )
            inactive_style = await page.locator('.top-actions .button:not([aria-current="page"])').first.evaluate(
                "el => { const s = getComputedStyle(el); return {border: s.borderColor, shadow: s.boxShadow}; }"
            )
            assert active_style["border"] != inactive_style["border"]
            assert active_style["shadow"] != inactive_style["shadow"]

            for pill in await page.locator(".top-actions .button").all():
                height = await pill.evaluate("el => el.getBoundingClientRect().height")
                assert height >= 40

            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_shell_skip_link_is_keyboard_reachable_and_hidden_until_focus() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=DESKTOP)
        try:
            await _load(page, current_label="Обзор")
            skip_link = page.locator(".skip-link")

            hidden_transform = await skip_link.evaluate("el => getComputedStyle(el).transform")
            assert hidden_transform != "none"

            await page.keyboard.press("Tab")
            assert await page.evaluate("document.activeElement.classList.contains('skip-link')")
            focused_transform = await skip_link.evaluate("el => getComputedStyle(el).transform")
            assert focused_transform != hidden_transform

            await page.keyboard.press("Enter")
            assert await page.evaluate("document.activeElement.id") == "main-content"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_shell_mobile_nav_is_a_scrollable_ribbon_without_page_overflow() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=MOBILE)
        try:
            await _load(page, current_label="История")
            nav = page.locator(".top-actions")
            assert await nav.evaluate("el => getComputedStyle(el).overflowX") == "auto"
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_shell_tablet_nav_fits_in_one_row_without_scroll_ribbon() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=TABLET)
        try:
            await _load(page, current_label="База данных")
            nav = page.locator(".top-actions")
            # At tablet width the shell keeps the continuous desktop-style layout
            # (no dedicated tablet breakpoint) — confirm that decision actually
            # holds: all pills fit on one row without needing the mobile
            # horizontal-scroll-ribbon treatment.
            fits_without_scroll = await nav.evaluate("el => el.scrollWidth <= el.clientWidth")
            assert fits_without_scroll
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
