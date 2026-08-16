import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


DEFAULT_TABLE_SECTIONS = [
    {
        "title": "Активность и пользователи",
        "tables": [
            ("chats", "Чаты"),
            ("users", "Пользователи"),
            ("user_chat_activity", "Активность пользователей"),
        ],
    },
    {
        "title": "Экономика",
        "tables": [
            ("economy_items", "Предметы экономики"),
            ("economy_listings", "Рыночные лоты"),
        ],
    },
]


def _render_admin_overview(
    *,
    attention: bool = True,
    table_sections: list | None = None,
    feedback_requests: list | None = None,
) -> str:
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
        extra_styles=["admin-overview.css", "admin-feedback.css", "admin-broadcast.css", "admin-table-search.css"],
        extra_scripts=[
            "admin-overview.js",
            "admin-feedback.js",
            "admin-broadcast.js",
            "admin-table-search.js",
        ],
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
        feedback_requests=[] if feedback_requests is None else feedback_requests,
        feedback_status="all",
        feedback_filter_error=None,
        table_sections=DEFAULT_TABLE_SECTIONS if table_sections is None else table_sections,
    )


def _many_feedback_items(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": item_id,
            "title": f"Заявка о доработке номер {item_id}",
            "details": "Подробное описание пожелания пользователя для проверки плотной раскладки.",
            "status_code": "open",
            "status_label": "Не сделано",
            "status_note": "Ожидает решения",
            "is_done": False,
            "created_at": "14 августа 2026, 10:00",
            "updated_at": "14 августа 2026, 10:00",
            "done_at": None,
            "author_label": f"@user{item_id}",
            "status_history": [{"label": "Заявка создана", "time": "14 августа 2026, 10:00", "tone": "neutral"}],
        }
        for item_id in range(1, count + 1)
    ]


def _many_table_sections(domain_count: int, tables_per_domain: int) -> list[dict[str, object]]:
    return [
        {
            "title": f"Домен {domain_index}",
            "tables": [
                (f"domain_{domain_index}_table_{table_index}", f"Таблица {domain_index}.{table_index}")
                for table_index in range(1, tables_per_domain + 1)
            ],
        }
        for domain_index in range(1, domain_count + 1)
    ]


async def _mount(
    page,
    *,
    attention: bool = True,
    table_sections: list | None = None,
    feedback_requests: list | None = None,
) -> None:
    await page.set_content(
        _render_admin_overview(
            attention=attention, table_sections=table_sections, feedback_requests=feedback_requests
        )
    )
    for stylesheet in (
        "panel.css",
        "server-ui-foundation.css",
        "admin-overview.css",
        "admin-feedback.css",
        "admin-broadcast.css",
        "admin-table-search.css",
    ):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    # The skip-link's `transform` transition (140ms) can still be mid-flight
    # when a test reads its geometry right after a fixed wait_for_timeout —
    # flaky only under full-suite load, not in isolation. Disable transitions
    # for deterministic geometry reads (same fix already applied in
    # test_web_admin_shell_browser.py for the identical failure class).
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")
    await page.add_script_tag(path=str(STATIC_DIR / "admin-overview.js"), type="module")
    await page.add_script_tag(path=str(STATIC_DIR / "admin-table-search.js"), type="module")


async def _goto_overview(page, *, attention: bool = True) -> None:
    """Same as `_mount`, but served from a routed same-origin URL.

    A plain `page.set_content()` page lives at `about:blank`, which has an
    opaque origin — relative `fetch()` calls (used by the real backup-request
    flow) fail immediately with a URL-parse TypeError before any request is
    dispatched, so `page.route()` interception never sees them.
    """
    html = _render_admin_overview(attention=attention)

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
    await page.add_script_tag(path=str(STATIC_DIR / "admin-overview.js"), type="module")
    await page.add_script_tag(path=str(STATIC_DIR / "admin-table-search.js"), type="module")


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
            await _goto_overview(page)
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

            async def handle_backup(route):
                # Never resolves within the test's lifetime — keeps the button
                # in its "submitting" state so the disabled check below can't race.
                await asyncio.sleep(5)
                await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

            await page.route("**/api/admin/request-backup", handle_backup)
            submit = dialog.locator("[data-admin-backup-submit]")
            await submit.click()
            assert await submit.is_disabled()
            assert await submit.inner_text() == "Формируем backup…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_overview_backup_error_shows_inline_without_losing_dialog() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _goto_overview(page)
            trigger = page.locator("[data-admin-backup-open]")
            dialog = page.locator("[data-admin-backup-dialog]")

            await trigger.click()

            async def handle_backup(route):
                await route.fulfill(
                    status=500,
                    content_type="application/json",
                    body='{"ok": false, "message": "Не удалось отправить backup. Проверьте логи и конфиг."}',
                )

            await page.route("**/api/admin/request-backup", handle_backup)
            submit = dialog.locator("[data-admin-backup-submit]")
            await submit.click()

            error = dialog.locator("[data-admin-backup-error]")
            await error.wait_for(state="visible")
            assert await error.inner_text() == "Не удалось отправить backup. Проверьте логи и конфиг."
            assert await dialog.is_visible()
            assert not await submit.is_disabled()
            assert await submit.inner_text() == "Запросить backup"
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


@pytest.mark.asyncio
async def test_admin_table_search_filters_cards_and_hides_empty_sections() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page)
            search = page.locator("[data-table-search-input]")
            sections = page.locator("[data-table-search-section]")
            empty_message = page.locator("[data-table-search-empty]")

            assert await page.locator("[data-table-search-card]:visible").count() == 5
            assert await empty_message.is_hidden()

            await search.fill("economy")
            assert await page.locator("[data-table-search-card]:visible").count() == 2
            assert await sections.nth(0).is_hidden()
            assert await sections.nth(1).is_visible()
            assert await empty_message.is_hidden()

            await search.fill("нет такой таблицы")
            assert await page.locator("[data-table-search-card]:visible").count() == 0
            assert await sections.nth(0).is_hidden()
            assert await sections.nth(1).is_hidden()
            assert await empty_message.is_visible()

            await search.fill("")
            assert await page.locator("[data-table-search-card]:visible").count() == 5
            assert await empty_message.is_hidden()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_table_sections_are_collapsible_without_hiding_critical_status() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page)
            details = page.locator("details.admin-secondary-details")
            attention = page.locator("[data-admin-overview]").get_by_role(
                "heading", name="Что требует внимания"
            )

            assert await details.count() == 2
            for index in range(await details.count()):
                assert await details.nth(index).evaluate("element => element.open")
            # Critical status is a plain section, not a <details> — it can't be collapsed.
            assert await attention.is_visible()

            second = details.nth(1)
            summary = second.locator("summary")
            await summary.click()
            assert not await second.evaluate("element => element.open")
            assert await second.locator("[data-table-search-card]").first.is_hidden()

            await summary.click()
            assert await second.evaluate("element => element.open")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_table_search_reopens_a_collapsed_matching_section() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page)
            economy_details = page.locator("details.admin-secondary-details").nth(1)
            await economy_details.locator("summary").click()
            assert not await economy_details.evaluate("element => element.open")

            await page.locator("[data-table-search-input]").fill("economy")
            assert await economy_details.evaluate("element => element.open")
            assert await economy_details.locator("[data-table-search-card]:visible").count() == 2
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
async def test_admin_overview_handles_large_dataset_without_overflow(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(
                page,
                feedback_requests=_many_feedback_items(24),
                table_sections=_many_table_sections(domain_count=10, tables_per_domain=6),
            )
            overview = page.locator("[data-admin-overview]")
            assert await overview.get_by_role("heading", name="Что требует внимания").is_visible()
            assert await page.locator(".admin-feedback-task").count() == 24
            assert await page.locator("details.admin-secondary-details").count() == 10
            assert await page.locator("[data-table-search-card]").count() == 60
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            # Search still works correctly at this scale.
            await page.locator("[data-table-search-input]").fill("domain_3_table_2")
            assert await page.locator("[data-table-search-card]:visible").count() == 1
        finally:
            await browser.close()
