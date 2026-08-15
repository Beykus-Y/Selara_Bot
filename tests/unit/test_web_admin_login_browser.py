from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"


def _render_admin_login(*, error: str | None = None, flash: str | None = None) -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    return environment.get_template("admin_login.html").render(
        page_title="Selara admin login fixture",
        page_name="admin_login",
        top_links=[{"href": "/", "label": "На главную", "variant": "ghost"}],
        show_logout=False,
        flash=flash,
        error=error,
        body_classes="ui-admin-login",
        navigation_label="Навигация страницы входа",
        extra_scripts=["admin-login.js"],
    )


async def _mount(page, html: str) -> None:
    await page.set_content(html)
    for stylesheet in ("panel.css", "server-ui-foundation.css"):
        await page.add_style_tag(path=str(STATIC_DIR / stylesheet))
    await page.add_script_tag(path=str(STATIC_DIR / "admin-login.js"), type="module")
    await page.wait_for_timeout(80)


def _rects_overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["width"] and a["x"] + a["width"] > b["x"] and a["y"] < b["y"] + b["height"] and a["y"] + a["height"] > b["y"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
        {"width": 820, "height": 1180},
    ],
)
async def test_admin_login_hero_copy_and_side_cards_never_overlap(
    viewport: dict[str, int],
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport=viewport)
        try:
            await _mount(page, _render_admin_login())

            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )

            copy_box = await page.locator(".login-copy").bounding_box()
            side_box = await page.locator(".login-side").bounding_box()
            assert copy_box is not None
            assert side_box is not None
            assert not _rects_overlap(copy_box, side_box)

            cards = page.locator(".login-side-card")
            assert await cards.count() == 2
            first_box = await cards.nth(0).bounding_box()
            second_box = await cards.nth(1).bounding_box()
            assert first_box is not None
            assert second_box is not None
            assert not _rects_overlap(first_box, second_box)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_login_shows_error_banner() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        try:
            await _mount(page, _render_admin_login(error="Неверный пароль."))
            banner = page.locator('[role="alert"]')
            assert await banner.is_visible()
            assert "Неверный пароль." in await banner.inner_text()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_login_password_field_is_autofocused_and_guards_double_submit() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_admin_login())

            password_field = page.locator('input[name="password"]')
            assert await password_field.evaluate("element => element === document.activeElement")

            form = page.locator("form")
            await form.evaluate(
                "form => form.addEventListener('submit', event => event.preventDefault())"
            )
            await password_field.fill("test-password")
            submit = page.locator("[data-admin-login-submit]")
            await submit.click()
            assert await submit.is_disabled()
            assert await submit.inner_text() == "Проверяем…"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_admin_login_does_not_disclose_env_var_name() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            await _mount(page, _render_admin_login())
            assert "ADMIN_PASSWORD" not in await page.content()
        finally:
            await browser.close()
