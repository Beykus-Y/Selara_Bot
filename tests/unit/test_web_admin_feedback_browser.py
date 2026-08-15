import asyncio
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
        extra_styles=["admin-overview.css", "admin-feedback.css", "admin-broadcast.css", "admin-table-search.css"],
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
        "admin-table-search.css",
    ):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "admin-feedback.js"), type="module")


async def _goto_feedback(page, *, status: str = "all", empty: bool = False) -> None:
    """Same as `_mount`, but served from a routed same-origin URL.

    A plain `page.set_content()` page lives at `about:blank`, which has an
    opaque origin — relative `fetch()` calls (used by the real status-update
    flow) fail immediately with a URL-parse TypeError before any request is
    dispatched, so `page.route()` interception never sees them.
    """
    html = _render_feedback(status=status, empty=empty)

    async def serve_page(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route("http://selara.test/admin", serve_page)
    await page.goto("http://selara.test/admin")
    for stylesheet in (
        "panel.css",
        "server-ui-foundation.css",
        "admin-overview.css",
        "admin-feedback.css",
        "admin-broadcast.css",
        "admin-table-search.css",
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
            await _goto_feedback(page)
            form = page.locator("[data-feedback-status-form]").first
            request_count = {"value": 0}

            async def handle_status(route):
                request_count["value"] += 1
                # Never resolves within the test's lifetime — keeps the button
                # in its "submitting" state so the disabled check below can't race.
                await asyncio.sleep(5)
                await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

            await page.route("**/api/admin/feedback/*/status", handle_status)
            button = form.locator("button")
            # A disabled button is not Playwright-actionable, so the click itself
            # already proves the guard: a second dispatched click cannot land.
            await button.click()
            assert await button.is_disabled()
            assert await button.inner_text() == "Обновляем…"
            await button.dispatch_event("click")
            assert request_count["value"] == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_feedback_status_error_shows_inline_and_keeps_other_items() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _goto_feedback(page)
            tasks = page.locator(".admin-feedback-task")
            first_form = tasks.nth(0).locator("[data-feedback-status-form]")
            second_form = tasks.nth(1).locator("[data-feedback-status-form]")

            async def handle_status(route):
                await route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"ok": false, "message": "Заявка уже обновлена другим администратором."}',
                )

            await page.route("**/api/admin/feedback/*/status", handle_status)

            await first_form.locator("button").click()

            first_error = first_form.locator("[data-feedback-item-error]")
            await first_error.wait_for(state="visible")
            assert await first_error.inner_text() == "Заявка уже обновлена другим администратором."
            assert await second_form.locator("[data-feedback-item-error]").is_hidden()
            # No page reload happened — button re-enabled with its original text.
            assert not await first_form.locator("button").is_disabled()
            assert await tasks.count() == 3
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
