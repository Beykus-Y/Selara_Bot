from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _render_admin_overview(*, attention: bool = True) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("admin.html").render(
        page_title="Selara admin overview",
        page_name="admin",
        top_links=[
            {"href": "/app/admin", "label": "Обзор", "variant": "subtle", "current": True},
            {"href": "/app/admin#broadcasts", "label": "Рассылки", "variant": "ghost"},
            {"href": "/app/admin/table/messages_compact", "label": "История", "variant": "ghost"},
            {"href": "/app/admin#database", "label": "База данных", "variant": "ghost"},
        ],
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-overview.css", "admin-feedback.css", "admin-broadcast.css"],
        extra_scripts=["admin-overview.js", "admin-feedback.js", "admin-broadcast.js"],
        admin_user_id=77,
        open_feedback_count=3 if attention else 0,
        attention_broadcast_count=1 if attention else 0,
        attention_summary="4 задачи требуют внимания" if attention else "0 задач требуют внимания",
        recent_broadcast_count=2 if attention else 0,
        admin_table_count=47,
        broadcast_active_days=3,
        recent_active_chat_count=12 if attention else 0,
        broadcast_audience_status="12 чатов доступны" if attention else "0 чатов доступны",
        recent_active_chats=[],
        recent_broadcasts=[],
        feedback_requests=[],
        feedback_status="all",
        feedback_filter_error=None,
        table_sections=[],
    )


async def _mount(page, *, attention: bool = True) -> None:
    await page.set_content(_render_admin_overview(attention=attention))
    for stylesheet in (
        "panel.css",
        "server-ui-foundation.css",
        "admin-overview.css",
        "admin-feedback.css",
        "admin-broadcast.css",
    ):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "admin-overview.js"), type="module")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_admin_overview_prioritizes_tasks_without_overflow(
    viewport: dict[str, int],
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page)

            overview = page.locator("[data-admin-overview]")
            assert await overview.get_by_role("heading", name="Что требует внимания").is_visible()
            assert await overview.get_by_text("4 задачи требуют внимания").is_visible()
            assert await overview.get_by_text("Подключена").is_visible()
            assert await overview.get_by_text("Проверка при отправке").is_visible()
            assert await overview.locator(".admin-quick-action").count() == 6
            assert await overview.locator(".admin-quick-action").first.evaluate(
                "element => element.getBoundingClientRect().height"
            ) >= 44
            backup_action = overview.locator("[data-admin-backup-open]")
            assert await backup_action.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) != "rgb(107, 107, 107)"
            assert await overview.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            assert await page.locator('form[action="/app/admin/logout"]').count() == 1
            if viewport["width"] == 390:
                hidden_skip = await page.locator(".skip-link").bounding_box()
                assert hidden_skip is not None
                assert hidden_skip["y"] + hidden_skip["height"] <= 0
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(180)
                visible_skip = await page.locator(".skip-link").bounding_box()
                assert visible_skip is not None
                assert visible_skip["y"] >= 0
                assert visible_skip["y"] + visible_skip["height"] <= viewport["height"]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_overview_backup_confirmation_restores_focus() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page)
            trigger = page.locator("[data-admin-backup-open]")
            dialog = page.locator("[data-admin-backup-dialog]")

            await trigger.click()
            assert await dialog.is_visible()
            assert await dialog.get_by_role("button", name="Отмена").evaluate(
                "element => element === document.activeElement"
            )
            await dialog.get_by_role("button", name="Отмена").click()
            assert await dialog.is_hidden()
            assert await trigger.evaluate("element => element === document.activeElement")

            await trigger.click()
            await page.keyboard.press("Escape")
            assert await dialog.is_hidden()
            assert await trigger.evaluate("element => element === document.activeElement")

            await trigger.click()
            await dialog.locator("form").evaluate(
                "form => form.addEventListener('submit', event => event.preventDefault())"
            )
            submit = dialog.locator("[data-admin-backup-submit]")
            await submit.click()
            assert await submit.is_disabled()
            assert await submit.inner_text() == "Формируем backup…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_overview_has_calm_empty_state() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, attention=False)
            overview = page.locator("[data-admin-overview]")
            assert await overview.get_by_text("Открытых задач нет").is_visible()
            assert await overview.get_by_text("Нет подходящих чатов").is_visible()
            assert await overview.locator(".admin-attention-card.is-calm").count() == 1
        finally:
            await browser.close()
