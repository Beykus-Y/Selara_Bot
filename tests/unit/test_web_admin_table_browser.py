from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.infrastructure.db.models import UserModel
from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"

TOP_LINKS = [
    {"href": "/app/admin", "label": "Обзор", "variant": "ghost"},
    {"href": "/app/admin#broadcasts", "label": "Рассылки", "variant": "ghost"},
    {"href": "/app/admin/table/messages_compact", "label": "История", "variant": "ghost"},
    {"href": "/app/admin#database", "label": "База данных", "variant": "subtle", "current": True},
]


def _users(count: int) -> list[UserModel]:
    rows = []
    for index in range(count):
        rows.append(
            UserModel(
                telegram_user_id=100 + index,
                username=(
                    "ОченьДлинноеИмяПользователяБезПробеловForTestOverflow1234567890"
                    if index == 0
                    else f"user{index}"
                ),
                first_name="Алиса" if index == 0 else f"User{index}",
                last_name=None,
                is_bot=index % 2 == 0,
                subscription_exempt=index % 3 == 0,
                updated_at=datetime(2026, 4, 8, 18, 24, tzinfo=timezone.utc),
            )
        )
    return rows


def _render_admin_table(*, page_num: int = 1, total: int = 120) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    rows = _users(3)
    columns = ["telegram_user_id", "username", "first_name", "is_bot", "subscription_exempt", "updated_at"]
    row_entries = [
        {
            "row": row,
            "pk_query": f"telegram_user_id={row.telegram_user_id}",
            "delete_label": f"Пользователи: telegram_user_id={row.telegram_user_id}",
        }
        for row in rows
    ]
    return environment.get_template("admin_table.html").render(
        page_title="Selara admin table fixture",
        page_name="admin_table",
        top_links=TOP_LINKS,
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-table.css"],
        extra_scripts=["admin-table.js"],
        table_name="users",
        table_title="Пользователи",
        columns=columns,
        row_entries=row_entries,
        page=page_num,
        total=total,
        limit=50,
        filters_input={},
        reference_labels={},
        previous_page_href="/app/admin/table/users?page=1" if page_num > 1 else None,
        next_page_href="/app/admin/table/users?page=2" if page_num * 50 < total else None,
    )


def _render_admin_edit() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    row = _users(1)[0]
    columns = [(col, getattr(row, col)) for col in ("telegram_user_id", "username", "first_name", "is_bot", "updated_at")]
    return environment.get_template("admin_edit.html").render(
        page_title="Selara admin edit fixture",
        page_name="admin_edit",
        top_links=TOP_LINKS,
        show_logout=True,
        logout_action="/app/admin/logout",
        body_classes="ui-admin-shell",
        navigation_label="Разделы админки",
        flash=None,
        error=None,
        extra_styles=["admin-table.css"],
        extra_scripts=["admin-table.js"],
        table_name="users",
        table_title="Пользователи",
        record_id="telegram_user_id=100",
        pk_fields=[("telegram_user_id", row.telegram_user_id)],
        primary_key_columns=["telegram_user_id"],
        columns=columns,
        reference_labels={},
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css", "admin-shared.css", "admin-table.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "admin-table.js"), type="module")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_admin_table_list_has_contained_scroll_and_touch_targets(
    viewport: dict[str, int],
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render_admin_table())

            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )

            action_button = page.locator(".admin-row-actions .button-small").first
            box = await action_button.bounding_box()
            assert box is not None
            assert box["width"] >= 44
            assert box["height"] >= 44

            badge_ok = page.locator(".badge-ok").first
            assert await badge_ok.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) != "rgba(0, 0, 0, 0)"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_table_pagination_links_are_encoded() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_admin_table(page_num=2, total=120))
            back_link = page.get_by_role("link", name="← Назад")
            forward_link = page.get_by_role("link", name="Вперёд →")
            assert await back_link.get_attribute("href") == "/app/admin/table/users?page=1"
            assert await forward_link.get_attribute("href") == "/app/admin/table/users?page=2"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_table_delete_dialog_names_record_and_restores_focus() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_admin_table())
            dialog = page.locator("#delete-dialog")
            triggers = page.locator("[data-delete-url]")

            first_trigger = triggers.nth(0)
            await first_trigger.click()
            assert await dialog.is_visible()
            assert "telegram_user_id=100" in (await dialog.locator("[data-delete-target]").inner_text())
            await page.keyboard.press("Escape")
            assert await dialog.is_hidden()
            assert await first_trigger.evaluate("element => element === document.activeElement")

            second_trigger = triggers.nth(1)
            await second_trigger.click()
            assert "telegram_user_id=101" in (await dialog.locator("[data-delete-target]").inner_text())
            await dialog.locator("[data-delete-cancel]").click()
            assert await dialog.is_hidden()
            assert await second_trigger.evaluate("element => element === document.activeElement")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_edit_shows_destructive_warning_and_guards_submit() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page, _render_admin_edit())

            assert await page.get_by_text("Прямое изменение записи в базе данных").is_visible()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )

            form = page.locator("[data-admin-edit-form]")
            await form.evaluate(
                "form => form.addEventListener('submit', event => event.preventDefault())"
            )
            submit = page.locator("[data-admin-edit-submit]")
            await submit.click()
            assert await submit.is_disabled()
            assert await submit.inner_text() == "Сохраняем…"
        finally:
            await browser.close()
