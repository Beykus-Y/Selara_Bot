from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _render_feedback(*, error: str | None = None, flash: str | None = None, feedback_items=None) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("feedback.html").render(
        page_title="Selara • Обратная связь",
        page_name="feedback",
        top_links=[{"href": "/", "label": "Главная", "variant": "ghost"}],
        show_logout=True,
        flash=flash,
        error=error,
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        hero_title="Обратная связь по боту",
        hero_subtitle="Оставьте идеи, чего не хватает в боте.",
        feedback_metrics=[
            {"label": "Всего заявок", "value": "2", "note": "из этого аккаунта", "tone": "indigo"},
            {"label": "Не сделано", "value": "1", "note": "ещё в работе", "tone": "magenta"},
            {"label": "Сделано", "value": "1", "note": "отмечено в админке", "tone": "cyan"},
        ],
        feedback_items=feedback_items
        if feedback_items is not None
        else [
            {
                "title": "Напоминания по расписанию",
                "details": "Хочу настраиваемые напоминания по крону.",
                "created_at": "16.08.2026 09:00",
                "status_code": "open",
                "status_label": "В работе",
                "status_note": "Ожидает рассмотрения.",
            },
            {
                "title": "Тёмная тема",
                "details": "Добавьте переключатель темы.",
                "created_at": "10.08.2026 12:00",
                "status_code": "done",
                "status_label": "Сделано",
                "status_note": "Отмечено как сделанная.",
            },
        ],
        extra_scripts=["feedback-form.js"],
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css", "admin-shared.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "feedback-form.js"), type="module")
    await page.wait_for_timeout(80)


@pytest.mark.asyncio
async def test_feedback_form_fields_have_length_limits() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_feedback())
            title_field = page.locator('input[name="title"]')
            details_field = page.locator('textarea[name="details"]')
            assert await title_field.get_attribute("maxlength") == "160"
            assert await details_field.get_attribute("maxlength") == "4000"
            assert await title_field.get_attribute("required") is not None
            assert await details_field.get_attribute("required") is not None
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_feedback_form_guards_double_submit() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_feedback())
            form = page.locator("[data-feedback-form]")
            await form.evaluate(
                "form => form.addEventListener('submit', event => event.preventDefault())"
            )
            await page.fill('input[name="title"]', "Тестовая идея")
            await page.fill('textarea[name="details"]', "Описание тестовой идеи.")
            submit = page.locator("[data-feedback-submit]")
            await submit.click()
            assert await submit.is_disabled()
            assert await submit.inner_text() == "Отправляем…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_feedback_page_shows_error_and_success_banners() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            error_page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await _mount(error_page, _render_feedback(error="Нужно коротко назвать идею."))
            banner = error_page.locator('[role="alert"]')
            assert await banner.is_visible()
            assert "Нужно коротко назвать идею." in await banner.inner_text()
            await error_page.close()

            flash_page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await _mount(flash_page, _render_feedback(flash="Предложение отправлено."))
            banner = flash_page.locator('[role="status"]')
            assert await banner.is_visible()
            assert "Предложение отправлено." in await banner.inner_text()
            await flash_page.close()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_feedback_page_shows_empty_state_when_no_items() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_feedback(feedback_items=[]))
            assert "Здесь пока пусто" in await page.content()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_feedback_page_has_no_horizontal_overflow(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render_feedback())
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
