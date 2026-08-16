from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _render_login(*, error: str | None = None, flash: str | None = None) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("login.html").render(
        page_title="Selara login fixture",
        page_name="login",
        top_links=[{"href": "/", "label": "Главная", "variant": "ghost"}],
        show_logout=False,
        flash=flash,
        error=error,
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        bot_username="selara_test_bot",
        bot_dm_url="https://t.me/selara_test_bot",
        body_classes="ui-login",
        navigation_label="Навигация страницы входа",
        extra_scripts=["login-form.js"],
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "login-form.js"), type="module")
    await page.wait_for_timeout(80)


@pytest.mark.asyncio
async def test_login_shows_error_banner() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page, _render_login(error="Код не найден, уже использован или истёк. Запросите новый через /login у бота."))
            banner = page.locator('[role="alert"]')
            assert await banner.is_visible()
            assert "истёк" in await banner.inner_text()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_login_code_field_is_autofocused_and_guards_double_submit() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_login())

            code_field = page.locator('input[name="code"]')
            assert await code_field.evaluate("element => element === document.activeElement")

            form = page.locator("form")
            await form.evaluate(
                "form => form.addEventListener('submit', event => event.preventDefault())"
            )
            await code_field.fill("123456")
            submit = page.locator("[data-login-submit]")
            await submit.click()
            assert await submit.is_disabled()
            assert await submit.inner_text() == "Проверяем…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_login_code_field_has_mobile_friendly_keyboard_attributes() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page, _render_login())
            code_field = page.locator('input[name="code"]')
            assert await code_field.get_attribute("inputmode") == "numeric"
            assert await code_field.get_attribute("autocomplete") == "one-time-code"
            assert await code_field.get_attribute("maxlength") == "6"
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
async def test_login_page_has_no_horizontal_overflow(viewport: dict[str, int]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render_login())
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
        finally:
            await browser.close()
