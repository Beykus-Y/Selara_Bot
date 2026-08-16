from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _audit_row(
    *,
    event_id: int,
    action: str,
    action_label: str,
    category: str,
    category_label: str,
    tone: str,
    actor: str,
    target: str = "—",
    long: bool = False,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "when": "15.08.2026 10:30 UTC",
        "time_label": "10:30",
        "action": action,
        "action_label": action_label,
        "category_code": category,
        "category_label": category_label,
        "tone": tone,
        "description": (
            "VeryLongAuditDescriptionWithoutAnySpaces1234567890改善履歴表示مرحباً🚀"
            if long
            else "Настройка режима экономики изменена: global → local."
        ),
        "actor": actor,
        "actor_label": "Система" if actor == "system" else f"Пользователь {actor}",
        "target": target,
        "target_label": "Без цели" if target == "—" else f"Пользователь {target}",
        "has_target": target != "—",
    }


def _render_audit(
    *,
    empty: bool = False,
    filtered: bool = False,
    load_error: str | None = None,
    total_count: int | None = None,
) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    all_rows = [
        _audit_row(
            event_id=44,
            action="web_setting_updated",
            action_label="Изменена настройка",
            category="settings",
            category_label="Настройки",
            tone="info",
            actor="77",
        ),
        _audit_row(
            event_id=43,
            action="captcha_failed_kick",
            action_label="Удалён после капчи",
            category="moderation",
            category_label="Модерация",
            tone="danger",
            actor="system",
            target="404",
        ),
        _audit_row(
            event_id=42,
            action="coins_transfer",
            action_label="Перевод монет",
            category="economy",
            category_label="Экономика",
            tone="success",
            actor="88",
            target="99",
        ),
        _audit_row(
            event_id=41,
            action="VeryLongAuditActionCodeWithoutSpaces1234567890",
            action_label="Другое событие",
            category="other",
            category_label="Другое",
            tone="neutral",
            actor="999999999999999999",
            long=True,
        ),
    ]
    rows = [] if empty else all_rows[1:2] if filtered else all_rows
    groups = [] if empty else [
        {"date_label": "Сегодня, 15 августа", "rows": rows[:2]},
        {"date_label": "Вчера, 14 августа", "rows": rows[2:]},
    ]
    groups = [group for group in groups if group["rows"]]
    return environment.get_template("audit.html").render(
        page_title="Selara audit fixture",
        page_name="audit",
        top_links=[],
        show_logout=True,
        flash=None,
        error=None,
        extra_styles=["audit.css"],
        chat_title="Selara Hub — очень длинное название чата 🚀",
        chat_id=-1001001001001,
        chat_section_links=[
            {"href": "/app/chat/-1001", "label": "Обзор", "variant": "ghost"},
            {"href": "/app/chat/-1001/audit", "label": "Журнал", "variant": "primary"},
        ],
        audit_total_count=(
            total_count if total_count is not None else (0 if empty else len(all_rows))
        ),
        audit_shown_count=len(rows),
        audit_system_count=0 if empty else 1,
        audit_filters={
            "q": "капча" if filtered else "",
            "category": "moderation" if filtered else "all",
            "actor": "all",
        },
        audit_filter_errors=[],
        audit_load_error=load_error,
        audit_category_options=[
            {"value": "all", "label": "Все категории"},
            {"value": "settings", "label": "Настройки"},
            {"value": "moderation", "label": "Модерация"},
            {"value": "economy", "label": "Экономика"},
        ],
        audit_actor_options=[
            {"value": "all", "label": "Все инициаторы"},
            {"value": "users", "label": "Пользователи"},
            {"value": "system", "label": "Система"},
        ],
        audit_reset_href="/app/chat/-1001/audit",
        audit_groups=groups,
    )


async def _mount(
    page,
    *,
    empty: bool = False,
    filtered: bool = False,
    load_error: str | None = None,
    total_count: int | None = None,
) -> None:
    await page.set_content(
        _render_audit(
            empty=empty,
            filtered=filtered,
            load_error=load_error,
            total_count=total_count,
        )
    )
    for stylesheet in ("panel.css", "server-ui-foundation.css", "audit.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_chat_audit_timeline_is_readable_and_responsive(
    viewport: dict[str, int],
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page)
            audit = page.locator("[data-audit-page]")
            assert await audit.locator(".audit-hero h1").evaluate(
                "(element, minWidth) => element.getBoundingClientRect().width >= minWidth",
                min(240, viewport["width"] * 0.55),
            )
            assert await audit.locator("[data-audit-event]").count() == 4
            assert await audit.locator(".audit-date-group").count() == 2
            assert await audit.locator(".audit-event").last.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert await audit.locator(".audit-filter-action").evaluate(
                "element => element.getBoundingClientRect().height"
            ) >= 44

            details = audit.locator(".audit-technical-details").first
            await details.locator("summary").click()
            assert await details.get_by_text("web_setting_updated").is_visible()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_chat_audit_load_error_never_claims_the_log_is_empty() -> None:
    """A failed query must not be presented as "no events yet".

    The audit log is a security surface: when `list_audit_logs` raises, the
    page previously kept the generic empty state ("Событий пока нет. Новые
    изменения и действия бота появятся здесь автоматически"), which tells an
    admin the chat is quiet when in truth nothing is known.
    """
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(
                page,
                empty=True,
                load_error="Не удалось загрузить журнал событий. Попробуйте обновить страницу.",
            )
            audit = page.locator("[data-audit-page]")
            assert await audit.get_by_role("alert").first.is_visible()

            body = await audit.inner_text()
            assert "Событий пока нет" not in body
            assert "появятся здесь автоматически" not in body
            # ...and it must offer a way to try again.
            assert await audit.get_by_role("link", name="Обновить страницу").is_visible()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_chat_audit_empty_log_without_filters_hides_the_reset_action() -> None:
    """"Сбросить фильтры" with no active filters is a dead control."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page, empty=True)
            audit = page.locator("[data-audit-page]")
            assert await audit.get_by_text("Событий пока нет").is_visible()
            assert await audit.get_by_role("link", name="Сбросить фильтры").count() == 0
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_chat_audit_filtered_empty_state_offers_a_reset() -> None:
    """Events exist but the filters match none — resetting is the real fix."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page, empty=True, filtered=True, total_count=4)
            audit = page.locator("[data-audit-page]")
            assert await audit.get_by_text("По фильтрам ничего не найдено").is_visible()
            assert await audit.get_by_role("link", name="Сбросить фильтры").first.is_visible()
        finally:
            await browser.close()
