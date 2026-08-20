"""Mobile-accessibility guard for the getting-started onboarding page (see
docs/DOCUMENTATION_AUDIT_TODO.md "P1 -- Discoverability / Onboarding"): the
navigation into the full docs deliberately wraps to multiple rows instead of
using a horizontal-scroll strip, so there is no scroll-strip to make
accessible -- but that only holds if it actually never overflows the
viewport, which this proves against a real rendered/laid-out page rather
than by reading the CSS.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from selara.web.getting_started import build_getting_started_context
from selara.web.rendering import create_template_environment

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "src/selara/web/static"

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}
TABLET = {"width": 820, "height": 1180}
VIEWPORTS = (DESKTOP, TABLET, MOBILE)


def _render_getting_started() -> str:
    environment = create_template_environment(template_dir=TEMPLATE_DIR)
    context = build_getting_started_context()
    return environment.get_template("getting_started.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        body_classes="",
        navigation_label="Основная навигация",
        **context,
    )


async def _load(page) -> None:
    await page.set_content(_render_getting_started())
    await page.add_style_tag(path=str(STATIC_DIR / "panel.css"))
    await page.add_style_tag(path=str(STATIC_DIR / "server-ui-foundation.css"))
    await page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")


@pytest.mark.asyncio
async def test_getting_started_page_has_no_horizontal_overflow_at_any_viewport() -> None:
    results: dict[int, bool] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for viewport in VIEWPORTS:
                page = await browser.new_page(viewport=viewport)
                await _load(page)
                results[viewport["width"]] = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                await page.close()
        finally:
            await browser.close()

    overflowing = {width: value for width, value in results.items() if value}
    assert not overflowing, f"horizontal overflow at viewport widths: {sorted(overflowing)}"


@pytest.mark.asyncio
async def test_getting_started_nav_links_are_comfortably_tappable_on_mobile() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport=MOBILE)
            await _load(page)
            links = page.locator(".onboarding-nav-link")
            count = await links.count()
            assert count > 0
            for index in range(count):
                box = await links.nth(index).bounding_box()
                assert box is not None
                assert box["height"] >= 40, f"nav link {index} is shorter than a 40px tap target"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_getting_started_page_screenshots_desktop_and_mobile(tmp_path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            desktop_page = await browser.new_page(viewport=DESKTOP)
            await _load(desktop_page)
            await desktop_page.screenshot(path=str(tmp_path / "getting-started-desktop.png"), full_page=True)

            mobile_page = await browser.new_page(viewport=MOBILE)
            await _load(mobile_page)
            await mobile_page.screenshot(path=str(tmp_path / "getting-started-mobile.png"), full_page=True)
        finally:
            await browser.close()

    assert (tmp_path / "getting-started-desktop.png").exists()
    assert (tmp_path / "getting-started-mobile.png").exists()
