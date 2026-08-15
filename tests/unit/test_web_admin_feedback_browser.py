from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _feedback_item(*, item_id: int, done: bool, long: bool = False) -> dict[str, object]:
    title = (
        "VeryLongFeatureRequestNameWithoutAnySpaces1234567890改善履歴表示مرحباً"
        if long
        else "История сообщений как диалог"
    )
    created = "14 августа 2026, 10:00"
    done_at = "15 августа 2026, 11:30" if done else None
    history = [{"label": "Заявка создана", "time": created, "tone": "neutral"}]
    history.append(
        {
            "label": "Отмечена как сделанная" if done else "Ожидает решения",
            "time": done_at or "обновлено 14 августа 2026, 10:00",
            "tone": "done" if done else "open",
        }
    )
    return {
        "id": item_id,
        "title": title,
        "details": "Хочу читать историю чата как нормальный диалог, а не как таблицу базы данных. 🚀",
        "status_code": "done" if done else "open",
        "status_label": "Сделано" if done else "Не сделано",
        "status_note": f"Отмечено {done_at}" if done else "Ожидает решения",
        "is_done": done,
        "created_at": created,
        "updated_at": done_at or created,
        "done_at": done_at,
        "author_label": "LongContinuousAuthorNameWithoutSpaces1234567890" if long else "@alice",
        "status_history": history,
    }


def _render_feedback(
    *,
    status: str = "all",
    empty: bool = False,
    filter_error: str | None = None,
) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    items = [] if empty else [_feedback_item(item_id=41, done=False), _feedback_item(item_id=40, done=True), _feedback_item(item_id=39, done=False, long=True)]
    return environment.get_template("admin.html").render(
        page_title="Selara feedback tasks",
        page_name="admin",
        top_links=[],
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-overview.css", "admin-feedback.css", "admin-broadcast.css"],
        extra_scripts=["admin-overview.js", "admin-feedback.js", "admin-broadcast.js"],
        admin_user_id=77,
        open_feedback_count=2,
        attention_broadcast_count=0,
        attention_summary="2 задачи требуют внимания",
        recent_broadcast_count=0,
        admin_table_count=47,
        broadcast_active_days=3,
        recent_active_chat_count=0,
        broadcast_audience_status="0 чатов доступны",
        recent_active_chats=[],
        recent_broadcasts=[],
        feedback_requests=items,
        feedback_status=status,
        feedback_filter_error=filter_error,
        table_sections=[],
    )


async def _mount(page, *, status: str = "all", empty: bool = False) -> None:
    await page.set_content(_render_feedback(status=status, empty=empty))
    for stylesheet in (
        "panel.css",
        "server-ui-foundation.css",
        "admin-overview.css",
        "admin-feedback.css",
        "admin-broadcast.css",
    ):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "admin-feedback.js"), type="module")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_admin_feedback_tasks_are_readable_and_responsive(
    viewport: dict[str, int],
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page)
            section = page.locator("[data-admin-feedback]")
            await section.scroll_into_view_if_needed()

            assert await section.locator("[data-feedback-filter]").count() == 3
            assert await section.locator('[data-feedback-filter="all"]').get_attribute("aria-current") == "page"
            assert await section.locator(".admin-feedback-task").count() == 3
            assert await section.locator(".admin-feedback-task").last.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            action = section.locator("[data-feedback-status-form] button").first
            assert await action.evaluate("element => element.getBoundingClientRect().height") >= 44
            history = section.locator(".admin-feedback-history").first
            await history.locator("summary").click()
            assert await history.get_by_text("Заявка создана").is_visible()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_feedback_submit_is_guarded_against_double_click() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page)
            form = page.locator("[data-feedback-status-form]").first
            await form.evaluate("element => element.addEventListener('submit', event => event.preventDefault())")
            button = form.locator("button")
            await button.click()
            assert await button.is_disabled()
            assert await button.inner_text() == "Обновляем…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_feedback_filtered_empty_state_explains_recovery() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, status="done", empty=True)
            section = page.locator("[data-admin-feedback]")
            await section.scroll_into_view_if_needed()
            assert await section.get_by_text("Нет заявок с этим статусом").is_visible()
            assert await section.get_by_role("link", name="Показать все заявки").is_visible()
        finally:
            await browser.close()
